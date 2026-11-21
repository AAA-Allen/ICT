# Harness 层开发边界

完整需求、里程碑和验收标准见 [docs/HARNESS_PRD.md](docs/HARNESS_PRD.md)；架构与执行设计见 [docs/HARNESS_DESIGN.md](docs/HARNESS_DESIGN.md)；阶段文档见 [docs/HARNESS_M0.md](docs/HARNESS_M0.md) 和 [docs/HARNESS_M1.md](docs/HARNESS_M1.md)。

起点：`4f52a06`（基础运行底座收口）。从此提交之后，新能力优先放在模型与工具执行的 harness 层；Graph Runtime 继续负责节点调度、状态提交、生命周期、Trace 和 checkpoint。

本项目始终定位为跨领域 Agent 框架。Harness 负责通用能力装配和执行约束；领域提示词、工具实现、数据与业务规则由应用接入。

## 职责

Harness 组合模型、工具、会话、上下文、Agent loop 与执行策略，并负责预算、工具可见性与授权、人工批准点，以及调用过程的事实记录。业务工具仍由应用注册，Harness 不推断工具副作用是否成功。

## 第一阶段：执行接口与调用预算

`ModelToolLoop` 定义可替换的模型/工具驱动接口，`DefaultModelToolLoop` 是当前默认实现。`ModelInvocationPipeline` 和 `ToolInvocationPipeline` 分别是模型与工具调用的统一入口，当前负责预算与既有注册表门禁，后续才加入审计和批准。`HarnessLimits` 定义每个 `agent_loop` 节点执行的模型调用和工具调用上限。`HarnessBudget` 在调用前计数；配置参数 `max_rounds` 默认为 4，`max_tool_calls` 默认为 16。模型轮次耗尽返回 `MAX_TOOL_ROUNDS`，工具次数耗尽返回 `MAX_TOOL_CALLS`。两个预算均为单次节点执行范围，恢复后的新执行会重新计数。

## M1：调用事实底座

`HarnessAudit`、`HarnessEvent` 以及内存/文件存储已接入模型和工具流水线。每次调用有稳定的 `call_id`，同一运行按 `sequence` 追加；默认只保存调用摘要，审计写入失败会以 `AUDIT_WRITE_FAILED` 阻止或失败调用。可通过 `AgentBuilder(audit=...)` 或 `build_agent_from_config(..., audit=...)` 注入；服务端可通过 `GET /api/runs/{run_id}/harness-events` 查询。

## M2：工具流水线与策略

`Tool` 支持 `read_only`、`side_effect`、`requires_approval` 元数据；`DefaultToolPolicy` 确保模型只能调用当前节点声明的工具 schema。未暴露工具返回 `TOOL_NOT_EXPOSED`，需要批准的副作用工具返回 `TOOL_APPROVAL_REQUIRED`，策略异常返回 `TOOL_POLICY_FAILED`，均不会执行工具函数。

## 后续阶段

## M3：上下文装配

`ContextAssembler` 在模型调用前按固定规则裁剪完整用户轮次；`agent_loop` 和 `llm` 可配置 `max_context_messages` 和 `max_context_chars`，保留系统指令与当前轮次。超限返回 `CONTEXT_LIMIT_EXCEEDED`；适配器提供的真实 token usage 写入模型调用审计。

## M4：等待批准

`requires_approval` 工具在能力检查后生成不含参数值的请求摘要，运行进入 `WAITING_APPROVAL`，请求、Trace 和 checkpoint 可查询。本机服务可提交决定；拒绝后进入不可恢复的阻断终态。批准决定持久化后，恢复仍需显式触发（HTTP `/resume` 或 `Runtime.resume_approved`），从私有快照续跑已批准的工具调用与后续模型轮次，副作用工具在开始执行前领取 `STARTED` 声明、结束后落盘结果，保证跨重启恰好执行一次；`STARTED` 不明结果必须人工核对后才能继续。同一运行支持多次顺序审批。普通 `resume` 不能跳过等待状态。工作台提供批准/拒绝/人工核对入口。详见 [docs/HARNESS_M4.md](docs/HARNESS_M4.md)。

1. 将每次模型决策、工具调用与失败尝试写入可查询的运行审计，即使节点最终失败也保留事实。
2. 提供需要人工批准的工具调用暂停与恢复契约，并将批准结果写入审计。
3. 增加上下文长度管理和可配置的模型请求预算；计量必须来自适配器返回的真实 usage，不能猜测。
4. 定义重试和幂等规则；默认不重试有副作用的工具。

这些能力逐项落地，不改变 Graph Runtime 对成功状态更新、失败阻断和 checkpoint 的现有语义。
