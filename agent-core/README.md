# agent-core

一个轻量、可追踪、可扩展的 Agent 工作流运行框架。

`agent-core` 不绑定具体模型，也不绑定具体业务。你提供模型、工具和业务规则，它负责把这些能力组织成可执行的 Agent：管理上下文、编排步骤、调用工具、处理失败、记录运行过程，并在需要时从 checkpoint 恢复。

> **一句话定位：** 用同一个运行底座，搭建学习助手、建模助手、测试助手或其他领域 Agent。

项目同时包含：

- `agent_core`：功能完整的 Graph/Agent 运行时、持久化和 HTTP 工作台
- `agent_lite`：只保留对话、历史和工具循环的极简 Agent 外壳

## 为什么做这个项目

许多 Agent Demo 只能展示“输入问题，输出文本”。真实业务还需要知道：

- Agent 调用了哪些工具
- 工具结果是否可信
- 哪一步失败了
- 任务为什么停止
- 运行是否可以恢复

`agent-core` 把这些执行边界放进框架中，让模型负责理解和决策，让工具负责真实业务操作，让运行时负责流程和状态。

## 核心模型

```text
用户输入
    |
    v
Agent / Graph Runtime
    |-- 模型适配器：理解任务、决定下一步
    |-- 工具注册表：调用 API、算法、数据库或 Python 函数
    |-- 工作流图：顺序执行、条件路由、状态更新
    |-- 生命周期：PENDING -> RUNNING -> 终态
    |-- Trace / Checkpoint：记录过程、支持恢复
    v
结构化结果
```

模型、工具和业务应用通过接口解耦：

```text
模型负责理解和决策
工具负责执行真实操作
业务服务负责数据和规则
agent-core 负责编排、状态和可观测性
```

## 主要能力

### Agent 和工作流

- 顺序工作流和 Graph 图执行
- 条件路由、节点状态更新和子 Agent 委派
- 模型、工具、记忆通过协议注入
- JSON 声明式 Agent 装配和配置热加载

### 工具和模型

- 注册 Python 函数或外部业务 API
- 声明式 `tool` 与 `agent_loop` 共用 Harness 工具流水线、调用审计和运行级工具额度
- 工具参数校验、能力声明和错误原因码
- 模型自主工具调用循环
- Harness 调用预算：限制单次 `agent_loop` 的模型轮次与工具调用次数
- OpenAI 兼容模型适配器
- 自定义本地模型和自训练模型接入
- 可选的流式输出和多轮对话历史

### 运行可靠性

- `COMPLETED`、`BLOCKED`、`FAILED`、`CANCELED` 状态
- 节点级 Trace 和运行生命周期事件
- 协作式取消、节点边界超时和最大步数限制
- 运行结果、事件和 checkpoint 持久化
- 显式恢复，不伪装失败，不把模拟结果当成真实执行证据

### 服务和工作台

- 纯 Python 标准库 HTTP API
- 同步运行和后台运行
- SSE 生命周期和 token 事件流
- 运行查询、取消、恢复和会话记忆
- 浏览器工作台：任务提交、时间线、Trace、拓扑和结果查看

服务端会话记忆保存在运行目录的 `sessions/` 中，重启后按 `session_id` 继续读取；清除会话也会删除对应记录。运行索引同时保存运行与会话的归属。它是单实例文件存储，尚不提供跨实例共享或长期记忆治理。

审批、结果核对和恢复写入可选用 Bearer token 保护：先在环境变量中设置密钥，再以 `agent-core-server --approval-token-env AGENT_CORE_APPROVAL_TOKEN` 启动。调用 `POST /api/runs/{run_id}/approval-decision`、`/approval-resolution` 或 `/resume` 时传入 `Authorization: Bearer <密钥>`。浏览器工作台可在“设置 → 审批凭证”输入同一个 token（不含 Bearer 前缀），点击“本页使用”后手动重试操作。凭证只在本页内存使用，不进入存储、URL 或运行记录，刷新、离开页面或点击清除即忘记；浏览器入口接受 1–4096 个不含空白的 ASCII 字符。其他 API 及读取入口仍无身份认证。本机默认配置不启用 token，这不是完整的公网鉴权方案。参考应用的相同入口见 [M13](docs/HARNESS_M13.md)。

工作台图标随 wheel 本地交付，字体使用系统回退。M13 浏览器流程可从源码用 `python -m tools.browser_smoke_server --port 8004` 在独立目录中复现（端口可换为未占用值）；服务只使用合成资料和脚本模型，固定 token `browser-smoke-only` 仅用于测试。实际浏览器证据与范围见 [M13](docs/HARNESS_M13.md)。

## 30 秒运行

要求 Python 3.10 或更高版本。

```bash
git clone <your-repository-url>
cd agent-core
pip install -e .
agent-core-server --port 8000
```

打开 <http://127.0.0.1:8000>，选择 `workbench` Agent，输入任务即可看到执行时间线和结构化结果。

也可以直接调用 API：

```bash
curl -X POST http://127.0.0.1:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"agent":"workbench","input":{"task":"整理本周进展并准备周一评审"}}'
```

没有配置大模型时，依赖模型的 Agent 会明确返回 `MODEL_NOT_CONFIGURED`；这不是静默降级或伪造回答。

## Python 最小用法

### 线性工作流

```python
from agent_core import Step, StepOutcome, StepStatus, WorkflowRunner

workflow = WorkflowRunner([
    Step("normalize", lambda ctx: StepOutcome(
        status=StepStatus.COMPLETED,
        output={"text": ctx.input["text"].strip()},
    )),
])

result = workflow.run({"text": "  hello  "})
print(result.status)       # RunStatus.COMPLETED
print(result.outputs)
```

### Graph Agent

```python
from agent_core import AgentBuilder, NodeResult

agent = (
    AgentBuilder("text-agent", instructions="处理用户文本")
    .add_node(
        "prepare",
        lambda ctx: NodeResult.success({"text": ctx.input["text"].strip()}),
    )
    .add_node(
        "finish",
        lambda ctx: NodeResult.success({"summary": f"已处理：{ctx.state['text']}"}),
    )
    .set_entry("prepare")
    .add_edge("prepare", "finish")
    .build()
)

result = agent.run({"text": "hello"})
print(result.status)
print(result.state["summary"])
```

节点只能通过 `NodeResult` 返回状态更新，运行时负责校验和合并状态。节点异常会进入 `FAILED`，阻断和失败不会继续执行后续节点。

## 模型和工具接入

模型是外部依赖。最小模型协议只需要返回包含 `content` 的映射：

```python
class MyModel:
    def complete(self, messages):
        answer = your_model.generate(messages)
        return {"content": answer}
```

工具可以是普通 Python 函数：

```python
from agent_lite import Agent, Tool

agent = Agent(
    model=MyModel(),
    system="你是一个简洁的助手",
    tools=[Tool(
        "add",
        lambda a, b: a + b,
        description="计算两个数的和",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    )],
)

reply = agent.run("2 加 3 等于多少？")
print(reply.content)
```

如果模型实现可选的 `complete_with_tools(messages, tool_schemas)`，Agent 会自动执行工具调用循环；否则它仍可以作为普通对话模型使用。

OpenAI 兼容端点可以通过环境变量配置：

```bash
export AGENT_MODEL_BASE_URL=https://api.example.com/v1
export AGENT_MODEL_API_KEY=your-key
export AGENT_MODEL_NAME=your-model
```

也可以直接包装 Ollama、vLLM、SGLang 或自定义推理服务。模型适配器不要求继承项目中的基类，只要遵守调用协议即可。

## 声明式 Agent

Harness 层的开发规划和验收标准见 [Harness PRD](docs/HARNESS_PRD.md)，阶段开发文档见 [Harness M0](docs/HARNESS_M0.md)、[Harness M1](docs/HARNESS_M1.md)、[Harness M2](docs/HARNESS_M2.md)、[Harness M3](docs/HARNESS_M3.md)、[Harness M4](docs/HARNESS_M4.md)、[Harness M5](docs/HARNESS_M5.md) 与 [Harness M6](docs/HARNESS_M6.md)。`agent_loop` 支持调用预算、上下文限制和可选的模型/工具重试；Agent 可选配置跨 `llm`、`agent_loop` 与同步子 Agent 的运行级调用额度。持久化的子运行结果可在父节点重放时复用，结果未落盘则停止自动委派。需要批准的工具会进入 `WAITING_APPROVAL`。本机服务支持批准后显式恢复、拒绝终止及结果不明时的人工核对。副作用工具仅在应用提供幂等键调用与结果查询契约时允许自动重试，需审批工具不自动重试。

普通 `tool` 节点现已接入同一流水线：支持调用审计、能力检查、运行级预算、可选有限重试及审批后恢复。节点默认最多尝试一次，重试需同时配置 `tool_max_retries` 和足够的 `max_tool_calls`。配置与恢复边界见 [Harness M7](docs/HARNESS_M7.md)。

M8 将普通工具和模型循环的审批恢复身份统一到节点访问边界，修复循环图误用旧审批结果的问题，并增加故障恢复矩阵。结果不明或缺少可信访问身份的 HTTP 恢复在执行前返回 409；历史快照兼容与具体边界见 [Harness M8](docs/HARNESS_M8.md)。

M9 为自定义 Python 节点提供 `ctx.calls.model(messages)` 和 `ctx.calls.tool(name, arguments)`；通过 `add_node(..., calls=NodeCallPolicy(...))` 配置权限、额度、上下文和重试。首版每节点一次逻辑调用，多步操作拆成图节点。接口、审批恢复与迁移示例见 [Harness M9](docs/HARNESS_M9.md)。

不想手写 Python 时，可以使用 JSON 配置 Agent。内置节点包括：

```text
validate_input  输入校验
split_steps     文本拆分
template        状态模板渲染
tool            工具调用
route_by_value  条件路由
llm             模型调用
agent_loop      模型工具循环
subagent        子 Agent 委派
```

示例：

```bash
python -m agent_core.server --config examples/assistant_agent.json
```

## HTTP API

服务启动后提供以下主要接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务和模型健康状态 |
| `GET` | `/api/agents` | 已注册 Agent 及其拓扑 |
| `POST` | `/api/runs` | 创建一次运行 |
| `GET` | `/api/runs` | 分页查询运行记录 |
| `GET` | `/api/runs/{id}` | 获取结果、Trace 和生命周期 |
| `GET` | `/api/runs/{id}/status` | 查询当前状态 |
| `GET` | `/api/runs/{id}/events/stream` | SSE 实时事件流 |
| `POST` | `/api/runs/{id}/cancel` | 发出协作式取消 |
| `GET` | `/api/runs/{id}/checkpoint` | 查看恢复点 |
| `POST` | `/api/runs/{id}/resume` | 从 checkpoint 显式恢复 |
| `GET` | `/api/sessions/{id}` | 查看会话记忆 |

## 如何扩展成具体领域 Agent

`agent-core` 只提供通用能力，领域能力通过工具和工作流接入。例如 MBSE 建模 Agent 可以注册：

```text
query_model
create_element
create_relation
check_consistency
analyze_impact
save_version
```

学习助手可以注册：

```text
search_material
create_plan
generate_exercises
grade_answer
update_progress
```

同一个运行底座不需要修改，替换模型、工具、记忆和业务规则即可组合出不同 Agent。

## `agent_core` 和 `agent_lite`

| 包 | 适用场景 |
| --- | --- |
| `agent_core` | 图工作流、持久化、生命周期、HTTP 服务和可视化工作台 |
| `agent_lite` | 最小对话外壳、上下文和模型驱动的工具循环 |

`agent_lite` 是独立入口，不依赖 `agent_core`，适合快速接入自训练模型或嵌入已有应用。完整运行时能力则使用 `agent_core`。

## 与 LangChain / LangGraph 的关系

`agent-core` 不是为了替代它们的生态：

- LangChain 更适合快速集成大量模型、文档、向量库和第三方工具。
- LangGraph 更适合复杂的有状态 Agent 图、长流程和人工介入。
- `agent-core` 更关注轻量、显式流程、稳定状态和业务系统嵌入。

如果项目需要 RAG、向量检索或特定第三方集成，可以把这些能力封装成工具，也可以在工具层局部使用 LangChain。

## 当前边界

当前版本适合课程项目、原型验证和领域 Agent MVP。它还不是完整的生产级平台，以下能力需要应用层或后续版本补充：

- 用户认证、多租户和更细粒度权限
- 分布式任务队列和横向扩展
- 生产级长期记忆和数据治理
- 操作系统级工具沙箱、服务级限流与跨进程幂等协调（节点预算、可选重试和应用侧幂等契约已实现）
- 知识库、RAG 和领域评测体系
- 大规模运行监控和告警

项目明确不内置大模型，也不自动替应用决定业务规则。模型、工具和外部系统的事实边界由应用负责。

## 项目结构

```text
agent-core/
├── agent_core/              # 完整运行时和服务
├── agent_lite/              # 极简 Agent 外壳
├── examples/                # Python 和 JSON 示例
├── frontend/                # Web 工作台（安装包同时包含静态文件）
├── tests/                   # 契约和回归测试
├── docs/                    # 项目文档和截图
├── pyproject.toml
└── README.md
```

## 开发

当前已完成 Harness M9 的自定义节点受管调用，M10 离线验收已通过；M11 正在进行候选版本交付准备。[全项目评估](docs/PROJECT_ASSESSMENT_2026_11_08.md)建议下一轮先推进 M12 流式与持续运行边界，再做 M13 工作台工具任务闭环；具体任务及验收标准见[下一阶段计划](docs/NEXT_PHASE_PLAN.md)。M12 首增量已实现稳定游标、续读与缺口提示，详见 [M12](docs/HARNESS_M12.md)；缓存回收、容量限制和内置模型响应上限也已实现，200 次本机持续运行基线已通过，见[资源记录](docs/M12_RESOURCE_BASELINE.md)。M13-01 已提供[受控资料审阅 Agent](docs/HARNESS_M13.md)，支持模型只读工具选择、证据校验及批准后保存；M13-02 已增加调用事实、预算与 usage 覆盖率面板；M13-03 已增加白名单审批说明与服务端恢复资格解释；凭证交互、真实模型、远端矩阵和发布门禁仍待完成。

M10 已提供资料审阅与任务分流两个验收场景：`python -m examples.workflow_acceptance --mode offline --repeat 2` 运行 14 个固定用例各两次；`--mode real` 使用服务端模型配置，缺少配置时生成 NOT_RUN 报告。离线通过不代表真实模型质量通过，当前真实验收仍待配置。用法与报告说明见 [M10](docs/HARNESS_M10.md)。

M11 增加仓库外虚拟环境的 wheel 安装检查，覆盖 Python API、CLI、内置工作台资源、示例及重启查询。独立安装、构建和排障步骤见 [M11 交付准备](docs/HARNESS_M11.md)，公开 API、存储格式与升级限制见 [兼容说明](docs/COMPATIBILITY.md)。真实模型、远端 CI、许可证及发布版本决策仍有待完成。

```bash
python -m pip install -e ".[dev]"
python -m examples.text_pipeline
python -m examples.graph_pipeline
python -m agent_core.server --port 8000
```

构建运行包并验证独立安装：

```bash
python -m build --wheel --no-isolation
python tools/verify_distribution.py dist
```

wheel 包含 `agent_core`、`agent_lite`、工作台资源和三个独立 JSON 配置；完整 Python 示例及验收脚本在源码仓库中。运行包没有第三方运行依赖；`dev` 额外依赖只用于测试与构建。

测试目录位于 `tests/`。项目当前处于 `0.x` 阶段，次版本可能包含破坏性变更，稳定调用方应根据结构化类型和 `reason_code` 判断结果，不要依赖异常消息文本。

工作台审批异步回归使用 Node.js 内置测试，无第三方前端测试依赖：`node --test tests/frontend_approval.test.cjs tests/frontend_streaming.test.cjs tests/frontend_harness.test.cjs`。CI 在 Windows 和 Linux 上执行这项检查，Node.js 只用于开发验证，不是 Python 服务的运行依赖。

## License

仓库尚未附带许可证；对外分发前需要明确许可证。

