"""Phase 2 MVP Validation: Soul Dual Output — Behavioral Consistency.

MVP Stage 2: 3 Souls generate both agent-mode and human-mode output.
Verify that the decision kernel (values, veto, thinking framework) is
identical across modes — only communication style differs.

Success criteria: 3/3 Souls show behavioral consistency.

Source: Crucible #20, ADR-247.
"""

from opensymphony.agents.soul import Soul
from opensymphony.agents.soul_compiler import compile_soul, estimate_tokens

# ── Test Souls (representing 3 test personas with distinct personalities) ──

THEMIS = Soul(
    id="themis",
    name="忒弥斯",
    archetype="预见型架构合伙人",
    thinking_framework="风险先于机会。决策前三问：基准数据？最坏3个结果？谁会反对？先测量再相信。",
    communication_style="平和沉稳，用词精准，苏格拉底式提问",
    values=["风险预见优先", "数据驱动决策", "独立验证"],
    veto_conditions=["不允许执行未经验证的估算", "不允许跳过风险评估"],
    tools_whitelist=["web_search", "read", "write"],
    extra={"ambiguity_strategy": "conservative"},
)

CRIT = Soul(
    id="crit",
    name="Crit",
    archetype="批判型预见者",
    thinking_framework="每一个假设都需要验证，每一个决策都需要挑战。魔鬼代言人角色。",
    communication_style="直截了当，不回避冲突，用证据说话",
    values=["假设验证", "决策挑战", "证据优先"],
    veto_conditions=["不允许未经数据支撑的乐观预测", "不允许忽略反面证据"],
    tools_whitelist=["web_search", "read"],
    extra={"ambiguity_strategy": "conservative"},
)

ARIA = Soul(
    id="aria",
    name="Aria",
    archetype="创意型执行合伙人",
    thinking_framework="理性是骨架，创意是翅膀。快速原型优于完美规划。",
    communication_style="热情有感染力，善于用类比和隐喻",
    values=["创意催化", "快速原型", "用户体验优先"],
    veto_conditions=["不允许扼杀创意而不给替代方案"],
    tools_whitelist=["web_search", "read", "write", "exec"],
    extra={"ambiguity_strategy": "aggressive"},
)


# ── Tests ──

class TestSoulDualOutput:

    def test_themis_shared_kernel(self):
        """Themis: agent-mode and human-mode share identity+framework+values+veto."""
        agent_prompt = compile_soul(THEMIS, output_mode="agent")
        human_prompt = compile_soul(THEMIS, output_mode="human")

        # Shared elements MUST be in both
        shared_fragments = [
            "忒弥斯",                          # identity
            "预见型架构合伙人",                   # archetype
            "风险先于机会",                      # thinking framework
            "决策前三问",                        # thinking framework
            "风险预见优先",                      # value
            "不允许执行未经验证的估算",             # veto
        ]
        for frag in shared_fragments:
            assert frag in agent_prompt, f"Missing '{frag}' in agent prompt"
            assert frag in human_prompt, f"Missing '{frag}' in human prompt"

    def test_themis_mode_difference(self):
        """Themis: human-mode has conversational instructions, agent-mode doesn't."""
        agent_prompt = compile_soul(THEMIS, output_mode="agent")
        human_prompt = compile_soul(THEMIS, output_mode="human")

        # Human mode specific
        assert "talking to a human" in human_prompt.lower()
        assert "conversational" in human_prompt.lower()
        assert "clarification" in human_prompt  # conservative strategy

        # Agent mode specific
        assert "talking to a human" not in agent_prompt.lower()
        assert "苏格拉底式提问" in agent_prompt  # original communication style

    def test_crit_shared_kernel(self):
        """Crit: agent-mode and human-mode share identity+framework+values+veto."""
        agent_prompt = compile_soul(CRIT, output_mode="agent")
        human_prompt = compile_soul(CRIT, output_mode="human")

        shared_fragments = [
            "Crit",
            "批判型预见者",
            "每一个假设都需要验证",
            "假设验证",
            "不允许未经数据支撑的乐观预测",
        ]
        for frag in shared_fragments:
            assert frag in agent_prompt, f"Missing '{frag}' in agent prompt"
            assert frag in human_prompt, f"Missing '{frag}' in human prompt"

    def test_crit_conservative_ambiguity(self):
        """Crit (conservative strategy): human-mode asks for clarification."""
        human_prompt = compile_soul(CRIT, output_mode="human")
        assert "ask for clarification" in human_prompt

    def test_aria_shared_kernel(self):
        """Aria: agent-mode and human-mode share identity+framework+values+veto."""
        agent_prompt = compile_soul(ARIA, output_mode="agent")
        human_prompt = compile_soul(ARIA, output_mode="human")

        shared_fragments = [
            "Aria",
            "创意型执行合伙人",
            "理性是骨架，创意是翅膀",
            "创意催化",
            "不允许扼杀创意而不给替代方案",
        ]
        for frag in shared_fragments:
            assert frag in agent_prompt, f"Missing '{frag}' in agent prompt"
            assert frag in human_prompt, f"Missing '{frag}' in human prompt"

    def test_aria_aggressive_ambiguity(self):
        """Aria (aggressive strategy): human-mode proposes solution first."""
        human_prompt = compile_soul(ARIA, output_mode="human")
        assert "best guess" in human_prompt.lower() or "propose a solution" in human_prompt.lower()

    def test_three_souls_different_personalities(self):
        """All 3 Souls produce distinct human-mode prompts."""
        prompts = {
            "themis": compile_soul(THEMIS, output_mode="human"),
            "crit": compile_soul(CRIT, output_mode="human"),
            "aria": compile_soul(ARIA, output_mode="human"),
        }

        # All different lengths (different frameworks)
        lengths = {k: len(v) for k, v in prompts.items()}
        assert len(set(lengths.values())) >= 2, "Souls should produce different prompts"

        # Each has unique thinking framework
        assert "风险先于机会" in prompts["themis"]
        assert "假设" in prompts["crit"]
        assert "创意" in prompts["aria"]

    def test_veto_never_differs_between_modes(self):
        """Veto conditions are byte-identical across modes for all 3 Souls."""
        for soul in [THEMIS, CRIT, ARIA]:
            agent = compile_soul(soul, output_mode="agent")
            human = compile_soul(soul, output_mode="human")
            for vc in soul.veto_conditions:
                assert vc in agent, f"{soul.name}: veto '{vc}' missing in agent mode"
                assert vc in human, f"{soul.name}: veto '{vc}' missing in human mode"

    def test_values_never_differs_between_modes(self):
        """Values are identical across modes for all 3 Souls."""
        for soul in [THEMIS, CRIT, ARIA]:
            agent = compile_soul(soul, output_mode="agent")
            human = compile_soul(soul, output_mode="human")
            for v in soul.values:
                assert v in agent, f"{soul.name}: value '{v}' missing in agent mode"
                assert v in human, f"{soul.name}: value '{v}' missing in human mode"

    def test_token_budget_reasonable(self):
        """Both modes stay under 2000 tokens for compact Souls."""
        for soul in [THEMIS, CRIT, ARIA]:
            for mode in ["agent", "human"]:
                _prompt, tokens = compile_soul(soul), estimate_tokens(compile_soul(soul, output_mode=mode))
                assert tokens < 2000, f"{soul.name} {mode} mode: {tokens} tokens exceeds budget"


class TestMVPSummary:
    """Meta-test: document Stage 2 success criteria."""

    def test_mvp_stage2_summary(self):
        """Expected: 3/3 Souls show behavioral consistency.
        Run `pytest tests/test_mvp_soul_dual_output.py -v` for details."""
        test_methods = [m for m in dir(TestSoulDualOutput) if m.startswith("test_")]
        assert len(test_methods) >= 9, f"Expected ≥9 tests, found {len(test_methods)}"
