# Harness M2 阶段开发文档

状态：本轮实现完成并通过测试

## 阶段目标

把工具暴露和调用前授权收敛到 Harness 入口，避免模型请求的工具集合与实际可调用工具不一致。

## 本轮交付

- `Tool` 增加通用元数据：`read_only`、`side_effect`、`requires_approval`。
- `ToolRegistry.describe()` 提供工具能力和风险元数据查询。
- `DefaultToolPolicy` 限制模型只能调用当前 `agent_loop` 声明的工具 schema。
- `agent_loop` 装配时拒绝重复工具名及非法的 object 参数 schema。
- 未暴露工具返回 `TOOL_NOT_EXPOSED`，不会进入注册表或业务函数。
- 需要批准的副作用工具返回 `TOOL_APPROVAL_REQUIRED`；人工批准等待状态属于后续 M4。
- 策略异常返回 `TOOL_POLICY_FAILED`，不执行工具。
- 所有策略阻断继续写入 Harness 审计事实。

## 兼容性

- `Tool` 原有前三个构造参数保持不变。
- 未声明新元数据的工具保持现有调用行为。
- 直接使用 `ToolRegistry` 的旧工作流继续只执行能力检查；`agent_loop` 经 Harness 统一执行暴露检查。

## 尚未完成

- 人工批准请求的持久化、等待和恢复（M4）。
- 更细粒度的应用策略组合和 HTTP 配置入口。
- 工具 schema 与注册表元数据的一致性校验。
