# Harness M7：普通工具节点接入统一执行入口

普通声明式 `tool` 节点通过 `ToolInvocationPipeline` 调用工具，共用能力检查、工具暴露策略、审计和运行级预算。该节点不需要配置模型。

## 配置

```json
{
  "name": "explicit-tool-agent",
  "entry": "read",
  "harness": {"max_model_calls": 1, "max_tool_calls": 2},
  "nodes": [{
    "id": "read",
    "type": "tool",
    "params": {
      "name": "read_record",
      "arguments": {"id": "{record_id}"},
      "output_key": "record",
      "max_tool_calls": 2,
      "tool_max_retries": 1,
      "tool_retry_backoff_seconds": 0
    }
  }]
}
```

应用须通过 `ToolRegistry` 注册 `read_record`，如需自动重试应声明 `read_only=True`。工具的参数、输出结构和副作用仍由应用负责。启用运行级 `harness` 时须注入持久化审计，要求与 M6 相同。

`max_tool_calls` 默认为 1，限制本次节点执行的实际尝试次数；`tool_max_retries` 默认为 0。重试同时消耗节点和运行额度。副作用工具仅在提供应用侧幂等契约时允许重试，需要审批的工具不自动重试。

## 审批与恢复

需要审批的工具由原来的 `BLOCKED/TOOL_APPROVAL_REQUIRED` 变为 `WAITING_APPROVAL`。能力检查失败仍直接阻断，不产生可执行审批请求。审计失败也会阻止执行。

服务端的既有审批、拒绝、核对和 `/resume` 入口可直接使用；Python 接入方须提供 `FilePendingInvocationStore`、`FileApprovalDecisionStore`、`RunStore` 和 checkpoint 存储，并通过 `Runtime.resume_approved()` 恢复。

私有快照保存已解析参数、输出键、路由及可选 `state_version`。恢复时核对当前配置和批准决定；参数、工具、输出键或路由变化返回 `APPROVAL_REQUEST_MISMATCH`。相同状态版本的节点重放复用已落盘的工具结果，不重复执行；图循环进入新的状态版本时重新申请批准。`STARTED` 且结果未落盘返回 `TOOL_OUTCOME_UNKNOWN`，须人工核对。

旧模型循环快照缺少 `state_version` 时仍可按原流程读取；普通工具节点不把旧模型循环快照当作自己的审批事实。

## 验证与边界

`tests/test_tool_node_harness.py` 覆盖调用审计脱敏、审计写入失败、能力拒绝、跨节点预算和重启重建、有限重试、审批重启续跑、参数错配、结果不明、同边界结果复用及后续访问重新审批。

运行额度仍仅覆盖 Harness 发起的调用。自定义 Python 节点直接调用模型或注册表、基础 `tool_step` / `graph_tool_node` 接口、异步与跨进程执行不自动纳入该预算。非审批工具在 checkpoint 之前发生的副作用仍依赖应用侧幂等契约，不提供外部副作用恰好一次保证。
