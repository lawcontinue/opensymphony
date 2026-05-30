"""Tests for Agent tool-use (ReAct loop) + file tools."""


from opensymphony.agents.agent import Agent
from opensymphony.llm.router import LLMRouter
from opensymphony.tools.production import (
    PRODUCTION_TOOLS,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    ListDirTool,
    call_tool,
    register_all,
)

# ── Mock LLM Provider ─────────────────────────────────────────────

class MockProvider:
    """Returns canned responses in sequence."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self._idx = 0

    def supports_model(self, model):
        return True

    def chat(self, model, messages, max_tokens, temperature, **kwargs):
        if self._idx < len(self.responses):
            resp = self.responses[self._idx]
            self._idx += 1
        else:
            resp = "FINAL_ANSWER: done"
        return resp, {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}


def _make_router(responses):
    router = LLMRouter()
    provider = MockProvider(responses)
    router.register_provider("mock", provider)
    # Override routing to use mock
    router.routing = {k: [("mock", "mock-model")] for k in router.routing}
    return router


# ── File Tool Tests ────────────────────────────────────────────────

class TestFileTools:
    def test_file_write_read_roundtrip(self, tmp_path):
        fpath = str(tmp_path / "test.txt")
        write_tool = FileWriteTool()
        read_tool = FileReadTool()

        r = write_tool.execute({"path": fpath, "content": "Hello World"})
        assert r["success"] is True

        r = read_tool.execute({"path": fpath})
        assert r["success"] is True
        assert r["result"]["content"] == "Hello World"

    def test_file_read_not_found(self):
        r = FileReadTool().execute({"path": "/nonexistent/file.txt"})
        assert r["success"] is False
        assert "not found" in r["error"].lower()

    def test_file_write_creates_dirs(self, tmp_path):
        fpath = str(tmp_path / "a" / "b" / "c.txt")
        r = FileWriteTool().execute({"path": fpath, "content": "deep"})
        assert r["success"] is True
        assert FileReadTool().execute({"path": fpath})["result"]["content"] == "deep"

    def test_file_edit_replace(self, tmp_path):
        fpath = str(tmp_path / "edit.txt")
        FileWriteTool().execute({"path": fpath, "content": "foo bar baz"})
        r = FileEditTool().execute({"path": fpath, "old_text": "bar", "new_text": "QUX"})
        assert r["success"] is True
        content = FileReadTool().execute({"path": fpath})["result"]["content"]
        assert content == "foo QUX baz"

    def test_file_edit_old_not_found(self, tmp_path):
        fpath = str(tmp_path / "edit2.txt")
        FileWriteTool().execute({"path": fpath, "content": "hello"})
        r = FileEditTool().execute({"path": fpath, "old_text": "missing", "new_text": "x"})
        assert r["success"] is False

    def test_list_dir(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        r = ListDirTool().execute({"path": str(tmp_path)})
        assert r["success"] is True
        names = [e["name"] for e in r["result"]["entries"]]
        assert "a.txt" in names
        assert "sub" in names

    def test_list_dir_not_found(self):
        r = ListDirTool().execute({"path": "/nonexistent"})
        assert r["success"] is False


# ── Registration Tests ─────────────────────────────────────────────

class TestRegistration:
    def test_file_tools_registered(self):
        register_all()
        for name in ("file_read", "file_write", "file_edit", "list_dir"):
            assert name in PRODUCTION_TOOLS, f"{name} not in PRODUCTION_TOOLS"

    def test_call_file_write(self, tmp_path):
        register_all()
        fpath = str(tmp_path / "via_registry.txt")
        r = call_tool("file_write", {"path": fpath, "content": "registry works"})
        assert r["success"] is True

    def test_call_unknown_tool(self):
        register_all()
        r = call_tool("nonexistent", {})
        assert r["success"] is False


# ── Agent chat_with_tools Tests ────────────────────────────────────

class TestAgentToolUse:
    def test_parse_tool_call(self):
        text = 'Some thinking...\nTOOL_CALL: {"name": "file_read", "params": {"path": "/tmp/x.txt"}}'
        result = Agent._parse_tool_call(text)
        assert result is not None
        assert result["name"] == "file_read"
        assert result["params"]["path"] == "/tmp/x.txt"

    def test_parse_tool_call_none(self):
        assert Agent._parse_tool_call("no tool call here") is None

    def test_react_loop_with_tool(self, tmp_path):
        """Agent reads a file then gives final answer."""
        # Setup: write a file
        fpath = str(tmp_path / "data.txt")
        FileWriteTool().execute({"path": fpath, "content": "The answer is 42"})

        # Mock LLM: first call → tool call, second call → final answer
        router = _make_router([
            f'TOOL_CALL: {{"name": "file_read", "params": {{"path": "{fpath}"}}}}',
            "FINAL_ANSWER: Based on the file, the answer is 42",
        ])

        agent = Agent()
        agent.init(router=router)

        tools = {"file_read": FileReadTool()}
        result = agent.chat_with_tools("Read the file and tell me the answer", tools=tools)

        assert result["answer"] == "Based on the file, the answer is 42"
        assert result["tool_calls"] == 1
        assert result["iterations"] == 2
        assert len(result["steps"]) == 2

    def test_react_loop_no_tools_needed(self):
        """Agent answers directly without tools."""
        router = _make_router(["FINAL_ANSWER: The sky is blue"])
        agent = Agent()
        agent.init(router=router)
        result = agent.chat_with_tools("What color is the sky?", tools={})
        assert "blue" in result["answer"].lower()
        assert result["tool_calls"] == 0

    def test_react_loop_unknown_tool(self):
        """Agent tries non-existent tool, gets error."""
        router = _make_router([
            'TOOL_CALL: {"name": "nonexistent", "params": {}}',
            "FINAL_ANSWER: I couldn't find the tool",
        ])
        agent = Agent()
        agent.init(router=router)
        tools = {"file_read": FileReadTool()}  # doesn't have "nonexistent"
        result = agent.chat_with_tools("Try the tool", tools=tools)
        assert result["tool_calls"] == 0  # nonexistent tool not counted

    def test_react_loop_max_iterations(self):
        """Agent hits max iterations without final answer."""
        router = _make_router(["thinking..."] * 10)
        agent = Agent()
        agent.init(router=router)
        result = agent.chat_with_tools("Do something", tools={}, max_iterations=3)
        assert result["truncated"] is True
        assert result["iterations"] == 3

    def test_format_tool_schemas(self):
        schemas = Agent._format_tool_schemas({"file_read": FileReadTool(), "file_write": FileWriteTool()})
        assert "file_read" in schemas
        assert "file_write" in schemas
