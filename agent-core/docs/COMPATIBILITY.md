# 公开契约与兼容说明

适用范围：当前 0.4.0 源码及尚未发布的 M8–M12 增量。`0.x` 次版本可能包含破坏性调整；当前版本号并不代表这些增量已经公开发布。安装与交付状态见 [M11](HARNESS_M11.md)，使用入口见 [README](../README.md)。

## Python 与配置接口

- `agent_core` 是完整运行框架；`agent_lite` 是独立的对话外壳，两者的 Agent 类型不互换。公开导出见各包的 `__all__`。
- 自定义节点通过 `AgentBuilder.add_node(..., calls=NodeCallPolicy(...))` 装配受管调用；使用 `ctx.calls.model(messages)` / `ctx.calls.tool(name, arguments)`。模型返回映射，工具返回输出映射；失败或等待通过 `ManagedCallStopped` 交回运行时。
- 首版每个自定义节点仅允许一次逻辑受管调用，重试属于该调用的尝试；多步业务拆成图节点。权限、暴露工具和额度由装配策略决定，调用代码不能临时扩大策略。
- `ctx.model`、`ctx.tools` 及基础工具适配函数保留低层入口。直接调用不自动具有完整 Harness 审计、审批和预算约束；运行时不拦截任意 Python 外部操作。
- 声明式 `llm`、`agent_loop`、`tool` 与自定义受管调用共用流水线。M7 之后普通工具节点开始消耗配置的运行额度，应用应按实际调用路径配置预算。
- 节点返回 `NodeResult`，运行状态使用 `RunStatus`。`RunResult.stopped_at_node` 是当前字段；Python `stopped_at` 保留弃用别名，持久化兼容读取旧字段，HTTP 使用新字段。

## 恢复与 HTTP 行为

运行完成、失败、阻断、取消和等待批准分别为 COMPLETED、FAILED、BLOCKED、CANCELED、WAITING_APPROVAL。HTTP 200 表示请求已处理，调用方仍须检查 `run.status` 和 `reason_code`。异步提交返回 202，随后查询运行。

`resumable` 是基于当前持久事实的恢复预检结果，不保证下一次恢复一定成功。运行、checkpoint、装配图和审批身份必须匹配；恢复只能显式发起。检查点以节点边界保存，不能续接任意 Python 函数的中间位置。

| 原因码 / 状态 | 调用方应理解的事实 |
| --- | --- |
| MODEL_NOT_CONFIGURED | 模型配置缺失，没有脚本模型兜底 |
| CAPABILITY_DENIED | 工具所需能力未授予，不能通过恢复参数扩大权限 |
| WAITING_APPROVAL | 待决定；批准本身不执行，决定后需显式恢复 |
| APPROVAL_REJECTED | 审批已拒绝，普通恢复不能绕过 |
| APPROVAL_VISIT_UNKNOWN | 历史审批缺少可信节点访问版本，不能猜测恢复身份 |
| APPROVAL_REQUEST_MISMATCH | 工具、参数或节点访问身份不匹配 |
| TOOL_OUTCOME_UNKNOWN | 外部工具可能已执行，需要核对；不要自动重发 |
| SUBAGENT_OUTCOME_UNKNOWN | 子运行结果缺失，不能自动重复委派 |
| NOT_RESUMABLE | 当前事实不允许直接恢复，应检查查询结果与持久证据 |

M8 调整了未知调用结果的服务行为：预检拒绝的 `/resume` 返回 HTTP 409 及具体原因码，不再启动一次随后阻断的恢复，也不新增恢复生命周期事件。旧客户端应处理 409，不能只等待一个 BLOCKED 运行结果。

审批身份由 run_id、node_id、state_version 和 request_id 关联；新的节点访问必须重新批准。自定义审批存储须实现 `for_node_visit(run_id, node_id, state_version)`，见 [M8](HARNESS_M8.md)。自定义存储实现者在升级前需要补齐此接口。

工具已开始且结果不明、预算存在未结算占用或缺少可信 checkpoint 时停止自动恢复。非审批工具和模型的完整回复没有通用持久重放保证；节点提交前故障后，已结算的调用可能再次发起并消耗额度。非审批副作用工具的跨恢复幂等由应用负责，不能据此承诺外部操作恰好一次。

## 流式兼容

M12 新增 token cursor、SSE id 和 gap 事件，支持 Last-Event-ID 与初始 cursor query。TokenBus.history 的 after 改为绝对序号，三元组返回形状保留；需要缺口信息的调用方迁移至 read。内存历史不跨服务重启持久化，详见 [M12](HARNESS_M12.md)。

## 持久化格式

M13-03 为 Tool 增加可选 approval_description/approval_fields；审批请求可带经白名单生成的 display，历史无说明仍可读。RunResult schema 主版本不变，新增内容为可选字段。HTTP 运行响应新增 recovery（allowed、reason_code、detail），与 resumable 一起从资格检查生成；未知字段可由旧客户端忽略。它不改变审批身份或自动授权恢复。

M12 后续增量为 TokenBus 加入保留期、缓存运行数和 payload 字节上限；Runtime 退出才将节点间受保护的流设为可回收。缓存过期或容量淘汰可能早于晚订阅，客户端须处理 gap。活动流无法接纳时返回 STREAM_CAPACITY_EXCEEDED，单事件超限为 STREAM_EVENT_TOO_LARGE。内置 HTTP 模型各响应路径超限统一为 MODEL_RESPONSE_TOO_LARGE，配置及默认值见 [M12](HARNESS_M12.md)。这些是有限资源下的新失败边界，没有持久格式升级。

| 记录 | 当前写入格式 | 兼容边界 |
| --- | --- | --- |
| RunStore 运行结果、独立生命周期事件 | schema 1 | 旧结果缺少 lifecycle_events 时按空事件读取；缺少 schema_version 时按 1 读取 |
| Checkpoint | schema 1 | 校验 graph_id、状态版本与下一节点；未知主版本拒绝 |
| Harness 调用审计 | schema 1 | 事件序列和 run_id 必须匹配；未知主版本拒绝 |
| 审批决定文件 | schema 2，内部决定 schema 1 | 可读取旧 schema 1 单决定文件；新格式保存一运行多决定 |
| 审批私有快照文件 | schema 2，内部调用 schema 1 | 可读取旧单调用文件；缺少 state_version 可查询但高层不自动恢复 |
| 服务端会话记忆 | schema 1 | 校验 session_id 和 values，损坏记录不按空记忆覆盖 |
| M10 验收报告 | schema 2 | 错误行保留调用事实；证据缺失可使完成回复数、额度或副作用次数为 null；旧报告保持原样，不自动转换 |

升级前停止使用该运行目录的服务，并备份运行目录、checkpoint 目录及审批私有目录。保留原有配置和版本用于复查。不应手工删除 STARTED、未结算占用或审批身份字段以强行恢复；需要应用核对真实外部结果。

## 单机与凭证边界

服务采用同步 Python 执行、单进程文件存储。同一存储目录通过 OS 锁限制单服务实例；活动运行保护不等同于跨机器协调。超时和取消是协作式边界，不能安全强制中断任意同步函数。

可选审批 Bearer token 只保护批准、结果核对和恢复的写入入口；读取和其他 API 没有完整用户认证，多租户、操作系统工具沙箱和分布式执行未提供。M13-04 工作台可在设置中使用仅驻留当前页面内存的 token；浏览器输入接受 1–4096 个不含空白的 ASCII 字符。凭证只随同源三个写入入口发送，禁止跟随重定向；刷新或离开页面即清除。HTTP 401 显示凭证问题并读回运行状态，不自动重试写入或将任务记为执行失败。GET /api/health 新增布尔字段 approval_auth_required，不返回 token；旧服务未提供时显示未知并由实际写入响应判断。详见 README 的服务说明。

模型配置从服务端 `AGENT_MODEL_BASE_URL`、`AGENT_MODEL_API_KEY`、`AGENT_MODEL_NAME` 读取。模型任务效果须由 M10 实际报告支撑；离线 HTTP 替身和 wheel 安装检查仅证明相应执行契约。
