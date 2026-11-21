# Harness M4 阶段开发文档

状态：已验收。等待契约、持久化决定、拒绝终态、批准后显式恢复、重启端到端恢复、人工核对路径、同一运行多次审批与工作台审批入口均已交付。

## 本轮交付

- Runtime、NodeResult 和生命周期新增独立 `WAITING_APPROVAL` 状态；等待不是 `BLOCKED` 或 `FAILED`。
- `NodeResult.waiting_approval()` 只保留请求 ID、工具名和参数 SHA-256 摘要，不持久化参数值。
- `agent_loop` 在调用 `requires_approval` 工具前验证暴露范围及能力授权，生成批准请求并停止；工具函数不执行。
- 等待请求写入 RunResult 和 RunStore，同一节点 checkpoint 持久化；重启后可查询状态与请求。
- HTTP 运行读取和状态接口返回等待状态。普通 `resume` 拒绝等待中的运行，防止绕过批准。
- Harness 审计记录 `tool.waiting_approval`；运行 Trace 记录 `node.waiting_approval` 和 `run.waiting_approval`。
- `FileApprovalDecisionStore` 将 `APPROVED`/`REJECTED` 决定绑定到当前等待请求；重复相同决定幂等，请求错配和冲突决定返回稳定原因码，重启后可读取。决定文件不进入运行列表。
- 本机服务提供 `POST /api/runs/{run_id}/approval-decision` 与对应 GET 查询。提交 `REJECTED` 会产生 `BLOCKED/APPROVAL_REJECTED` 终态，普通 `resume` 不可恢复；提交 `APPROVED` 仅持久化决定，返回 `execution_pending: true`，运行仍保持等待。
- 默认 `agent_loop` 在等待前写入服务端私有调用快照，保存已构造的模型消息、剩余工具调用和先前轮次。读取时重新核对首个工具的参数摘要；快照写入失败返回 `PENDING_INVOCATION_SAVE_FAILED`，不产生无法恢复的等待运行。公开运行结果仍只包含请求摘要。
- 私有快照新增 `PENDING -> STARTED -> COMPLETED` 执行状态。`claim_execution()` 仅接受与快照匹配的批准决定，并在工具执行前原子写入 `STARTED`；重启后再次领取返回 `TOOL_OUTCOME_UNKNOWN`，防止静默重放。`record_result()` 保存已完成或失败的工具结果；已完成的调用不能再次领取。
- `ToolRegistry.invoke_approved()` 从持久化决定读取批准事实，核对工具名和参数摘要，先领取 `STARTED`，再调用工具并保存结果。Harness 工具流水线可通过 `approval_decisions` 参数使用该入口，继续执行暴露策略、能力与预算检查，并记录工具结果审计。声明写入失败时不调用工具；结果写入失败时保留 `STARTED`，后续调用被阻断。
- 默认模型工具循环新增 `resume_pending()`：从私有快照中的模型消息、剩余调用和已完成轮次续跑，首个调用走已批准工具入口，不重新询问模型；若首个结果已经落盘，则复用该结果而不重复工具调用。恢复时重建模型与工具预算；`STARTED` 直接返回 `TOOL_OUTCOME_UNKNOWN`。
- `agent_loop` 节点在执行时检测本节点的活动快照（`active_for_node`），存在即走 `resume_pending` 续跑而不是重新询问模型；未接入决定存储时保持 `APPROVAL_REQUIRED` 阻断。快照损坏返回 `PENDING_INVOCATION_INVALID`。
- `Runtime.resume_approved(graph, checkpoint, decisions=...)` 是批准后的唯一显式恢复入口：核对运行结果中的等待请求与持久化 `APPROVED` 决定（请求 ID、工具名、参数摘要）一致后，生命周期经 `run.resumed(APPROVED)` 从 `WAITING_APPROVAL` 回到 `RUNNING` 并从检查点续跑。普通 `resume` 仍然拒绝等待中的运行。
- HTTP `POST /api/runs/{run_id}/resume` 接受等待中的运行：存在匹配的 `APPROVED` 决定时走显式恢复（等待运行不接受 `state_updates`），否则保持 `409 NOT_RESUMABLE`。GET 运行对已批准的等待运行报告 `resumable: true`。
- 重启验收：服务重启后（新 Runtime、重建注册表、同一存储目录），等待运行可查询、可批准、可恢复，副作用工具跨重启恰好执行一次；快照停在 `STARTED` 时恢复返回 `BLOCKED/TOOL_OUTCOME_UNKNOWN`，绝不静默重放。
- `POST /api/runs/{run_id}/approval-resolution` 提供 STARTED 不明结果的人工核对路径：操作者在外部系统核实后提交 `COMPLETED`（附输出）或 `FAILED`（附原因码），快照转为 `COMPLETED` 并标记 `resolved_by: OPERATOR`，写入 `tool.outcome_resolved` 审计；之后的恢复复用核对结果，不重复调用工具。PENDING/COMPLETED 快照不可核对。
- 决定存储升级为每运行多条（文件 schema v2，兼容读取 v1 单决定文件）：每个等待请求绑定一条不可变决定；`load_for_request` 按请求查询，重复决定幂等，冲突决定报错。私有快照同样升级为每运行多条，按请求 ID 追加，`active_for_node` 优先返回未完成快照。同一运行可以顺序经历多次等待-批准-恢复。
- 工作台新增审批入口：等待中的运行显示批准面板（工具名、请求 ID、参数摘要，不含参数值），支持"批准并继续"（先持久化决定再显式恢复）与"拒绝"（进入不可恢复阻断终态）；`TOOL_OUTCOME_UNKNOWN` 的运行显示人工核对表单。等待、待审批状态在时间线与状态标签中单独呈现。

## 后续边界

HTTP 决定与核对入口仅限本机开发使用，尚无宿主应用身份认证；私有快照按本地文件保存，尚无加密或保留期管理。副作用工具的"恰好一次"由开始执行前的持久化领取保证，不覆盖外部系统自身的重复去重需求。
