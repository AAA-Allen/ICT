# Harness M1 阶段开发文档

状态：本轮实现完成并通过测试

## 阶段目标

为模型调用和工具调用建立独立的追加式事实底座。Runtime 继续保存节点和运行级 `TraceEvent`；Harness 审计记录保存一次调用的请求、结果、失败原因和调用关联关系。

## 本轮交付

- `HarnessEvent`：包含 `run_id`、连续序号、事件类型、状态、原因码、节点/执行 ID、调用 ID 和安全摘要。
- `HarnessAudit`：按运行查询事件，并在写入失败时返回稳定的 `AUDIT_WRITE_FAILED` 原因。
- `InMemoryHarnessAuditStore`：测试和本地短生命周期使用。
- `FileHarnessAuditStore`：按运行 ID 原子写入 JSON，进程重启后可读取。
- 模型流水线记录 `model.requested`、`model.completed`、`model.failed` 和 `model.rejected`。
- 工具流水线记录 `tool.requested`、`tool.completed`、`tool.blocked`、`tool.failed` 和 `tool.rejected`。
- `AgentBuilder`、声明式配置装配和 `agent_loop` 支持注入审计器；未注入时保持现有行为。
- HTTP 提供 `GET /api/runs/{run_id}/harness-events` 查询入口；未知运行返回 `UNKNOWN_RUN`。

## 设计选择

1. 事件与 Runtime Trace 分层：Runtime 负责节点生命周期，Harness 负责模型/工具调用事实，避免把领域调用细节塞进 Graph Runtime。
2. 同一 `call_id` 关联一次调用的请求与终态事件；`sequence` 保证同一 `run_id` 的恢复和查询顺序稳定。
3. 只写入摘要字段，例如消息数量、工具名、参数键和输出键；默认不保存完整提示词、参数值或工具输出，降低敏感信息进入审计的风险。
4. 审计写入失败会阻止模型调用，或让工具调用以 `AUDIT_WRITE_FAILED` 失败，避免静默丢失事实。

## 验收结果

- 模型和工具成功调用的事件顺序及调用 ID 可查询。
- 文件存储重新实例化后仍能读取事件。
- 审计存储故障产生稳定原因码。
- M0 与原有 Agent loop 测试保持通过。

## 当前边界

- 审计器目前按单次运行追加事件，但预算仍是单次 `agent_loop` 节点范围。
- 工作台尚未展示 Harness 事件；当前先提供稳定 HTTP 查询端点，前端展示作为后续增量。
- 尚未实现 checkpoint 恢复时的跨节点预算重建、人工批准、上下文裁剪、重试和幂等。
