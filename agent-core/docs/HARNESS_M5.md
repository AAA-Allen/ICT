# Harness M5 阶段开发文档

状态：已完成阶段验收。模型与工具的有限重试、逐次审计和应用侧幂等契约已交付。

## 本轮交付

- `ModelRetryPolicy` 限定最多 5 次额外尝试，默认 0 次；退避基数可配置，每次退避最长 30 秒。
- `ModelInvocationPipeline` 仅对 `MODEL_TIMEOUT`、`MODEL_UNREACHABLE` 进行重试。每次实际请求消耗一次模型预算；额度耗尽时保留原始模型失败，不再等待或发起请求。
- 启用策略时，同一逻辑调用的 `model.requested`、`model.attempt_failed`、`model.attempt_completed` 和最终事件共用 `call_id`。失败 attempt 记录序号、原因码、是否继续重试、计划退避时长与错误类型；审计写入失败会阻止下一次尝试。
- 未配置策略时保留原有单次模型调用和审计事件序列；工具重试也默认关闭。
- `llm` 节点支持 `model_max_retries`（0 到 5，默认 0）和 `model_retry_backoff_seconds`（0 到 30，默认 0）。流式调用仅在尚未发出 token 时重试；已有 token 后的失败直接返回原始模型原因码，避免在同一流中混合不同尝试的输出。
- `agent_loop` 使用相同的可选重试配置。`max_rounds` 限制实际模型请求次数，重试也消耗额度；私有审批快照记录实际已消耗次数，重启续跑恢复该预算。旧快照没有此字段时继续按已有工具调用消息推算。
- `agent_loop` 新增 `tool_max_retries`（0 到 5，默认 0）和 `tool_retry_backoff_seconds`（0 到 30，默认 0）。只对 `TOOL_TIMEOUT`、`TOOL_UNREACHABLE` 重试；每次实际尝试消耗一次 `max_tool_calls`，同一逻辑调用共享审计 `call_id`。审批快照同时保存已消耗的工具尝试预算。
- 只读工具可以按策略重试。副作用工具必须显式提供 `ToolIdempotency(invoke, lookup)` 才能重试：Harness 在每次调用前按同一键查询外部结果，查询失败即停止；已经得到结果则复用。应用必须保证 `invoke(arguments, key)` 对同一键原子去重，并让 `lookup(key)` 返回已提交的结果或 `None`。键不写入公开审计。
- 未配置幂等契约的副作用工具至多执行一次；需审批工具继续使用 M4 一次性领取和人工核对，不自动重试。越权、参数错误、取消、预算耗尽及非瞬态错误不重试；审计写入失败也阻止下一次尝试。

应用侧契约示例：

```python
Tool(
    "write_record", ordinary_write, side_effect=True,
    idempotency=ToolIdempotency(
        invoke=lambda arguments, key: external_store.write_once(key, arguments),
        lookup=lambda key: external_store.find_result(key),
    ),
)
```

`write_once` 必须由外部存储原子地按键去重；`find_result` 返回已提交结果或 `None`，查询故障必须抛错。Harness 为同一次逻辑调用的各次尝试复用同一键，键包含运行 ID、节点 ID、调用 ID 和参数摘要。

## 验收与边界

单元测试覆盖重试上限、预算、审计顺序、结果核对、无幂等契约不重试及审批后预算恢复。重试只在当前进程内的同一次逻辑调用中进行；运行恢复后重新执行普通节点仍依赖宿主应用的外部幂等契约。跨节点、跨子 Agent 的总预算和重启期间未落盘模型调用的恢复语义不属于本阶段验收。
