# Harness M9：自定义节点的受管调用

状态：实现和专项验证完成，最终回归结果见文末。M9-01–M9-04 的接口、配置、共用执行服务和迁移示例均已交付。

## 公开接口与装配边界

`AgentBuilder.add_node(id, execute, calls=NodeCallPolicy(...))` 为自定义节点配置调用权限。节点通过 `ctx.calls.model(messages)` 获取模型回复映射，通过 `ctx.calls.tool(name, arguments)` 获取工具输出映射；无需构造底层流水线或读取私有字段。

`NodeCallPolicy` 是不可变配置：`allowed_tools`、`capabilities`、`limits`、`model_retry`、`tool_retry`、`context` 分别表达可见工具、能力授予、节点额度、重试和上下文限制。调用方法不能传入更大的权限或预算。未配置 policy 的自定义节点使用这些入口时明确阻断。

首版每个节点访问只允许一次逻辑受管调用，内部重试按实际尝试消耗额度。第二次逻辑调用在执行前失败；多步外部操作应拆成图节点。此限制保证审批恢复只需重放一次调用之前的本地参数计算，不需要新增任意 Python 调用序列的持久化日志。调用前的本地计算须可重放，原始模型、工具或其他外部副作用不自动纳入管理。

## 返回与停止

成功返回隔离的 JSON 映射；模型回复保留适配器实际返回的 usage。失败、权限拒绝、取消或等待审批会抛出 `ManagedCallStopped`，由 Agent 外壳转换为对应 `NodeResult`。即使节点捕获该异常并返回成功，已记录的停止结果仍优先，等待不能被伪装成成功。

调用服务只在节点执行期间有效；不可保存引用供节点退出后继续调用。既有 `ctx.model`、`ctx.tools` 与基础工具适配接口继续作为低层入口保留，框架不会拦截任意 Python 代码的外部操作。

## 执行与恢复

模型与工具调用复用已有 Harness 流水线。内置 `llm`、`tool` 和 `agent_loop` 通过同一服务构造调用流水线，预算、审计和策略语义保持一致；模型工具循环仍由既有驱动实现。

自定义模型调用支持已有 `complete_stream` 协议与 token bus；产生 token 后不自动重试。上下文限制在模型调用前执行，取消在外部调用前检查，重试沿用协作式取消边界。

自定义工具审批沿用 M8：快照绑定运行、节点和状态版本；参数不匹配时阻断，STARTED 结果不明时不重发，已落盘结果在同一节点边界重放时复用。审批存储、决定、运行结果及 checkpoint 均由宿主显式提供；普通未配置持久恢复的模型与非审批工具调用仍遵守 M8 的恢复限制。

## 最小接入

```python
from agent_core import AgentBuilder, NodeCallPolicy, NodeResult, Tool, ToolRegistry

tools = ToolRegistry()
tools.register(Tool("measure", lambda args: {"length": len(args["text"])}, read_only=True))

def measure(ctx):
    result = ctx.calls.tool("measure", {"text": ctx.state["text"]})
    return NodeResult.success({"length": result["length"]})

agent = (AgentBuilder("custom", tools=tools)
         .add_node("measure", measure, calls=NodeCallPolicy(allowed_tools=frozenset({"measure"})))
         .set_entry("measure").build())
result = agent.run({"text": "hello"})
```

模型节点使用 `reply = ctx.calls.model(messages)`，再按业务需要解析 `reply["content"]`。模型由 `AgentBuilder(model=...)` 注入；消息由应用显式传入，不自动拼接 instructions 或会话记忆。

完整双节点示例：`python -m examples.managed_calls`。示例用明确标注的脚本模型验证调用路径，将模型参数替换为实际适配器即可接入服务。默认示例不验证真实模型质量。

## 配置和额度

节点默认额度为 `HarnessLimits(1, 1)`，默认不重试。要重试，须同时为对应调用增加尝试额度并设置 `ModelRetryPolicy` 或 `ToolRetryPolicy`。工具重试仍受只读或应用侧幂等契约约束；审批工具不自动重试。

Agent 的 `run_limits` 和同步子 Agent 继承规则沿用 M6；启用时须提供持久化审计。节点可配置的额度不会覆盖或重置运行额度。工具可见性与能力是两道检查：仅出现在 `allowed_tools` 中不会自动授予工具所需能力，须由 `capabilities` 显式授予。

`NodeCallPolicy` 为不可变对象，受管服务不提供修改权限、额度或停止结果的公开属性。这里的约束是 SDK 使用契约，不是针对恶意 Python 代码的隔离边界。

## 停止原因与兼容

| 原因码 | 行为 |
| --- | --- |
| `MANAGED_CALLS_NOT_CONFIGURED` | 未在 add_node 中配置 calls，阻断调用 |
| `MULTIPLE_MANAGED_CALLS` | 同一节点第二次逻辑调用在执行前失败；先前操作不回滚 |
| `MANAGED_CALLS_CLOSED` | 节点已退出，拒绝继续使用保存的服务或上下文 |
| `APPROVAL_REQUEST_MISMATCH` | 恢复时工具参数、调用种类或保存的调用身份不匹配 |
| 既有 Harness 原因码 | 继续表达模型/工具失败、能力拒绝、审计错误、额度耗尽及结果不明 |

运行取消仍由 Runtime 确认并进入 `CANCELED`；节点受管入口会先阻止对应新调用。成功回复保持 JSON 快照隔离；不符合原有 JSON 模型协议的回复返回 `MODEL_INVALID_REPLY`。usage 原样返回给应用，审计继续只记录适配器明确提供的已支持 token 计数字段，不估算 token 或费用。

现有 `AgentBuilder.add_node(id, execute)` 和声明式配置保持可用；`calls=` 是可选关键字参数。无需修改旧应用就能继续运行，但低层直接调用不会因此自动受到 Harness 管理。本阶段只为 Python 自定义节点增加公开配置，不新增 JSON 中任意 Python 代码的加载能力。

内置工作台已将 `text_stats` 调用迁移到公开受管入口，正常运行现在也会出现 `tool.requested` / `tool.completed` 审计。Token bus 在同一运行的下一个模型节点开始时重新打开并保留前序历史，避免第二个节点的 token 被已关闭的总线丢弃。

## 验证与限制

专项用例位于 `tests/test_managed_calls.py`，覆盖权限与暴露规则、额度和重试、停止结果不可吞掉、审批跨重启恢复、结果不明与结果复用、取消、上下文裁剪、usage、流式输出和节点退出后的调用失效。HTTP 自定义工具审批续跑由 `tests/test_server.py` 验证。

首版是同步接口，每次节点访问只有一个受管逻辑调用。多调用程序需拆成图；本阶段不建设调用序列日志、跨进程事务或任意 Python 的执行恢复。模型回复和非审批工具结果的 checkpoint 重放限制继续按 M8 处理。应用在调用前的本地计算以及调用后的结果处理也必须遵守其自身的幂等和副作用约定。

## 最终验证

2026-11-08 本机：`python -m pytest -q` 为 434 passed；`python -m unittest discover -s tests -q` 为 434 tests / OK；前端审批异步测试 6 项通过。`node --check frontend/app.js`、文档链接和 `git diff --check` 通过。`python -m examples.managed_calls` 返回 `COMPLETED` 并输出脚本模型标识。远端 CI 和真实模型质量未在本阶段验收。
