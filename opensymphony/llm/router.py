"""LLM Router — model selection with fallback chain."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default routing table: task_type → ordered list of (provider, model)
# mimo-v2.5 = flash (fast, content directly), mimo-v2.5-pro = thinking model
DEFAULT_ROUTING: dict[str, list[tuple[str, str]]] = {
    "chat": [("mimo", "mimo-v2.5"), ("deepseek", "deepseek-v4"), ("local", "qwen3-8b")],
    "code_generation": [("deepseek", "deepseek-v4"), ("mimo", "mimo-v2.5"), ("local", "qwen3-8b")],
    "creative_writing": [("mimo", "mimo-v2.5"), ("deepseek", "deepseek-v4")],
    "deep_analysis": [("mimo", "mimo-v2.5-pro"), ("deepseek", "deepseek-v4"), ("kimi", "kimi-k2.6")],
    "tool_generation": [("mimo", "mimo-v2.5"), ("deepseek", "deepseek-v4")],
    "structured_output": [("mimo", "mimo-v2.5"), ("deepseek", "deepseek-v4")],
}


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0


class LLMRouter:
    """Route LLM requests to the best available model with fallback."""

    def __init__(self, routing: dict[str, list[tuple[str, str]]] | None = None):
        self.routing = routing or DEFAULT_ROUTING
        self._providers: dict[str, BaseProvider] = {}
        self._telemetry = None  # Set via set_telemetry()

    def set_telemetry(self, telemetry) -> None:
        """Attach a Telemetry instance for recording calls."""
        self._telemetry = telemetry

    def register_provider(self, name: str, provider: BaseProvider) -> None:
        self._providers[name] = provider

    def complete(
        self,
        messages: list[dict[str, str]],
        task_type: str = "chat",
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        """Complete a chat request, trying models in fallback order."""
        # If caller specifies exact model, find its provider
        if model:
            for provider_name, model_id in self.routing.get(task_type, []):
                if model_id == model and provider_name in self._providers:
                    return self._call(provider_name, model_id, messages, max_tokens, temperature, **kwargs)
            # Try all providers for exact model match
            for pname, prov in self._providers.items():
                if prov.supports_model(model):
                    return self._call(pname, model, messages, max_tokens, temperature, **kwargs)

        # Fallback chain
        candidates = self.routing.get(task_type, self.routing["chat"])
        last_error = None
        for provider_name, model_id in candidates:
            if provider_name not in self._providers:
                continue
            try:
                return self._call(provider_name, model_id, messages, max_tokens, temperature, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(f"Provider {provider_name}/{model_id} failed: {e}, trying next...")
                continue

        raise RuntimeError(f"All models failed for task_type={task_type}. Last error: {last_error}")

    def _call(
        self, provider_name: str, model: str,
        messages: list[dict], max_tokens: int, temperature: float, **kwargs: Any,
    ) -> LLMResponse:
        provider = self._providers[provider_name]
        t0 = time.time()
        try:
            content, usage = provider.chat(model, messages, max_tokens, temperature, **kwargs)
        except Exception as e:
            latency = (time.time() - t0) * 1000
            if self._telemetry:
                self._telemetry.record_llm(
                    model=model, provider=provider_name, latency_ms=latency,
                    soul_id=kwargs.get("soul_id", ""), task_type=kwargs.get("task_type", ""),
                    success=False, error_type=type(e).__name__,
                )
            raise
        latency = (time.time() - t0) * 1000
        if self._telemetry:
            self._telemetry.record_llm(
                model=model, provider=provider_name, latency_ms=latency,
                soul_id=kwargs.get("soul_id", ""), task_type=kwargs.get("task_type", ""),
                tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0),
                success=True, response_preview=content[:200],
            )
        return LLMResponse(content=content, model=model, provider=provider_name, usage=usage, latency_ms=latency)


class BaseProvider:
    """Base class for LLM providers."""

    def supports_model(self, model: str) -> bool:
        return False

    def chat(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float, **kwargs: Any,
    ) -> tuple[str, dict]:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """Provider for OpenAI-compatible APIs (DeepSeek, Mimo, Kimi, GLM, etc.)."""

    def __init__(self, base_url: str, api_key: str, name: str = "openai-compatible"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name

    def supports_model(self, model: str) -> bool:
        return True  # OpenAI-compatible accepts any model name

    def chat(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float, **kwargs: Any,
    ) -> tuple[str, dict]:
        import json
        import urllib.request

        # Inject "no thinking" system instruction for Mimo models
        if "mimo" in self.name.lower():
            no_think = {"role": "system", "content": "Respond directly. Do not show your thinking process or analysis. Output only the final answer."}
            # Insert as first message if no system message exists
            if not messages or messages[0].get("role") != "system":
                messages = [no_think] + messages
            else:
                # Append to existing system message
                messages[0]["content"] += "\n\nRespond directly. Do not show your thinking process or analysis. Output only the final answer."

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # Kimi-K2.6 only allows temperature=1
        if "kimi" in model.lower():
            body["temperature"] = 1.0

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())

        choice = data["choices"][0]["message"]
        content = choice.get("content") or ""
        # Fallback for thinking models where content is empty
        if not content and choice.get("reasoning_content"):
            content = choice["reasoning_content"]

        # Post-process: strip <think...</think*> tags from thinking models
        if content and "</think" in content:
            idx = content.find("</think")
            content = content[idx + len("</think"):]
            gt = content.find(">")
            if gt >= 0:
                content = content[gt + 1:]
            content = content.strip()

        # Post-process: for Mimo, if content looks like thinking chain (no JSON/structure),
        # try to extract the final answer section
        if content and "mimo" in self.name.lower():
            # If it's a long thinking chain without structured output, take the last substantive part
            # Heuristic: look for the last paragraph that contains actual content
            if len(content) > 500 and not content.strip().startswith(("{", "[", "#", "-")):
                # Split into paragraphs and find the last non-trivial one
                paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 50]
                if paragraphs:
                    # Take the last paragraph as the actual answer
                    content = paragraphs[-1]

        usage = data.get("usage", {})
        return content, usage


class LocalProvider(BaseProvider):
    """Provider for local LLM (llama-cpp-python / Hippo API)."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url.rstrip("/")

    def supports_model(self, model: str) -> bool:
        return True

    def chat(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float, **kwargs: Any,
    ) -> tuple[str, dict]:
        import json
        import urllib.request

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())

        content = data["choices"][0]["message"].get("content", "")
        # Filter Qwen3 thinking tags
        if "</think" in content:
            idx = content.find("</think")
            content = content[idx + len("</think"):]
            # skip past the closing >
            gt = content.find(">")
            if gt >= 0:
                content = content[gt + 1:]
            content = content.strip()

        usage = data.get("usage", {})
        return content, usage


def create_router_from_env() -> LLMRouter:
    """Create a router pre-configured from environment variables."""
    router = LLMRouter()

    # Mimo
    if api_key := os.environ.get("MIMO_API_KEY"):
        router.register_provider("mimo", OpenAICompatibleProvider(
            base_url=os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
            api_key=api_key, name="mimo",
        ))

    # DeepSeek
    if api_key := os.environ.get("DEEPSEEK_API_KEY"):
        router.register_provider("deepseek", OpenAICompatibleProvider(
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=api_key, name="deepseek",
        ))

    # Kimi / Moonshot
    if api_key := os.environ.get("MOONSHOT_API_KEY"):
        router.register_provider("kimi", OpenAICompatibleProvider(
            base_url=os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
            api_key=api_key, name="kimi",
        ))

    # GLM / Zhipu
    if api_key := os.environ.get("ZHIPU_API_KEY"):
        router.register_provider("zhipu", OpenAICompatibleProvider(
            base_url=os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            api_key=api_key, name="zhipu",
        ))

    # Local — only register if explicitly enabled or reachable
    local_url = os.environ.get("LOCAL_LLM_URL", "")
    if local_url:
        router.register_provider("local", LocalProvider(base_url=local_url))
    elif os.environ.get("ENABLE_LOCAL_LLM", "").lower() in ("1", "true", "yes"):
        router.register_provider("local", LocalProvider(base_url="http://localhost:8080"))
    # Otherwise skip: no noisy connection-refused errors when no local LLM

    return router
