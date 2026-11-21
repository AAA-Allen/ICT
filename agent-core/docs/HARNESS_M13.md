# Harness M13：工具型参考 Agent 与工作台闭环

状态：M13-01 至 M13-05 的本机开发与离线浏览器验收已完成。参考应用复用既有 agent_loop、受管调用、审批与持久恢复；工具调用适配器已补齐服务端 usage 的保留。真实模型效果、远端 CI 与发布门禁另行验收。

## 应用流程

模型只能选择 list_documents/read_document，只读能力为 documents.read；它根据工具返回的资料、SHA-256 和原文提出 JSON 草稿。后续节点检查输出名、结构、引文和内容哈希，通过后才进入独立 save_draft 工具等待批准。批准后按已保存参数执行，写入前再次核对来源；拒绝不写文件。

图为 begin → review(agent_loop) → validate(ctx.calls.tool) → save(ctx.calls.tool，需批准) → finish。运行模型额度为 6、工具额度为 10，模型循环自身最多 6 次模型尝试和 8 次工具尝试，无自动重试。应用提示词与文件工具不进入 agent_core。

离线模型是显式脚本：读取工具结果后提出固定待办，仅验证接线和治理，不代表理解能力。真实模型可自行选择只读调用次序；结构合法、引用存在也不证明摘要和待办正确，必须人工复查。

## 从命令行执行

仓库根目录执行以下命令；默认样例资料为公开合成内容：

```powershell
python -m examples.reference_agent --workspace outputs/review-demo --run-id sample-review --filename review.md
```

结果应为 WAITING_APPROVAL / APPROVAL_REQUIRED。查看 `outputs/review-demo/reports/sample-review.json` 的 draft.preview、proposal 和 sha256，确认草稿后选择一项：

```powershell
python -m examples.reference_agent --workspace outputs/review-demo --action approve --run-id sample-review
python -m examples.reference_agent --workspace outputs/review-demo --action reject --run-id sample-review
```

approve/reject 是互斥决定，不应先后用于同一次运行。批准保存至 workspace/drafts/review.md，拒绝为 BLOCKED / APPROVAL_REJECTED。新任务使用新的 run_id 和输出文件名；既有草稿不会覆盖，返回 DRAFT_EXISTS。

每个 CLI 动作都会重新装配 Runtime 与存储；恢复使用原有 checkpoint 和审批记录。不会重新发起前序模型、列表、读取或校验工具调用；save_draft 会独立重读所引用证据进行一致性检查。CLI 用与服务一致的存储锁，服务运行时应通过 HTTP 决定/恢复，不能再用 CLI 操作同一状态目录。

报告是当前状态视图，每次动作更新该 run_id 的报告；实际执行事实以 state/runs 中的运行结果、生命周期、审计和 private_approvals 为准。报告保留完成调用数、草稿预览、保存结果与证据路径；不记录模型密钥、认证头或服务 URL。

## 工作台入口

```powershell
python -m examples.reference_agent --action serve --port 8001 --workspace outputs/review-workbench
```

打开 `http://127.0.0.1:8001/`，选择 document-review 并提交任务。只提供 task 也可运行，输出名由 run_id 派生；API 可显式提供 filename。该服务使用现有工作台，端口 8001 避免替换已有 8000 服务。

现有界面能够展示原有 Trace、待批准、拒绝与恢复；草稿的结构化内容也保存在运行 state.draft 中。调用事实面板、工具声明的审批说明、恢复资格和内存凭证已接通。M13-01 通过 HTTP 验证接线，M13-02 已做面板的浏览器检查，完整审批闭环的浏览器 smoke 仍待 M13-05。

## 真实模型入口

按 README 配置 AGENT_MODEL_BASE_URL、AGENT_MODEL_API_KEY、AGENT_MODEL_NAME 后，加 `--mode real` 执行 run 或 serve。后续 approve/reject 也应保持原模式。缺少配置时打印 NOT_RUN / MODEL_NOT_CONFIGURED 并退出 2，不创建工作区，不回退到脚本。

SDK 构建入口为 build_app(workspace, documents, model=..., mode=...)；服务入口为 create_reference_server。宿主注入自定义模型时须保证 mode 标记与实际提供方一致。应用状态保存实际装配模式和配置模型名，脚本结果标记 NOT_EVALUATED_SCRIPTED；真实回复仍标记需要人工复查，不自动判为质量通过。

## 文件和证据边界

- documents 与 workspace 必须互不包含。state、reports、drafts 位于 workspace 下，拒绝目录重定向。
- 仅支持平面 ASCII 文件名：字母/数字起始，后接字母、数字、下划线或连字符，stem 最多 64 字符，扩展名 .md。拒绝绝对路径、分隔符、父目录、ADS 和 Windows 保留设备名。
- 文档列表最多 32 个；单文档读取最多 65,536 字节，必须非空 UTF-8。只读候选文件必须为常规文件，拒绝符号链接、重解析文件和越出输入根的解析路径。
- 草稿包含限定长度的 summary、1..8 项 todos、1..8 条逐字引用与源 SHA-256。目标名必须与请求名一致；伪造引文返回 INVALID_CITATION，审批期间来源变化返回 SOURCE_CHANGED，均不生成草稿。
- 保存使用排他创建，不修改已有文件；框架仍按 STARTED/COMPLETED 记录工具事实。若在外部写入后、结果落盘前崩溃，保持 TOOL_OUTCOME_UNKNOWN，不能用重复执行来猜测结果。

这些是受控本机示例的应用路径检查，不是 OS 沙箱。资料与工作区目录应由宿主管理，未提供对敌对进程并发替换目录/文件的隔离；读 API 与其他 HTTP API 的认证边界仍与现有服务一致。样例不具备任意 shell、任意目录或生产工单写入能力。

## 验收与下一步

专项测试覆盖等待后重建恢复、拒绝、源变化、禁止覆盖、路径与设备名、未暴露写工具、伪造引文、大小限制、缺少真实配置，以及实际 HTTP 提交、服务重建和批准恢复。符号链接创建不可用的平台会将真实符号链接用例标为跳过，不冒充通过。

本机 M13-05 浏览器闭环已完成。后续按 [下一阶段计划](NEXT_PHASE_PLAN.md) 继续真实模型验收、远端 CI 与交付决策；没有配置的真实模型效果仍为 NOT_RUN。

## M13-02 调用事实面板

任务区、运行详情及服务端历史详情从现有 GET /api/runs/{run_id}/harness-events 读取，无新增执行日志或聚合端点。界面重绘和手动刷新均重新查询；不把审计放入 localStorage，也不在失败时用旧缓存或零替代未知。运行结果与审计为独立快照，不宣称跨请求原子一致。

- 模型请求记录、完成回复、工具请求记录、成功结果分别计数。一次待批准工具在恢复时可能新增请求记录，不能据此认定多次业务写入。
- 额外重试尝试按已记录 attempt>1 的节点/执行/调用身份去重；retry=true 只是计划，可能因取消而没有后续尝试，两者单独展示。
- 运行级额度只依据 run_budget.claimed/settled 记录，展示占用/上限与未结算数，不作为外部执行次数。未记录运行级预算时显示未知；限额冲突、孤立或重复结算显示记录不一致。
- usage 仅累计适配器确实返回的 prompt_tokens、completion_tokens、total_tokens，各字段显示覆盖回复数。部分缺失不补零，不推算 total_tokens 或费用；明确返回的零与缺失不同。
- 空审计、请求失败、结构异常均显示未知；非空的当前快照只说明“已记录”的事实，不保证未审计调用不存在。同步子运行的占用可能出现在父预算中，usage 不自动跨子运行合并。
- 展示序号、节点、事件、工具名、状态与原因码，不渲染参数全文或任意 summary 内容；文字经过转义。事件表展示末 200 条，统计基于完整响应。

面板校验运行身份和连续序列。异步结果只有在当前面板、当前运行快照及最新请求仍匹配时才应用；切换任务、进入日志或发起新刷新会阻止旧响应覆盖新面板。服务端详情每次重取结果，较早的详情请求不能覆盖后来打开的运行。

另修复 OpenAICompatibleModel.complete_with_tools 丢弃服务端 usage 的问题：工具请求与最终文本两种回复均保留原 usage；缺失为 {}，不补字段。非法 usage 结构返回 MODEL_INVALID_RESPONSE，真实计数经 Harness 原有 model_usage 过滤后进入审计。

## M13-03 审批公开字段与恢复资格

Tool 新增 approval_description（静态字符串，最多 500 字符）和 approval_fields（最多 8 个不同的顶层字段名 tuple）。仅需审批的工具可声明说明；字段必须对应原始参数中的字符串值，每项最多 200 字符，不支持嵌套或整对象公开。非法声明在装配时拒绝，调用时缺字段或值越界则返回 APPROVAL_DISPLAY_INVALID，不能执行工具。

Harness 从声明白名单生成 pending_approval.display，形状为 description/fields；随 RunResult 和审批等待审计持久化。NodeResult 仍丢弃其他任意请求字段。说明不改变 request_id、完整参数 argument_digest、批准校验或私有调用快照；服务端读取历史请求时不调用当前工具重新生成说明。旧请求缺少 display 时明确提示“未提供可公开动作说明”，保留原有手工核对与批准路径。

参考 save_draft 只公开 filename。摘要、待办、引文和来源目录不进入新的公开字段。元数据是宿主的展示声明，不替代工具权限、路径检查或业务规则，也不提供自动秘密识别；宿主不得将私有字段加入白名单。升级待批准工具时仍需保持原动作语义，说明不是工具代码版本证明。

运行响应保留 resumable，并新增 recovery={allowed, reason_code, detail}，二者从同一次服务端资格检查派生。既有检查点、私有快照、审批与未结算占用预检被复用；没有发起恢复、修改生命周期或结算额度。reason_code 对应既有拒绝码，detail 解释如 APPROVAL_PENDING、CHECKPOINT_MISSING 等情况；它不是新的恢复命令。

页面按服务端结构化事实显示解释，未知或旧服务未提供信息时不猜测；展示“允许尝试”不保证恢复成功，执行时仍校验配置、能力、额度及外部依赖。拒绝或结果不明时不会自动重发。旧请求的未知动作说明不会自动填充为当前工具描述。

本机 HTTP 预览验证使用独立的 8002 端口。8001 的重启命令被自动审批审查阻止，仅返回策略阻止，无进一步原因；原服务保留，新实例没有终止或替换它。8002 的参考请求已验证 display 仅含动作说明与 filename，recovery 为 NOT_RESUMABLE / APPROVAL_PENDING，未批准或执行保存。

## M13-04 页面内存审批凭证

GET /api/health 仅新增 approval_auth_required 布尔值。工作台“设置 → 审批凭证”接受不含 Bearer 前缀、无空白的 ASCII token（1–4096 字符），点击“本页使用”后清空输入框。凭证在单独闭包内使用，不放进任务对象、localStorage、sessionStorage、URL、运行记录或应用日志。未提交的输入在关闭设置时清空；刷新重建页面后没有凭证，pagehide 也会清除，防止往返缓存保留。此处不承诺对浏览器扩展、密码管理器或同源脚本的隔离。

仅为同源 POST /api/runs/{run_id}/approval-decision、/approval-resolution、/resume 添加 Authorization；读取、普通任务、会话操作、SSE 及外部 URL 不附带该凭证，敏感请求禁止跟随重定向。健康检查明确要求 token 而当前缺失时本地停止提交；旧服务未提供标记时显示未知，由写入响应判断。401 使用固定提示，不展示原始错误正文，清除被拒凭证；旧请求的 401 不会清除后来替换的凭证。设置后仍需用户手动重试，没有自动写入重发。

认证失败会读回服务端运行事实；已记录的批准或核对结果不会被当作执行失败。读回不可用时恢复原有本地状态，不伪造成功或终止；服务端仍在执行时继续观察。恢复编辑器保留已编辑内容，设置凭证后手动再次确认。批准/核对和恢复仍是独立请求，token 不改变审批身份、权限或恢复校验。

参考服务也支持 --approval-token-env，先通过服务端环境配置同一个 token，再执行（不要把密钥写进命令行或提交到仓库）：

```powershell
python -m examples.reference_agent --action serve --port 8003 --workspace outputs/review-auth --approval-token-env AGENT_CORE_APPROVAL_TOKEN
```

打开该服务的工作台，在设置中手动输入配置值。环境变量缺失、为空或全空白时启动失败，不创建工作区；该参数只适用于 serve。它不保护读取和其他 API，不扩展为完整用户认证。本轮使用临时本机 HTTP 集成测试验证保护，没有替换既有 8001/8002 实例。

## M13-05 浏览器闭环与离线资源

`tools/browser_smoke_server.py` 在独立工作区注册 document-review、recovery-router、scripted-stream 和原有 workbench。页面和 API 以 CSP 限制为同源资源。参考审阅与流式回答都由脚本模型给出；流式运行状态另标记 `model_kind=offline-scripted`、`model_quality=NOT_EVALUATED_SCRIPTED`，不能从这些结果推断真实模型质量。测试服务的固定 token `browser-smoke-only` 仅用于合成验收，不能用于实际部署。已有 8000/8001/8002 实例未受影响；本次本机演示位于独立的 8003 端口及 `outputs/browser-smoke-m13`。另开实例请选未占用端口与新工作区。

浏览器实际操作验证了：提交参考任务后得到待审批和公开动作说明；缺少或错误 token 均不保存；设置合成 token 后批准并显式恢复，生成一份草稿；独立请求拒绝后保持 BLOCKED / APPROVAL_REJECTED 且零写入；恢复路由补充 priority 后由 BLOCKED 变 COMPLETED；流式字符在运行中可见；运行中刷新后通过相同运行 ID 续读并得到最终结果；刷新后的服务端运行记录包含五条对应状态。审批和恢复的工具事实来自实际本机服务，模型内容均是脚本替身。窄屏 390×844 的提交按钮及设置入口也已检查；本轮浏览器证据见 [截图](../outputs/verification/m13-browser-smoke.png)。

前端移除了在线字体请求，保留系统字体回退；既有 Lucide 0.468.0 图标固定版本和上游 ISC 许可声明打进 wheel，静态服务从本地交付。资源与许可证哈希记录在 `frontend/vendor/README.md`。安装验证读取 wheel 内资源并由独立安装的 HTTP 服务返回相同字节。浏览器测试服务的 CSP 禁止外部资源，可用时仍正常显示图标和核心操作。

浏览器实测还暴露日志视图残留上一个任务的流式文本。现切入日志时清掉任务专属面板；运行中任务的后台更新不覆盖日志；较晚返回的旧日志请求不覆盖新页面或新查询。对应 Node 回归先复现失败，修复后通过。该视图修复不改变运行结果或审计记录。

## 本机验证记录

2026-11-08：完整 pytest 492 passed / 1 skipped；unittest 493 tests / OK，skipped=1；前端 Node 11 项通过。跳过项为当前 Windows 不允许创建真实符号链接，未将其标为已验证。专项还验证了写入已完成但 checkpoint 提交失败后，重建恢复复用已确认结果，文件不覆盖、模型不重复。

CLI 独立进程试跑：批准用例 COMPLETED，模型完成调用 3 次，list_documents/read_document/validate_draft/save_draft 各完成一次；拒绝用例 BLOCKED / APPROVAL_REJECTED，save_draft 完成数为零，未生成拒绝草稿。真实模式缺少配置为 NOT_RUN。HTTP 服务重建后的批准与恢复测试通过，源码语法及文档链接、git diff --check 通过。没有执行真实模型质量、远端 CI 或浏览器视觉验收。

M13-02：完整 pytest 494 passed / 1 skipped，unittest 495 tests / OK（skipped=1）；前端 22 项通过。新增覆盖未知与明确零、usage 字段覆盖率、占用与请求/结果的区分、取消重试、预算异常、HTML 转义、过期面板和详情请求。工具模型 usage 丢失的两项回归先复现失败，再修复通过。重新构建 wheel 的独立安装检查通过。

本机浏览器已验证一个无模型调用的已完成运行，以及 document-review 的实际待审批运行。后者显示模型请求/完成 3/3、工具请求/成功 4/3、模型占用 3/6、工具占用 3/10，usage 明确未知；未自动批准。窄屏布局检查截图为 outputs/verification/m13-audit.png。这是面板试跑，不代表 M13-05 的完整浏览器回归、外部模型质量或远端 CI 已完成。

M13-03：完整 pytest 500 passed / 1 skipped；unittest 501 tests / OK（skipped=1）；前端 25 项通过。新增验证白名单字段、嵌套或超长值拒绝、历史请求兼容、重启保留说明，以及恢复资格与实际 HTTP 拒绝码一致。重新构建 wheel 独立安装检查通过。8002 的实际 HTTP 请求验证到待审批，未执行保存；本轮没有浏览器视觉验收或外部模型质量验收。

M13-04：完整 pytest 503 passed / 1 skipped；前端 Node 39 项通过。新增覆盖凭证发送范围、缺失本地阻断、401 固定提示及清除、旧请求竞争、实际控件绑定、pagehide 清除、输入清空与刷新隔离、历史记录无凭证、批准/拒绝、恢复编辑内容保留及核对失败的状态回读。临时 HTTP 集成验证实际参考应用的无凭证/错凭证拒绝、批准后零写入、认证恢复后精确保存及持久记录无 token。重新构建 wheel 的独立安装检查通过。本轮未执行浏览器视觉验收、外部模型或远端 CI；M13-05 仍待完成。

M13-05：完整 pytest 504 passed / 1 skipped；前端 Node 46 项通过；最新 wheel 的独立安装检查通过，包含本地图标和许可文件。跳过项仍为本机 Windows 无法创建真实符号链接。浏览器操作覆盖批准、拒绝、恢复、两次流式任务、运行中刷新续读、服务端历史与窄屏布局。测试服务仅用脚本模型，真实模型验收与远端 CI 尚未运行。
