# Harness M6 运行级预算与恢复语义

状态：运行内模型/工具调用额度、保守恢复、同步子 Agent 额度继承及持久委派重放已实现。

## 配置

声明式 Agent 可选配置：

```json
"harness": {"max_model_calls": 8, "max_tool_calls": 16}
```

两项额度均为正整数；启用时必须提供持久化的 `HarnessAudit`。内置 `FileHarnessAuditStore` 满足单进程部署的这一要求，`InMemoryHarnessAuditStore` 会在装配时被拒绝。自定义存储需显式声明 `durable = True` 并保证持久化。Python 装配入口为 `AgentBuilder(..., run_limits=HarnessLimits(...), audit=audit)`。未配置时沿用节点级预算，不改变既有行为。

## 执行与恢复

- 同一 `run_id` 中的 `llm` 与 `agent_loop` 节点共享运行额度；模型和工具重试每次实际尝试各占一次。节点级额度继续生效，先检查节点额度，再占运行额度。运行额度耗尽返回 `MAX_MODEL_CALLS` 或 `MAX_TOOL_CALLS`，不进入外部调用。
- 调用前写入 `run_budget.claimed`，调用结果写入审计后写入 `run_budget.settled`。恢复时从审计重建已占额度；checkpoint 不会重置额度。配置额度改变返回 `RUN_BUDGET_LIMIT_MISMATCH`。
- 占用尚未结算时，模型返回 `MODEL_OUTCOME_UNKNOWN`、工具返回 `TOOL_OUTCOME_UNKNOWN`，停止自动调用。即使外部请求实际已完成，只要响应或结算事实未可靠落盘，也不能把它当成新请求重发。
- 审计读取或写入失败返回明确原因码并阻止调用。文件审计存储适用于单进程部署；跨进程并发占用需要提供支持原子追加或事务的存储实现。
- 同步 `subagent` 调用继承父运行预算，子运行的模型与工具尝试均占父额度。父预算审计中的占用事件记录 `source_run_id`，可区分父、子调用。委派节点从 checkpoint 续跑时仍从父运行审计恢复额度。子 Agent 单独启动时使用自己的预算配置；嵌套委派期间父预算优先。
- 父审计与子运行结果存储均持久化时，`subagent` 在执行前记录稳定的子运行 ID、目标 Agent 和参数摘要。父节点重放时，已落盘的子结果直接复用，不再调用子 Agent；请求错配返回 `SUBAGENT_REQUEST_MISMATCH`，子结果缺失返回 `SUBAGENT_OUTCOME_UNKNOWN`。审计写入失败阻止启动子 Agent。启用运行级预算而缺少任一持久存储时返回 `SUBAGENT_PERSISTENCE_REQUIRED`。

## 边界

M7 更新：普通 `tool` 节点现已接入 Harness 及运行级预算，详见 [HARNESS_M7.md](HARNESS_M7.md)。以下保留 M6 初始交付范围；应用直接调用模型或工具注册表仍不自动纳入预算。

运行额度只约束通过 Harness 流水线发起的模型与工具调用。普通 `tool` 节点使用基础层工具入口，不在本阶段的运行额度内。预算继承仅覆盖同一进程内的同步委派链；异步、跨进程子 Agent 仍需显式运行族标识与跨运行原子占用协议。checkpoint 只保存节点边界状态，不保存尚未提交的模型回复，因此未结算调用须由应用核对后人工处理。子运行结果未落盘时无法判定副作用是否已经发生，必须人工核对；不提供恰好一次的外部副作用保证。没有持久父审计时，旧的内存委派行为保持不变，重放不具备去重保证。
