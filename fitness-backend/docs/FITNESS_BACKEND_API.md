# 深蹲业务后端 API（首轮本机版）

2026-11-19 更新：已完成真实算法与 agent-core 编排的 HTTP 联调。本文下方保留首轮接口说明；最新算法配置和复现方式以 [README](../README.md#2026-11-19-算法与-agent-core-联调) 为准。当前支持三种深蹲动作，`FITNESS_ALGORITHM_RUN` 可选；算法 `usable_candidate` 映射为业务 `completed`，`insufficient_pose` 映射为 `QUALITY_INSUFFICIENT`。`algorithm.results` 现包含质量、周期、特征和生物力学指标，原证据中的候选与未知状态保持不变。

## 启动

需要 Python 3.10+。在仓库根目录执行：

```powershell
pip install -e .
fitness-backend --port 8082
```

也可运行 `python -m fitness_backend.server --port 8082`。默认绑定 `127.0.0.1`，数据保存在 `outputs/fitness_backend/`（SQLite、视频和临时算法输出）；同一数据目录有进程锁，第二个服务实例会拒绝启动。重启时会清理中断留下的临时上传文件和临时算法 JSON，不删除已登记的视频。`GET http://127.0.0.1:8082/api/health` 返回算法和 Agent 的配置状态。本服务与 `agent-core-server` 分开启动，避免两套 `/api` 路由混淆。

当前无登录和正式鉴权。所有视频与任务请求都要求 `X-Session-Id`，用于绑定同一浏览器会话；**该值不是安全凭证**，不可把服务直接暴露到公网。浏览器跨域仅放行 `http://localhost:5173` 和 `http://127.0.0.1:5173`。

## 调用顺序

1. 前端为当前本机会话生成并保存稳定的 `X-Session-Id`。
2. `POST /api/videos` 上传 MP4 或 WebM 原始文件，取得 `video_id`；可用 `GET /api/videos/{video_id}` 核对服务端记录。
3. `POST /api/analyses` 提交 `video_id` 和 `exercise_id=normal_squat`，取得 `task_id`。
4. `GET /api/analyses/{task_id}` 查询状态与结果；建议在任务未终止时每 2 秒查询一次。刷新页面后可用 `GET /api/analyses` 找回同一会话的任务 ID。
5. 需要取消时调用 `POST /api/analyses/{task_id}/cancel`。

### 上传

请求体是文件原始二进制，**不是** `multipart/form-data`。`Content-Type` 只允许 `video/mp4`、`video/webm`；必须提供 `Content-Length`，上限 100 MiB。校验文件头签名，但暂未校验编码、时长和完整可解码性。上传空闲超过 15 秒或总耗时超过 180 秒返回 `408 UPLOAD_TIMEOUT`。`X-Filename` 可选。建议每次上传生成 `X-Upload-Key`，网络失败后用相同会话、相同键和相同文件重试；后端复用原 `video_id`。不同文件或类型使用同一键返回 `409 UPLOAD_KEY_CONFLICT`。不传键时每次上传生成新视频 ID。

```powershell
curl.exe -X POST http://127.0.0.1:8082/api/videos `
  -H "X-Session-Id: local-demo-1" -H "Content-Type: video/mp4" `
  -H "X-Filename: squat.mp4" -H "X-Upload-Key: upload-001" `
  --data-binary "@C:\path\squat.mp4"
```

首次成功为 `201`、`reused:false`；同内容重试返回 `200`、`reused:true`：

```json
{"video_id":"19f6294190334e57b1aa8ecc9c9aab61","size":1234567,"content_type":"video/mp4","sha256":"...","reused":false}
```

失败示例：`413` `BODY_SIZE_INVALID`、`415` `UNSUPPORTED_VIDEO_TYPE`、`400` `INVALID_VIDEO`、`INCOMPLETE_UPLOAD`、`INVALID_UPLOAD_KEY`，或 `409 UPLOAD_KEY_CONFLICT`。视频 ID 只在完整写盘并登记后返回；本地预览地址不代表上传成功。

`GET /api/videos/{video_id}` 需要相同的 `X-Session-Id`，返回文件名、类型、大小、SHA-256、创建时间和 `available`；不返回服务器磁盘路径。其他会话查不到该视频。已登记但文件丢失时 `available=false`，创建分析任务或使用原上传键重试均返回 `409 VIDEO_UNAVAILABLE`；重新上传需换一个键。

### 创建分析任务

```powershell
curl.exe -X POST http://127.0.0.1:8082/api/analyses `
  -H "X-Session-Id: local-demo-1" -H "Content-Type: application/json" `
  -d '{"video_id":"19f6294190334e57b1aa8ecc9c9aab61","exercise_id":"normal_squat","camera_view":"side","idempotency_key":"practice-001"}'
```

`camera_view` 可为 `front`、`side`、`oblique`、`front_or_oblique`、`unknown`，默认 `unknown`。首轮只允许 `normal_squat`。`idempotency_key` 可选；同一会话和键重复提交相同参数返回原任务，参数不同返回 `409 IDEMPOTENCY_CONFLICT`。不提供键则每次创建新任务。

新建成功为 `202`，返回 `task` 和 `reused:false`；复用返回 `200` 和 `reused:true`。任务可很快从 `QUEUED` 转为下一状态，因此创建响应只代表接受请求，后续以查询结果为准。

```json
{"task":{"task_id":"422f9e972f414c41bafab55c41794e20","video_id":"19f6294190334e57b1aa8ecc9c9aab61","exercise_id":"normal_squat","camera_view":"side","status":"QUEUED","reason_code":null,"algorithm":null,"agent":null,"agent_status":"NOT_AVAILABLE","agent_run_id":null,"agent_reason_code":null,"cancel_requested":false,"created_at":"2026-11-03 10:00:00","updated_at":"2026-11-03 10:00:00"},"reused":false}
```

失败示例：`404 VIDEO_NOT_FOUND`、`409 VIDEO_UNAVAILABLE`、`400 UNSUPPORTED_EXERCISE`、`400 INVALID_CAMERA_VIEW`。视频与任务均绑定 `X-Session-Id`；其他会话查询不到该视频或任务。

### 查询与取消

```powershell
curl.exe http://127.0.0.1:8082/api/analyses/422f9e972f414c41bafab55c41794e20 -H "X-Session-Id: local-demo-1"
curl.exe -X POST http://127.0.0.1:8082/api/analyses/422f9e972f414c41bafab55c41794e20/cancel -H "X-Session-Id: local-demo-1"
```

`GET /api/analyses?limit=20&offset=0` 按 `X-Session-Id` 返回该会话的任务摘要、`total`、`limit` 和 `offset`；默认每页 20 条，最多 100 条。列表不包含算法完整结果，选中任务后再查询详情。会话 ID 需要由前端稳定保存；它不是登录身份。

状态：`QUEUED`、`ANALYZING`、`AGENT_PROCESSING`、`COMPLETED`、`FAILED`、`CANCELED`；后三者是终态。算法成功后先持久化 `algorithm`，再进入 `AGENT_PROCESSING`。取消排队任务会立即进入 `CANCELED`；运行中的取消先设置 `cancel_requested=true`，后台步骤结束后进入终态，Agent 模型请求最多等待其超时。已完成任务的取消调用返回原任务。服务重启时已收到取消请求的任务进入 `CANCELED/USER_CANCELED`；未完成的算法任务进入 `FAILED/SERVER_RESTARTED`；中断的 Agent 任务保留算法结果，进入 `COMPLETED` 且 `agent_status=FAILED`、`agent_reason_code=SERVER_RESTARTED`。不会自动重试。

算法尚未配置时，任务会进入 `FAILED/ALGORITHM_NOT_CONFIGURED`，例如：

```json
{"task":{"task_id":"422f9e972f414c41bafab55c41794e20","video_id":"19f6294190334e57b1aa8ecc9c9aab61","exercise_id":"normal_squat","camera_view":"side","status":"FAILED","reason_code":"ALGORITHM_NOT_CONFIGURED","algorithm":null,"agent":null,"agent_status":"NOT_AVAILABLE","agent_run_id":null,"agent_reason_code":null,"cancel_requested":false,"created_at":"2026-11-03 10:00:00","updated_at":"2026-11-03 10:00:01"}}
```

HTTP 错误统一为 `{"error":{"code":"...","message":"..."}}`。`message` 供人阅读，前端按 `code` 和任务 `reason_code` 判断状态。其他任务失败码包括 `VIDEO_UNAVAILABLE`、`SCHEDULING_FAILED`、`ALGORITHM_FAILED`、`ALGORITHM_REPORTED_FAILURE`、`QUALITY_INSUFFICIENT`、`ALGORITHM_TIMEOUT`（180 秒）、`INVALID_ALGORITHM_RESULT`、`ALGORITHM_RESULT_TOO_LARGE`。算法若提供 `status`，只接受 `completed` 进入完成态；`failed` 和 `quality_insufficient` 会进入 `FAILED` 并保留相应原因码，其他状态视为无效结果。算法结果还需包含非空的 `results` 或 `guidance` 对象，原始输出超过 2 MiB 时不会完整读入内存。暂不自动重试失败任务。当前没有独立的质量不足终态，算法若输出 `unknown`，必须在结果中如实展示，不解释成正常或错误。

## 算法接入预留

拿到算法代码后配置以下服务端环境变量，再启动业务服务：

```powershell
$env:FITNESS_ALGORITHM_SCRIPT = 'C:\path\scripts\squat_offline_api.py'
$env:FITNESS_ALGORITHM_MODEL = 'C:\path\models\pose_landmarker_lite.task'
$env:FITNESS_ALGORITHM_RUN = 'C:\path\eval\squat_skeleton_offline_v1_20261030'
# 可选：$env:FITNESS_ALGORITHM_PYTHON = 'C:\path\python.exe'
```

服务按算法对接文档传入 `--video`、`--model`、`--run`、`--action`、`--camera-view`、`--output`。返回 JSON 只公开 `status`、`algorithm_version`、`results`、`confidence`、`guidance` 字段；原始路径和算法内部字段不透出。当前未收到算法代码，未验证这些命令与结果结构。

## Agent 接入

算法成功后，业务服务用仓库内 `agent-core` 执行一次深蹲反馈节点。配置模型后重启服务：

```powershell
$env:AGENT_MODEL_BASE_URL = 'https://your-provider.example/v1'
$env:AGENT_MODEL_API_KEY = 'your-api-key'
$env:AGENT_MODEL_NAME = 'your-model'
```

三个变量均设置时，`GET /api/health` 的 `agent_configured=true`。模型接收动作 ID、视角和算法公开结果；返回 `summary` 和 `suggestions`，每条建议包含 `text` 与引用算法 JSON 字段的 `evidence_paths`。服务校验证据路径存在且值不是 `unknown`，无可靠证据时允许空建议。`algorithm` 与 `agent` 分开返回；`agent_run_id` 供排查运行，`agent_reason_code` 记录失败原因。

Agent 未配置或调用失败时，任务仍以 `COMPLETED` 结束并保留 `algorithm`，但 `agent=null`。此时 `agent_status` 分别为 `NOT_CONFIGURED` 或 `FAILED`；前端应显示“仅算法结果”，不能展示为完整 Agent 反馈。`agent_status=COMPLETED` 才有 Agent 建议。任务尚未运行 Agent 或算法失败时为 `NOT_AVAILABLE`。模型输出格式或证据引用无效时，`agent_reason_code=AGENT_INVALID_RESPONSE`。Agent 不自动重试。

## 尚需团队确认

- 前端动作 ID 与 `normal_squat` 的映射，以及前端如何保存会话 ID。
- 算法实际字段、拍摄质量不足的判断规则和 `unknown` 的展示文案。
- Agent 建议的正式业务文案、算法证据字段与前端部分结果展示规则。
- 正式鉴权、部署地址、媒体存储、历史记录和素材查询整合方式。
