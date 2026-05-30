# OpenSymphony — 架构设计文档 v0.3

**日期**: 2026-05-19
**状态**: 设计阶段（v0.2 已验收，双功能扩展设计完成）
**决策者**: Project team
**核心定位**: 让你的 Agent 有性格、有规矩、能成长，**同时服务于 Agent 协作和人类交互**

---

## 0. v0.3 更新摘要（相对 v0.2）

**来源**: design session

| 变更 | 说明 |
|------|------|
| **双功能定位** | Agent 框架 + 人类对话框架统一架构 |
| **管道协议** | AgentMessage 不区分发送者类型（Agent/Human） |
| **Intent Bridge** | 自然语言→结构化消息的翻译层，置信度分级路由 |
| **Soul 双输出** | Soul Compiler 加 `output_mode`，同一 YAML 两种输出 |
| **三层递进** | Human 监督（第一天）→ 对话（涌现）→ 人格感知（已有） |
| **Human 接入层** | Gateway 新增 HumanAdapter，Human 监督走 Governance |
| **治理复用** | Unified governance with differentiated timeouts |

---

## 1. 核心差异化

| 锚点 | 描述 | 竞品对比 |
|------|------|---------|
| **治理** | Voting, precedent, and defense for collective decisions | Competitors lack governance |
| **灵魂** | Soul 定义 Agent 的思维框架，不是工具而是人格 | 竞品只有 Skill（工具） |
| **本地推理** | 5060Ti 16GB，不依赖云端 | 竞品纯云端 |
| **自进化** | tool_workshop，Agent 自己造工具 | Hermes 有记忆闭环但无工具自造 |
| **双功能** ⭐ | Agent 协作 + 人类对话统一架构，管道协议不区分发送者 | 零竞品做到深度统一 |

**一句话**：Symphony — 让你的 Agent 有性格、有规矩、能成长，无论对面是 Agent 还是人。
- **性格** = Soul（思维框架，跨交互模式的行为一致性保证）
- **规矩** = Governance（投票/先例/防御，洋葱架构，统一覆盖 Agent+Human）
- **成长** = tool_workshop（Agent 自己造工具，越用越强）
- **管子** ⭐ = AgentMessage 管道协议（不区分发送者类型，如 Unix 管道不区分数据源）

---

## 2. 架构

**洋葱模型**：请求从外到内，每层必经治理。⭐ v0.3 新增：Human 接入层和 Intent Bridge。

```
请求 → Gateway → [Intent Bridge*] → [Governance 拦截] → Runtime → [Governance 审计] → Kernel → 响应
                    ↑ 仅 Human 输入
```

```
┌───────────────────────────────────────────────────────────────┐
│                    OpenSymphony v0.3                      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │   Gateway（消息网关）— 第一层：接入                         │ │
│  │   HTTP REST │ Telegram │ Discord │ WeChat │ CLI            │ │
│  │   ⭐ HumanAdapter — 人类输入的专用适配器                    │ │
│  └─────────────────────────┬─────────────────────────────────┘ │
│                             │                                   │
│           ┌─────────────────┼──────────────────┐                │
│           │ Human 输入      │ Agent 输入        │                │
│           ▼                 ▼                   │                │
│  ┌─────────────────┐  ┌──────────────┐         │                │
│  │ Intent Bridge ⭐ │  │ 直接通过     │         │                │
│  │ NL→结构化消息    │  │              │         │                │
│  │ +raw_input 保留  │  │              │         │                │
│  │ +置信度分级路由  │  │              │         │                │
│  └────────┬────────┘  └──────┬───────┘         │                │
│           │                  │                  │                │
│           └──────────┬───────┘                  │                │
│                      ▼                          │                │
│  ┌───────────────────────────────────────────────▼───────────┐ │
│  │   AgentMessage 管道 ⭐ — 不区分发送者类型                   │ │
│  │   {sender, type, content, priority, raw_input?, ...}      │ │
│  └─────────────────────────┬─────────────────────────────────┘ │
│                             │                                   │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │   Governance（治理层）— 第二层：所有操作必经                 │ │
│  │   权限检查 → 先例匹配 → 风险分级 → 投票 → 审计             │ │
│  │   操作前拦截 + 操作后审计（append-only 记录）               │ │
│  │   ⭐ Human 监督安全策略：显式授权 + 双重确认 + 延迟执行      │ │
│  │   ⭐ 实现分化：Agent 投票超时 5s / Human 投票超时 24h       │ │
│  └─────────────────────────┬─────────────────────────────────┘ │
│                             │                                   │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │   Runtime（Agent 运行时）— 第三层：执行                     │ │
│  │   AgentPool │ TaskQueue │ Session │ Lifecycle              │ │
│  │   Agent 通信：事件总线 + AgentMessage 协议                  │ │
│  │                                                            │ │
│  │   Agent 类型：                                              │ │
│  │   - AIAgent（标准 Agent，由 Soul 驱动）                     │ │
│  │   ⭐ - HumanProxy（人类接入代理，走 Intent Bridge）          │ │
│  └─────────────────────────┬─────────────────────────────────┘ │
│                             │                                   │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │   Kernel（内核）— 第四层：核心能力                          │ │
│  │                                                            │ │
│  │   Soul Engine  │ SoulCompiler ⭐ │ LLM Router              │ │
│  │   (灵魂引擎)    │ (YAML→prompt   │ (模型路由+降级)         │ │
│  │                 │  双输出模式)    │                         │ │
│  │                                                            │ │
│  │   Memory       │ Tool Workshop │ Embedding (BGE-M3)        │ │
│  │   (三层记忆)    │ (工具自造)     │ (向量检索)               │ │
│  │                                                            │ │
│  │   ⭐ Soul output_mode:                                      │ │
│  │     "agent" → 结构化指令（JSON schema / tool calling）      │ │
│  │     "human" → 自然语言人格（对话式，有情感）                 │ │
│  │                                                            │ │
│  │   ⭐ Soul 模糊性解读：不同人格对同一模糊表达有不同解读策略   │ │
│  │     法律 Soul → 保守解读 → 追问澄清                        │ │
│  │     创意 Soul → 激进解读 → 先出方案再问                     │ │
│  │                                                            │ │
│  │   Soul = {id, name, archetype, thinking_framework,         │ │
│  │           values, veto_conditions, tools_whitelist,         │ │
│  │           ambiguity_strategy} ⭐                            │ │
│  │   Agent = Soul + Session + Context + Permissions            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │   Infrastructure（基础设施）— 第五层                       │ │
│  │   5060Ti Local │ 4 Cloud API │ SQLite/JSON                 │ │
│  │   Audit Log (append-only) │ Embedding Index                │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心概念

### 3.1 Agent

```python
class Agent:
    id: str
    soul: Soul              # 人格配置（思维框架 + 模糊性解读策略）
    session: Session         # 会话上下文
    permissions: Permissions # 权限等级 (L0-L3)
    tools: list[Tool]       # 可用工具（含自造工具）
    memories: MemoryTier    # 三层记忆
    status: AgentStatus     # created/init/active/idle/evolving/suspended/terminated
    resource_limits: Limits # 资源限制（token/h, 工具调用/h, 内存）
    agent_type: AgentType   # ⭐ "ai" | "human_proxy"
```

### 3.2 Soul（灵魂）

Soul 不是 Skill。Skill 是"能做什么"，Soul 是"怎么思考"。

⭐ v0.3 新增：Soul 是**跨交互模式的行为一致性保证**——不管面对 Agent 还是 Human，Agent 的价值观、决策逻辑、行为边界是一样的（因为 Soul 定义的是这些底层东西）。行为表现可以不同（结构化 vs 自然语言），但决策内核一致。

```python
class Soul:
    id: str
    name: str                    # "Assistant", "Analyst"
    archetype: str               # "预见型架构师", "量化分析师"
    thinking_framework: str      # 思维框架（决策前三问、苏格拉底式等）
    communication_style: str     # 沟通风格（简洁/详细/苏格拉底）
    values: list[str]            # 核心价值观（风险先于机会/数据驱动等）
    veto_conditions: list[str]   # 一票否决条件
    tools_whitelist: list[str]   # 可用工具白名单
    ambiguity_strategy: str      # ⭐ 模糊性解读策略：conservative | aggressive | balanced
```

### 3.2.1 SoulCompiler（灵魂编译器）⭐ v0.3 更新

Soul YAML 定义 → system_prompt 的编译过程，**支持双输出模式**：

```python
class SoulCompiler:
    """将结构化 Soul 定义编译为 LLM 可用的 system_prompt"""
    
    def compile(soul: Soul, output_mode: str = "agent") -> str:
        # output_mode = "agent" → 结构化指令（JSON schema / tool calling）
        # output_mode = "human" → 自然语言人格（对话式，有情感）
        # 
        # 共享部分（行为一致性保证）：
        #   archetype → 角色定义
        #   thinking_framework → 决策规则
        #   values → 约束条件
        #   veto_conditions → 硬性禁止规则
        #
        # 分化部分（输出模式）：
        #   "agent" → communication_style + tools_whitelist + 格式要求
        #   "human" → 对话风格 + 情感表达 + 模糊性解读策略
```

### 3.3 Intent Bridge ⭐ v0.3 新增

自然语言→结构化消息的翻译层，置信度分级路由：

```python
class IntentBridge:
    """将 Human 自然语言输入翻译为结构化 AgentMessage"""
    
    async def translate(raw_input: str) -> AgentMessage:
        result = await self.llm.parse_intent(raw_input)
        # result = {type, target, confidence, structured_content}
        
        if result.confidence > 0.8:
            # 快路径：直接通过
            return AgentMessage(
                type=result.type,
                content=result.structured_content,
                raw_input=raw_input,  # ← 始终保留原文
                confidence=result.confidence,
            )
        elif result.confidence > 0.5:
            # 中路径：附原文，Agent 可参考
            return AgentMessage(
                type=result.type,
                content=result.structured_content,
                raw_input=raw_input,
                confidence=result.confidence,
                note="low_confidence_check_raw",
            )
        else:
            # 慢路径：触发 Human 澄清
            return AgentMessage(
                type="clarification_needed",
                content={"question": f"你说的'{raw_input}'是指...?"},
                raw_input=raw_input,
                confidence=result.confidence,
            )
```

**关键设计决策**：
- `raw_input` 始终附带在 AgentMessage 中 → 解决上下文丢失问题（Anthropic vs Devin 教训）
- 置信度分级 → 低置信度才触发澄清，避免不必要的 Human 打扰
- Soul 的 `ambiguity_strategy` 影响 Intent Bridge 的阈值 → 法律 Soul 更保守（阈值更高）

### 3.4 Governance（治理）⭐ v0.3 更新

**洋葱架构**：所有请求先过治理层，操作后审计。⭐ 新增 Human 监督安全策略。

```python
class GovernanceLayer:
    """所有 Agent 操作的必经之路（中间件模式）"""
    
    # ⭐ 实现分化：同一接口，不同参数
    TIMEOUTS = {
        "agent_vote": 5,       # Agent 投票 5 秒超时
        "human_vote": 86400,   # Human 投票 24 小时超时
    }
    
    AUTH_STRATEGIES = {
        "agent_to_agent": "auto_authorize",      # 自动授权 + 审计
        "human_to_agent": "explicit_authorize",   # ⭐ 显式授权 + 审计
        "human_to_high_risk": "double_confirm",   # ⭐ 双重确认 + 延迟执行
    }
    
    async def before_action(agent, action):
        """操作前拦截"""
        # 1. 权限检查 (L0-L3)
        # 2. 先例匹配 (precedent_db)
        # 3. 风险分级 (P0-P2)
        # 4. 投票（重大决策需多 Agent 投票）
        # 5. ⭐ Human 高风险操作：双重确认
        # 6. 拒绝 → 返回拒绝原因
    
    async def after_action(agent, action, result):
        """操作后审计（append-only，不可篡改）"""
        # 1. 结果记录
        # 2. 异常检测
        # 3. 先例更新
        # 4. 反思触发
```

### 3.5 AgentMessage（管道协议）⭐ v0.3 更新

**管道哲学**：AgentMessage 不区分发送者类型，如 Unix 管道不区分数据源。

```python
class AgentMessage:
    """Agent 间通信的标准协议 — 不区分发送者是 Agent 还是 Human"""
    sender: str           # 发送者 ID（Agent ID 或 "human:{user_id}"）
    receiver: str | list  # 接收 Agent ID（或广播）
    type: str             # request/response/broadcast/vote/clarification_needed ⭐
    content: Any          # 结构化消息内容
    priority: int         # 优先级
    requires_vote: bool   # 是否需要投票
    timestamp: datetime
    raw_input: str | None # ⭐ 原始自然语言（Human 输入时保留，Agent 输入时为 None）
    confidence: float     # ⭐ Intent Bridge 置信度（Human 输入时有值）
    sender_type: str      # ⭐ "ai" | "human" — 仅用于日志/审计，不影响路由

class EventBus:
    """事件总线——Agent 间通信的基础设施"""
    async def publish(message: AgentMessage)
    async def subscribe(agent_id: str, handler: Callable)
    async def broadcast(message: AgentMessage)
```

### 3.6 LLM Router（模型路由 + 降级）

```python
class LLMRouter:
    """按任务类型路由模型，带降级链"""
    
    ROUTING = {
        "code_generation": ["local", "deepseek", "mimo"],
        "creative_writing": ["mimo", "deepseek", "local"],
        "deep_analysis": ["deepseek", "kimi", "mimo"],
        "long_context": ["kimi", "deepseek"],
        "tool_generation": ["mimo", "deepseek"],
        "intent_parsing": ["mimo", "local"],  # ⭐ Intent Bridge 用
    }
```

### 3.7 Memory（记忆）

三层架构，带向量检索：

| 层 | 存储 | 内容 | 检索方式 | 容量 |
|----|------|------|---------|------|
| L1 | 内存 | 当前会话上下文 | 直接索引 | ~4K tokens |
| L2 | SQLite + BGE-M3 | 历史会话 + 经验 | 向量相似度检索 | ~100K 条 |
| L3 | 文件（append-only）| 原始日志 + 审计 | 关键词搜索 | 无限 |

---

## 4. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | Existing codebase, team expertise |
| Web 框架 | FastAPI（全 async） | 异步优先，性能好 |
| 消息网关 | 自己写 adapter（零依赖核心） | 不绑定第三方 |
| ⭐ Intent Bridge | Mimo v2.5 + structured output | 轻量、已有 API、支持 JSON schema 约束 |
| 记忆 | SQLite + BGE-M3 向量索引 | 轻量 + 语义检索 |
| 治理 | agora-core (已有) → async 重写 | 67/67 测试全过 |
| 本地推理 | llama-cpp-python / Hippo | 5060Ti 已部署 |
| 云端 API | Mimo/DeepSeek/Kimi/GLM | 4 个已接入，带降级链 |
| 任务队列 | 内置 PriorityQueue | 已在用 |
| 测试 | pytest + 端到端测试 | 标准 + 全链路验证 |
| 依赖策略 | **零依赖核心**，adapter 按需装 | 降低门槛 |

---

## 5. 目录结构

```
opensymphony/
├── opensymphony/                  # 框架核心包
│   ├── __init__.py
│   ├── kernel.py              # 内核：启动、配置、生命周期
│   ├── agent.py               # Agent 定义（⭐ 含 agent_type: ai/human_proxy）
│   ├── soul.py                # Soul 引擎（⭐ 含 ambiguity_strategy）
│   ├── soul_compiler.py       # Soul YAML → prompt（⭐ 双输出模式）
│   ├── session.py             # 会话管理
│   ├── intent_bridge.py       # ⭐ 自然语言→结构化消息翻译层
│   ├── memory/
│   │   ├── l1.py              # 工作记忆
│   │   ├── l2.py              # 经验库
│   │   └── l3.py              # 原始日志
│   ├── governance/
│   │   ├── voting.py          # 投票机制（adapted from governance module）
│   │   ├── precedent.py       # 先例系统
│   │   ├── defense.py         # 防御层
│   │   ├── hitl.py            # 人工审批
│   │   └── human_safety.py    # ⭐ Human 监督安全策略
│   ├── gateway/
│   │   ├── base.py            # 网关基类
│   │   ├── http.py            # HTTP REST
│   │   ├── human_adapter.py   # ⭐ Human 输入适配器（调 Intent Bridge）
│   │   ├── telegram.py        # Telegram adapter
│   │   └── discord.py         # Discord adapter
│   ├── runtime/
│   │   ├── pool.py            # Agent 池
│   │   ├── scheduler.py       # 任务调度
│   │   └── lifecycle.py       # Agent 生命周期
│   ├── tools/
│   │   ├── workshop.py        # 工具工坊（已有）
│   │   ├── loader.py          # 工具加载器（已有）
│   │   └── builtin/           # 内置工具
│   ├── llm/
│   │   ├── router.py          # 模型路由
│   │   ├── local.py           # 本地推理
│   │   └── cloud.py           # 云端 API
│   └── utils/
│       ├── audit.py           # 审计日志
│       └── safety.py          # 安全检查
├── souls/                     # Soul 配置文件
│   ├── themis.yaml            # ⭐ 含 ambiguity_strategy: conservative
│   ├── athena.yaml            # ⭐ 含 ambiguity_strategy: balanced
│   ├── aria.yaml              # ⭐ 含 ambiguity_strategy: aggressive
│   └── ...
├── tests/
├── examples/
├── README.md
├── pyproject.toml
└── LICENSE                    # Apache 2.0
```

---

## 6. Human 三层递进策略 ⭐ v0.3 新增

| 层 | 功能 | 开发阶段 | 依赖 |
|----|------|---------|------|
| **L1：Human 监督** | HITL 审批、撤销、审计、显式授权 | Phase 2（和治理同步） | Governance 层 |
| **L2：Human 对话** | 自然语言交互、多轮上下文 | Phase 4 后（Soul 双输出涌现） | Soul Compiler + Intent Bridge |
| **L3：人格感知** | Agent 面对人类时有一致人格感 | 已有（Soul 天然支持） | Soul Engine |

**关键洞察**：L3 已经是现有能力，L1 是安全刚需，只有 L2 需要新开发（Intent Bridge）。总体增量开发量 ~20h。

---

## 7. 路线图

### Phase 0：地基（1.5 周，~20h）

**目标**：Symphony 能启动、创建 Agent、Agent 能对话

- [ ] `opensymphony/kernel.py` — 框架启动/配置（全 async）
- [ ] `opensymphony/agent.py` — Agent 基础定义 + 生命周期状态机
- [ ] `opensymphony/soul.py` — Soul 引擎
- [ ] `opensymphony/soul_compiler.py` — Soul YAML → system_prompt 编译器
- [ ] `opensymphony/llm/router.py` — 模型路由 + 降级链
- [ ] `opensymphony/gateway/http.py` — HTTP REST 网关
- [ ] `pyproject.toml` — 零依赖核心 + 可选依赖
- [ ] 端到端测试：HTTP 请求 → Agent 对话 → 返回

### Phase 1：记忆 + 会话 + 通信（1.5 周，~22h）

**目标**：Agent 有记忆，跨会话持续，Agent 间能通信

- [ ] `opensymphony/memory/l1.py` — 工作记忆（当前上下文）
- [ ] `opensymphony/memory/l2.py` — 经验库（SQLite + BGE-M3 向量检索）
- [ ] `opensymphony/memory/l3.py` — 原始日志（append-only）
- [ ] `opensymphony/session.py` — 会话管理
- [ ] `opensymphony/event_bus.py` — 事件总线 + AgentMessage 协议
- [ ] Agent 跨会话记忆召回
- [ ] 端到端测试：Agent 能记住上次对话 + Agent A 发消息给 Agent B

### Phase 2：治理 + Human 监督 ⭐（1.5 周，~28h）

**目标**：多 Agent 协作时有治理机制，**Human 监督从第一天支持**

- [ ] `opensymphony/governance/voting.py` — adapted from governance module（async 重写）
- [ ] `opensymphony/governance/precedent.py` — 先例系统
- [ ] `opensymphony/governance/defense.py` — 防御层
- [ ] `opensymphony/governance/hitl.py` — 人工审批
- [ ] `opensymphony/governance/human_safety.py` — ⭐ Human 监督安全策略
- [ ] 治理层中间件：before_action + after_action
- [ ] 审计日志（append-only）
- [ ] ⭐ 实现分化参数（Agent 投票 5s / Human 投票 24h）
- [ ] 端到端测试：3 个 Agent 投票通过一项决策 + ⭐ Human 审批高风险操作

### Phase 3：多 Agent + 工具（1 周，~18h）

**目标**：Agent 池 + 工具自造 + 资源沙盒

- [ ] `opensymphony/runtime/pool.py` — Agent 池管理
- [ ] `opensymphony/runtime/scheduler.py` — 任务调度
- [ ] `opensymphony/runtime/sandbox.py` — Agent 资源限制
- [ ] `opensymphony/tools/workshop.py` — 工具工坊（迁移 + AST 安全分析）
- [ ] Agent 能自造工具并共享
- [ ] 端到端测试：Agent A 造工具 → Agent B 使用

### Phase 4：消息网关 + Soul 深度 + ⭐ Intent Bridge（1.5 周，~30h）

**目标**：接入 Telegram/Discord，Soul 系统完善，**Human 对话能力涌现**

- [ ] `opensymphony/gateway/telegram.py` — Telegram adapter
- [ ] `opensymphony/gateway/discord.py` — Discord adapter
- [ ] `opensymphony/gateway/human_adapter.py` — ⭐ Human 输入适配器
- [ ] `opensymphony/intent_bridge.py` — ⭐ Intent Bridge（Mimo v2.5 + structured output）
- [ ] Soul YAML 规范 v1.0 + ⭐ ambiguity_strategy + ⭐ output_mode
- [ ] Soul 压缩 + 测试流程
- [ ] ⭐ 端到端测试：Human 自然语言 → Intent Bridge → AgentMessage → Agent 响应

### Phase 5：产品化 + 开源（2 周，~35h）

**目标**：开源准备

- [ ] 完整文档（README + API docs + 架构说明）
- [ ] 30+ 端到端测试
- [ ] `examples/` 示例项目（3 个：简单对话 / 多 Agent 协作 / ⭐ Human+Agent 混合交互）
- [ ] CI/CD（GitHub Actions）
- [ ] GOVERNANCE.md + CONTRIBUTING.md
- [ ] PyPI 发布
- [ ] 开源发布

**总计**：~153h，10-11 周（含缓冲）

---

## 8. 双功能 MVP 验证计划 ⭐ v0.3 新增

**来源**: design review, approved

| 阶段 | 内容 | 成功标准 | 期限 |
|------|------|---------|------|
| **1. 管道协议验证** | 10 个 Agent-Agent 测试改 Human 模拟输入 | 8/10 通过 | 1 周 |
| **2. Soul 双输出验证** | 3 个 Soul（忒弥斯/Crit/刻菲斯）生成 Agent+Human 输出 | 3/3 行为一致 | 1 周 |
| **3. Intent Bridge 验证** | 10 个模糊 Human 输入→结构化消息 | ≥7 正确翻译，≤2 不必要澄清，κ>0.7 | 1 周 |

---

## 9. 安全设计

### 9.1 Agent 沙盒

每个 Agent 运行时有资源限制：
- 最大 token 消耗/小时（可配置）
- 最大工具调用次数/小时
- 超限自动 suspend → 需 HITL 恢复

### 9.2 治理层防篡改

- 投票结果、先例记录 → **append-only**，不可修改或删除
- 审计日志带 hash 校验

### 9.3 工具工坊安全

- **AST 级别代码分析**（不只正则匹配）
- 文件操作限制：只能操作 `tool_workshop/` 目录
- 网络访问禁止：自造工具不能联网
- 子进程隔离测试 + 超时

### 9.4 ⭐ Human 安全策略

- Agent→Agent：自动授权 + 审计
- Human→Agent：**显式授权** + 审计
- Human→高风险操作：**双重确认** + 延迟执行
- Human 输入日志独立存储，不可被 Agent 修改

## 10. 与现有系统的关系

| 现有系统 | 处理方式 |
|---------|---------|
| governance module | → 迁移到 opensymphony/governance/ |
| Symphony orchestrator.py | → 重构为 opensymphony/kernel.py |
| Symphony tool_workshop.py | → 迁移到 opensymphony/tools/workshop.py |
| Symphony souls/ | → 迁移到 souls/ (YAML 格式，⭐ 加 ambiguity_strategy) |
| Symphony dispatch.py | → 迁移到 opensymphony/gateway/http.py |
| governance module | → integrated |
| 5060Ti 部署 | → 保持，作为本地推理节点 |

---

## 11. 开源策略

- **许可证**: Apache 2.0
- **品牌**: OpenSymphony
- **定位**: "唯一内置民主治理 + 灵魂系统 + 人类协作的 multi-agent 框架" ⭐
- **目标用户**: AI 创业者、Agent 开发者、Multi-Agent 研究者
- **发布时机**: Phase 4 完成后（有网关 + 治理 + Soul + ⭐ Human 对话，才能展示差异化）
- **差异化叙事**: ⭐ "给 Agent 世界造一根管子——Unix 管道哲学，Agent 和 Human 走同一根管子"

---

## 12. 决策溯源

| 决策 | 来源 | 日期 |
|------|------|------|
| Onion architecture + five layers | design review | 2026-05-12 |
| 双功能定位 | design review | 2026-05-19 |
| 管道协议（AgentMessage 不区分发送者） | design session | 2026-05-19 |
| Three-tier progression | design session | 2026-05-19 |
| Intent Bridge 置信度分级路由 | design session+雅典娜 | 2026-05-19 |
| Soul 行为一致性保证 + 模糊性解读 | design session+Aria | 2026-05-19 |
| 治理复用 + 实现分化 | design session | 2026-05-19 |
| 专业化悖论（小团队差异化生存） | design session | 2026-05-19 |

---


