"""Kernel — Symphony framework entry point and lifecycle management."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .agents.agent import Agent, AgentStatus
from .agents.soul import Soul, load_souls_dir
from .echo import EchoEngine
from .event_bus import EventBus
from .governance.defense import DefenseLayer
from .governance.hitl import HITLManager
from .governance.middleware import GovernanceMiddleware
from .governance.precedent import PrecedentStore
from .governance.voting import VotingMechanism
from .llm.router import LLMRouter, create_router_from_env
from .memory.l2 import L2Memory
from .memory.l3 import L3Memory
from .runtime.pool import AgentPool
from .runtime.sandbox import AgentSandbox
from .runtime.scheduler import TaskScheduler
from .session import Session
from .skill_registry import SkillRegistry
from .telemetry import Telemetry
from .tools.production import PRODUCTION_TOOLS, call_tool
from .tools.production import register_all as register_production_tools
from .tools.workshop import ToolWorkshop

logger = logging.getLogger("symphony")


def _load_dotenv():
    """Load .env file from project root."""
    candidates = [
        Path(__file__).parent.parent / ".env",
        Path(".env"),
        Path(".env"),
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            break


class SymphonyKernel:
    """The kernel — starts, configures, and manages the Symphony framework."""

    def __init__(
        self,
        souls_dir: Path | str | None = None,
        router: LLMRouter | None = None,
        config: dict[str, Any] | None = None,
        data_dir: Path | str | None = None,
    ):
        self.config = config or {}
        self.router = router or create_router_from_env()
        self.souls_dir = Path(souls_dir) if souls_dir else None
        self.data_dir = Path(data_dir) if data_dir else Path("data")

        self._souls: dict[str, Soul] = {}
        self._agents: dict[str, Agent] = {}
        self._event_bus = EventBus()
        self._telemetry = Telemetry(self.data_dir / "telemetry.db")
        self.router.set_telemetry(self._telemetry)
        self._skill_registry = SkillRegistry(self.data_dir / "skills.db")
        self._echo_engine = EchoEngine(self._skill_registry)
        self._l2: L2Memory | None = None
        self._l3: L3Memory | None = None
        self._governance: GovernanceMiddleware | None = None
        self._pool = AgentPool()
        self._scheduler = TaskScheduler()
        self._sandbox = AgentSandbox()
        self._workshop: ToolWorkshop | None = None
        self._running = False

        # v0.3: Human-facing components
        self._intent_bridge: Any | None = None
        self._human_adapter: Any | None = None

        # P0-1 fix: System-level context injected into every agent
        self._system_context: str = ""

    def start(self) -> None:
        _load_dotenv()
        logger.info("Symphony kernel starting...")
        if self.souls_dir and self.souls_dir.exists():
            self._souls = load_souls_dir(self.souls_dir)
            logger.info(f"Loaded {len(self._souls)} souls from {self.souls_dir}")

        # Init persistent storage
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._l2 = L2Memory(self.data_dir / "experiences.db")
        self._l3 = L3Memory(self.data_dir / "audit")

        # Init governance
        defense = DefenseLayer()
        voting = VotingMechanism()
        precedents = PrecedentStore(self.data_dir / "precedents.db")
        hitl = HITLManager()
        self._governance = GovernanceMiddleware(defense, voting, precedents, hitl)

        # Init tool workshop
        self._workshop = ToolWorkshop(self.data_dir / "tool_workshop")

        # Register production tools
        registered = register_production_tools()
        logger.info(f"Registered {len(registered)} production tools: {list(registered.keys())}")

        providers = list(self.router._providers.keys())
        logger.info(f"LLM providers: {providers}")
        # Build system context (P0-1: agent self-knowledge)
        tool_names = list(PRODUCTION_TOOLS.keys())
        soul_names = [s.name for s in self._souls.values()]
        self._system_context = (
            "你是 Symphony Framework 的 Agent。"
            "Symphony 是双功能 Agent 框架（Agent 协作 + 人类对话），采用洋葱架构。\n"
            "核心层：\n"
            "- 治理层（Governance）：投票、先例匹配、防御、人机交互——所有操作必经治理\n"
            "- Soul 引擎：YAML 定义人格，agent/human 双输出模式\n"
            "- 运行时（Runtime）：Agent 池、任务调度、沙箱\n"
            "- 内核（Kernel）：Soul 编译器、LLM 路由、三层记忆、工具工坊\n"
            f"- 当前可用工具：{', '.join(tool_names) if tool_names else '无'}\n"
            f"- 当前已加载 Soul：{', '.join(soul_names) if soul_names else '无'}\n"
            "当用户问关于 Symphony 的问题时，基于以上信息回答。"
        )

        self._running = True
        logger.info("Symphony kernel ready")

    def stop(self) -> None:
        logger.info("Symphony kernel stopping...")
        for agent in self._agents.values():
            agent.status = AgentStatus.TERMINATED
        if self._l2:
            self._l2.close()
        if self._governance and self._governance.precedents:
            self._governance.precedents.close()
        self._running = False
        logger.info("Symphony kernel stopped")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    def create_agent(self, soul_id: str | None = None, **kwargs: Any) -> Agent:
        soul = self._souls.get(soul_id) if soul_id else None
        session = Session()
        agent = Agent(soul=soul, **kwargs)
        agent.init(router=self.router, session=session, event_bus=self._event_bus)
        agent._echo_engine = self._echo_engine  # Attach Echo Engine

        # P0-1: Inject system context into agent's system prompt
        if self._system_context:
            agent._system_prompt = self._system_context + "\n\n" + (agent._system_prompt or "")

        # Attach persistent storage to session
        if self._l2 and self._l3:
            agent._session.attach_storage(self._l2, self._l3)

        # Add to pool
        self._pool.add(agent)
        self._agents[agent.id] = agent
        logger.info(f"Created agent {agent.id} (soul={soul_id or 'none'})")
        return agent

    async def handle_human_message(
        self,
        user_id: str,
        message: str,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        """Handle a message from a human user via HumanAdapter.

        Uses IntentBridge for parsing and Soul human mode for response.
        """
        if not self._human_adapter:
            from .gateway.human_adapter import HumanAdapter
            if not self._intent_bridge:
                try:
                    from .intent_bridge import IntentBridge
                    self._intent_bridge = IntentBridge()
                except Exception:
                    pass
            self._human_adapter = HumanAdapter(self, self._intent_bridge)
        return await self._human_adapter.handle_message(user_id, message, target_agent)

    def get_agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._agents.values()]

    def list_souls(self) -> list[dict[str, str]]:
        return [{"id": s.id, "name": s.name, "archetype": s.archetype} for s in self._souls.values()]

    def health(self) -> dict[str, Any]:
        return {
            "status": "running" if self._running else "stopped",
            "version": "0.1.0",
            "souls_loaded": len(self._souls),
            "agents_active": sum(1 for a in self._agents.values() if a.status in (AgentStatus.ACTIVE, AgentStatus.IDLE)),
            "agents_total": len(self._agents),
            "providers": list(self.router._providers.keys()),
            "event_bus_subscribers": len(self._event_bus._subscribers),
            "l2_experiences": self._l2.count() if self._l2 else 0,
            "governance": self._governance.health() if self._governance else {},
            "pool": self._pool.stats().__dict__,
            "scheduler": self._scheduler.stats(),
            "workshop_tools": len(self._workshop.list_tools()) if self._workshop else 0,
            "production_tools": list(PRODUCTION_TOOLS.keys()),
        }

    def execute_tool(self, name: str, params: dict) -> dict:
        """Execute a production tool by name."""
        return call_tool(name, params)

    def list_tools(self) -> list[dict]:
        """List all registered production tools with health status."""
        from .tools.production import list_tools as _list_tools
        return _list_tools()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Symphony Framework")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--souls-dir", type=Path, default=Path("souls"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    kernel = SymphonyKernel(souls_dir=args.souls_dir, data_dir=args.data_dir)
    kernel.start()

    try:
        import uvicorn

        from .gateway.http import create_app
        app = create_app(kernel)
        uvicorn.run(app, host=args.host, port=args.port)
    except ImportError:
        logger.warning("FastAPI/uvicorn not installed. Running without HTTP gateway.")
        logger.info(f"Kernel health: {kernel.health()}")
        kernel.stop()


if __name__ == "__main__":
    main()
