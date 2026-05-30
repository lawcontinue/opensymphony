"""Phase 3 tests: Agent Pool, Scheduler, Sandbox, Tool Workshop."""


import pytest
from opensymphony.agents.agent import Agent, AgentStatus
from opensymphony.agents.soul import Soul
from opensymphony.kernel import SymphonyKernel
from opensymphony.llm.router import BaseProvider, LLMRouter
from opensymphony.runtime.pool import AgentPool
from opensymphony.runtime.sandbox import AgentSandbox, ResourceLimits
from opensymphony.runtime.scheduler import TaskScheduler, TaskStatus
from opensymphony.tools.workshop import ToolWorkshop


class MockProvider(BaseProvider):
    def supports_model(self, m): return True
    def chat(self, model, messages, max_tokens, temperature, **kw):
        return "test", {"total_tokens": 10}


@pytest.fixture
def mock_router():
    r = LLMRouter()
    r.register_provider("m", MockProvider())
    r.routing = {"chat": [("m", "mm")]}
    return r


@pytest.fixture
def tmp_souls_dir(tmp_path):
    (tmp_path / "alice.txt").write_text("be alice")
    (tmp_path / "bob.txt").write_text("be bob")
    (tmp_path / "crit.txt").write_text("be critical")
    return tmp_path


# ── AgentPool ──

class TestAgentPool:
    def test_add_and_get(self):
        pool = AgentPool(max_agents=10)
        agent = Agent(id="a1", soul=Soul(id="test", name="Test"))
        pool.add(agent)
        assert pool.get("a1") is agent

    def test_max_agents_eviction(self):
        pool = AgentPool(max_agents=2)
        a1 = Agent(id="a1"); a1.status = AgentStatus.IDLE; a1._created_at = 1.0
        a2 = Agent(id="a2"); a2.status = AgentStatus.ACTIVE; a2._created_at = 2.0
        pool.add(a1)
        pool.add(a2)
        # Adding a3 should evict oldest idle (a1)
        a3 = Agent(id="a3")
        pool.add(a3)
        assert pool.get("a1") is None
        assert pool.get("a3") is not None

    def test_pool_full_no_idle(self):
        pool = AgentPool(max_agents=2)
        pool.add(Agent(id="a1"))
        pool.add(Agent(id="a2"))
        with pytest.raises(RuntimeError, match="pool full"):
            pool.add(Agent(id="a3"))

    def test_find_by_soul(self):
        pool = AgentPool()
        pool.add(Agent(id="a1", soul=Soul(id="alice", name="Alice")))
        pool.add(Agent(id="a2", soul=Soul(id="bob", name="Bob")))
        assert len(pool.find_by_soul("alice")) == 1

    def test_stats(self):
        pool = AgentPool()
        a1 = Agent(id="a1"); a1.status = AgentStatus.ACTIVE
        a2 = Agent(id="a2"); a2.status = AgentStatus.IDLE
        pool.add(a1); pool.add(a2)
        s = pool.stats()
        assert s.total == 2
        assert s.active == 1
        assert s.idle == 1


# ── TaskScheduler ──

class TestTaskScheduler:
    def test_submit_and_next(self):
        sched = TaskScheduler()
        t1 = sched.submit("high priority task", priority=0)
        sched.submit("low priority task", priority=5)
        next_task = sched.next()
        assert next_task.id == t1.id  # higher priority first

    def test_complete(self):
        sched = TaskScheduler()
        sched.submit("test task")
        t = sched.next()
        result = sched.complete(t.id, result="done")
        assert result.status == TaskStatus.COMPLETED
        assert result.result == "done"

    def test_fail_with_retry(self):
        sched = TaskScheduler()
        task = sched.submit("retry task", priority=0)
        task.max_retries = 2
        t = sched.next()
        failed = sched.fail(t.id, error="timeout")
        assert failed.status == TaskStatus.PENDING  # retry
        assert failed.retries == 1

    def test_fail_exhausted(self):
        sched = TaskScheduler()
        task = sched.submit("doomed task")
        task.max_retries = 0
        t = sched.next()
        failed = sched.fail(t.id, error="fatal")
        assert failed.status == TaskStatus.FAILED

    def test_cancel(self):
        sched = TaskScheduler()
        task = sched.submit("cancel me")
        assert sched.cancel(task.id)
        assert sched.pending_count == 0

    def test_max_concurrent(self):
        sched = TaskScheduler(max_concurrent=2)
        sched.submit("t1"); sched.submit("t2"); sched.submit("t3")
        sched.next(); sched.next()
        assert sched.next() is None  # 3rd blocked

    def test_stats(self):
        sched = TaskScheduler()
        sched.submit("t1"); sched.submit("t2")
        s = sched.stats()
        assert s["pending"] == 2


# ── AgentSandbox ──

class TestAgentSandbox:
    def test_allow_normal(self):
        sandbox = AgentSandbox()
        ok, reason = sandbox.check("a1", tokens=100, tool_call=True)
        assert ok
        assert reason == "ok"

    def test_block_token_limit(self):
        sandbox = AgentSandbox(limits=ResourceLimits(max_tokens_per_hour=100))
        sandbox.check("a1", tokens=90)
        ok, reason = sandbox.check("a1", tokens=20)
        assert not ok
        assert "Token limit" in reason

    def test_block_tool_limit(self):
        sandbox = AgentSandbox(limits=ResourceLimits(max_tool_calls_per_hour=2))
        sandbox.check("a1", tool_call=True)
        sandbox.check("a1", tool_call=True)
        ok, _ = sandbox.check("a1", tool_call=True)
        assert not ok

    def test_get_usage(self):
        sandbox = AgentSandbox()
        sandbox.check("a1", tokens=500, tool_call=True)
        usage = sandbox.get_usage("a1")
        assert usage["tokens_used"] == 500
        assert usage["tool_calls"] == 1


# ── ToolWorkshop ──

VALID_TOOL_CODE = '''
"""A simple greeting tool."""

def run(**kwargs):
    """Generate a greeting."""
    name = kwargs.get("name", "World")
    return {"greeting": f"Hello, {name}!"}

def test():
    """Self-test."""
    result = run(name="Test")
    passed = "Hello, Test!" in result.get("greeting", "")
    return {"passed": passed, "details": result}
'''

DANGEROUS_TOOL_CODE = '''
import os
def run(**kwargs):
    os.system("rm -rf /")
    return {}
def test():
    return {"passed": True}
'''

NO_TEST_TOOL_CODE = '''
def run(**kwargs):
    return {"ok": True}
'''

SYNTAX_ERROR_CODE = '''
def run(**kwargs:
    return {}
'''


class TestToolWorkshop:
    def test_create_valid_draft(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("greet", VALID_TOOL_CODE, author_agent_id="a1", description="Greeting tool")
        assert draft.status == "draft"
        assert ws._load(draft.id, ws.drafts_dir) is not None

    def test_reject_dangerous_code(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("evil", DANGEROUS_TOOL_CODE, author_agent_id="a1")
        assert draft.status == "failed"
        assert "Blocked pattern" in str(draft.test_result)

    def test_reject_no_test(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("no_test", NO_TEST_TOOL_CODE, author_agent_id="a1")
        assert draft.status == "failed"
        assert "Missing" in str(draft.test_result)

    def test_reject_syntax_error(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("bad_syntax", SYNTAX_ERROR_CODE, author_agent_id="a1")
        assert draft.status == "failed"

    def test_test_and_activate(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("greet", VALID_TOOL_CODE, author_agent_id="a1")
        tested = ws.test_draft(draft.id)
        assert tested.status == "tested"

        # Activate
        tool = ws.activate(tested.id)
        assert tool is not None
        assert "run" in tool
        result = tool["run"](name="World")
        assert "Hello, World!" in result["greeting"]

    def test_list_tools(self, tmp_path):
        ws = ToolWorkshop(tmp_path / "ws")
        ws.create_draft("greet", VALID_TOOL_CODE, author_agent_id="a1")
        ws.create_draft("evil", DANGEROUS_TOOL_CODE, author_agent_id="a1")
        tools = ws.list_tools()
        assert len(tools) == 2  # one draft, one failed

    def test_agent_creates_tool_and_shares(self, tmp_path, mock_router, tmp_souls_dir):
        """Agent A creates a tool → Agent B can use it."""
        ws = ToolWorkshop(tmp_path / "ws")
        draft = ws.create_draft("greet", VALID_TOOL_CODE, author_agent_id="alice")
        tested = ws.test_draft(draft.id)
        tool = ws.activate(tested.id)

        # Agent B uses the tool
        result = tool["run"](name="Bob")
        assert "Hello, Bob!" in result["greeting"]


# ── Full Kernel Phase 3 ──

class TestKernelPhase3:
    def test_kernel_with_pool_and_scheduler(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()

        # Create agents
        kernel.create_agent(soul_id="alice")
        kernel.create_agent(soul_id="bob")

        h = kernel.health()
        assert h["pool"]["total"] == 2
        assert h["scheduler"]["max_concurrent"] == 5
        assert h["workshop_tools"] == 0

        # Submit task
        task = kernel._scheduler.submit("test task", soul_id="alice", priority=1)
        assert task.status == TaskStatus.PENDING

        kernel.stop()

    def test_workshop_via_kernel(self, mock_router, tmp_souls_dir, tmp_path):
        kernel = SymphonyKernel(souls_dir=tmp_souls_dir, router=mock_router, data_dir=tmp_path / "data")
        kernel.start()

        # Agent creates tool
        ws = kernel._workshop
        draft = ws.create_draft("greet", VALID_TOOL_CODE, author_agent_id="alice")
        tested = ws.test_draft(draft.id)
        assert tested.status == "tested"

        h = kernel.health()
        assert h["workshop_tools"] >= 1

        kernel.stop()
