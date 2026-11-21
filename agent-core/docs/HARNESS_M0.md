# Harness M0 阶段开发文档

状态：代码与单元测试完成，待提交  
基线：`4f52a06`  
约束：所有实现必须建立在现有 `agent_core` 的 Agent、NodeResult、ToolRegistry、Runtime 和声明式节点装配之上。

## 阶段目标

把现有 `agent_loop` 中的模型循环和工具调用拆出最小、可替换的 Harness 接口，同时保持现有 JSON 配置和运行结果契约兼容。

## 本阶段交付

1. `ModelToolLoop`：模型/工具驱动循环的可替换协议。
2. `DefaultModelToolLoop`：当前默认循环实现，保留现有工具调用消息格式和 `*_rounds` 审计状态。
3. `ModelInvocationPipeline`：模型调用前统一占用模型轮次预算。
4. `ToolInvocationPipeline`：工具调用前统一占用工具预算，并继续通过 `ToolRegistry` 做能力检查、参数快照和结果校验。
5. `HarnessLimits` / `HarnessBudget`：单次 `agent_loop` 节点执行的模型与工具调用上限。
6. `tests/test_harness.py`：组件级契约测试；`tests/test_nodes.py` 继续覆盖声明式 Agent 的端到端行为。

## 非目标

- 不实现持久化调用事件、人工批准、上下文裁剪、重试或幂等。
- 不修改 Graph Runtime 的状态提交、生命周期和 checkpoint 语义。
- 不引入插件市场、动态卸载或新的领域业务接口。
- 不把单节点预算描述成整次运行或恢复后的累计预算。

## 实现约束

- 所有非成功结果继续使用 `NodeResult` 和稳定 `reason_code`。
- 模型和工具调用都必须通过 Harness 入口；默认循环不得直接绕过 `ToolRegistry`。
- 工具预算在工具函数执行前占用；模型预算在模型适配器调用前占用。
- 自定义循环只能使用传入的模型调用函数和 `ToolInvocationPipeline`，不能依赖具体领域工具实现。
- 原有 `agent_loop` 参数 `max_rounds` 保持兼容；新增 `max_tool_calls` 默认值为 16。

## 测试矩阵

| 范围 | 必测行为 |
| --- | --- |
| `HarnessLimits` | 正数校验；拒绝零、负数、布尔值和非整数。 |
| `HarnessBudget` | 模型/工具预算边界；超限后计数不继续增长。 |
| 模型入口 | 调用前占用预算；超限返回 `MAX_TOOL_ROUNDS`；模型异常透传稳定原因。 |
| 工具入口 | 调用前占用预算；超限不执行工具；未知工具、能力拒绝和工具异常保持原因码。 |
| 默认循环 | 工具调用后继续模型；最终答案写入 `NodeResult`；消息格式和轮次审计保持兼容。 |
| 可替换循环 | 自定义实现可通过 `agent_loop_node(..., loop=...)` 接入，不要求修改 Runtime。 |
| 声明式 Agent | `max_tool_calls` 参数校验；原有成功、阻断、失败和最大轮次语义不变。 |

## 完成标准

- `tests/test_harness.py` 组件测试全部通过。
- 现有 `tests/test_nodes.py` 中 Agent loop 测试全部通过。
- 未授权、未知和超预算工具调用不会进入工具函数。
- 文档明确当前预算作用域和未实现能力。
- 完成后再提交 M0；在测试通过前不标记 M0 完成。

## 本轮验证结果

- `python -m unittest tests.test_harness tests.test_nodes.AgentLoopTests -v`：18 passed。
- `python -m unittest discover -s tests -q`：298 passed。
- `git diff --check`：通过。

代码尚未提交，因此 M0 仍处于待提交状态。

## 下一阶段入口

M1 先设计追加式调用事实和模型历史投影，再把 `ModelInvocationPipeline` 与 `ToolInvocationPipeline` 接入审计。M1 不应直接在默认循环中增加散落的日志写入；事件模型和持久化边界必须先定稿。
