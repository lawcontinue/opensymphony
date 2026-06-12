"""LLM Router — model selection with fallback chain."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# SSL context that doesn't verify self-signed certs (for restricted networks)
def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# Default routing table: task_type → ordered list of (provider, model)
# 熔炉#62 consensus: Mimo-Pro 综合 T0, GLM 结构化 T0, DeepSeek 速度 T0, M3 指令密集 T0
DEFAULT_ROUTING: dict[str, list[tuple[str, str]]] = {
    # 通用 Agent 日常: Mimo-Pro T0 → GLM → DeepSeek-Pro
    "chat": [("mimo", "mimo-v2.5-pro"), ("glm", "glm-5.1"), ("deepseek", "deepseek-v4-pro")],
    # 速度优先场景: DeepSeek-Pro T0 → Mimo-Pro
    "speed_critical": [("deepseek", "deepseek-v4-pro"), ("mimo", "mimo-v2.5-pro")],
    # 代码生成: Mimo-Pro T0 → DeepSeek-Pro
    "code_generation": [("mimo", "mimo-v2.5-pro"), ("deepseek", "deepseek-v4-pro")],
    # 创意写作: Mimo-Pro → DeepSeek-Pro
    "creative_writing": [("mimo", "mimo-v2.5-pro"), ("deepseek", "deepseek-v4-pro")],
    # 深度分析: Mimo-Pro T0 → DeepSeek-Pro → GLM
    "deep_analysis": [("mimo", "mimo-v2.5-pro"), ("deepseek", "deepseek-v4-pro"), ("glm", "glm-5.1")],
    # 结构化输出: GLM T0 → Mimo-Pro
    "structured_output": [("glm", "glm-5.1"), ("mimo", "mimo-v2.5-pro")],
    # 工具生成/FC: Mimo-Pro → DeepSeek-Pro
    "tool_generation": [("mimo", "mimo-v2.5-pro"), ("deepseek", "deepseek-v4-pro")],
    # 指令密集/批处理: M3 T0 → GLM（⚠️ Injection guard 必须启用）
    "instruction_heavy": [("minimax", "MiniMax-M3"), ("glm", "glm-5.1")],
    # Flash 日常（低成本快速，未经 benchmark 验证，仅用于非关键路径）
    "chat_flash": [("deepseek", "deepseek-v4-flash"), ("mimo", "mimo-v2.5")],
}

# R1-specific routing (MiniMax-M3 primary, local gemma fallback)
# 熔炉#62: M3 指令密集 T0，结构化需 guard
R1_ROUTING: dict[str, list[tuple[str, str]]] = {
    "chat": [("minimax", "MiniMax-M3"), ("local", "mlx-community/gemma-3-12b-it-qat-4bit")],
    "speed_critical": [("minimax", "MiniMax-M3")],
    "code_generation": [("minimax", "MiniMax-M3"), ("local", "mlx-community/gemma-3-12b-it-qat-4bit")],
    "creative_writing": [("minimax", "MiniMax-M3")],
    "deep_analysis": [("minimax", "MiniMax-M3"), ("local", "mlx-community/gemma-3-12b-it-qat-4bit")],
    "tool_generation": [("minimax", "MiniMax-M3")],
    "structured_output": [("minimax", "MiniMax-M3")],  # M3 结构化 43%，需后验证
    "instruction_heavy": [("minimax", "MiniMax-M3")],  # T0: 指令遵循 100%
    "chat_flash": [("minimax", "MiniMax-M3")],
}


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    tool_calls: list[dict] | None = None  # OpenAI-format tool_calls when present


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
        # Extract tool_calls passed through usage dict
        tool_calls = usage.pop("_tool_calls", None)
        if self._telemetry:
            self._telemetry.record_llm(
                model=model, provider=provider_name, latency_ms=latency,
                soul_id=kwargs.get("soul_id", ""), task_type=kwargs.get("task_type", ""),
                tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0),
                success=True, response_preview=content[:200],
            )
        return LLMResponse(content=content, model=model, provider=provider_name, usage=usage, latency_ms=latency, tool_calls=tool_calls)


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

        # Function calling: add tools to request body
        tools = kwargs.get("tools")
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

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

        with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode())

        choice = data["choices"][0]["message"]
        content = choice.get("content") or ""
        # Fallback for thinking models where content is empty
        if not content and choice.get("reasoning_content"):
            content = choice["reasoning_content"]

        # Extract tool_calls from response (OpenAI format)
        raw_tool_calls = choice.get("tool_calls")

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
            if len(content) > 500 and not content.strip().startswith(("{", "[", "#", "-")):
                paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 50]
                if paragraphs:
                    content = paragraphs[-1]

        usage = data.get("usage", {})
        # Pass tool_calls through usage dict (extracted by LLMRouter._call)
        if raw_tool_calls:
            usage["_tool_calls"] = raw_tool_calls
        return content, usage


class AnthropicProvider(BaseProvider):
    """Provider for Anthropic Messages API (MiniMax, Claude, etc.)."""

    def __init__(self, base_url: str, api_key: str, name: str = "anthropic"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name

    def supports_model(self, model: str) -> bool:
        return True

    def chat(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float, **kwargs: Any,
    ) -> tuple[str, dict]:
        import json
        import urllib.request

        # Convert OpenAI messages → Anthropic format
        system_parts = []
        anthropic_msgs = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                anthropic_msgs.append({"role": "assistant", "content": content})
            else:
                anthropic_msgs.append({"role": "user", "content": content})

        body: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        # FC: add tools if present
        tools = kwargs.get("tools")
        if tools:
            # Convert OpenAI tools → Anthropic format
            body["tools"] = [
                {"name": t["function"]["name"],
                 "description": t["function"].get("description", ""),
                 "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}})}
                for t in tools
            ]

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())

        # Parse Anthropic response
        content = ""
        raw_tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                raw_tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
                })

        usage = data.get("usage", {})
        # Normalize usage keys
        usage = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        }
        if raw_tool_calls:
            usage["_tool_calls"] = raw_tool_calls
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


def create_router_from_env(routing_preset: str = "default") -> LLMRouter:
    """Create a router pre-configured from environment variables.
    
    routing_preset: "default" for R0/5060Ti, "r1" for R1 (MiniMax-primary)
    """
    if routing_preset == "r1":
        router = LLMRouter(routing=R1_ROUTING)
    else:
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

    # GLM / Zhipu (coding/token-plan endpoint)
    if api_key := os.environ.get("ZHIPU_API_KEY"):
        router.register_provider("zhipu", OpenAICompatibleProvider(
            base_url=os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4"),
            api_key=api_key, name="zhipu",
        ))

    # Local — only register if explicitly enabled or reachable
    local_url = os.environ.get("LOCAL_LLM_URL", "")
    if local_url:
        router.register_provider("local", LocalProvider(base_url=local_url))
    elif os.environ.get("ENABLE_LOCAL_LLM", "").lower() in ("1", "true", "yes"):
        router.register_provider("local", LocalProvider(base_url="http://localhost:8080"))
    # Otherwise skip: no noisy connection-refused errors when no local LLM

    # R1 — Remote Mac Mini (mlx_lm server, OpenAI-compatible)
    r1_url = os.environ.get("R1_LLM_URL", "")
    if r1_url:
        router.register_provider("r1", LocalProvider(base_url=r1_url))

    # MiniMax — OpenAI-compatible API
    if api_key := os.environ.get("MINIMAX_API_KEY"):
        router.register_provider("minimax", OpenAICompatibleProvider(
            base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
            api_key=api_key, name="minimax",
        ))

    return router
