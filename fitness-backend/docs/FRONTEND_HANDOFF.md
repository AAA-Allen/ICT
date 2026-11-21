# 前后端首轮联调交接

更新：2026-11-04。前端同学已提供 `fitness-agent-web` 所在仓库，并负责修改 Vue 和浏览器联调；本文件说明业务后端的接线方式，不代表页面已联调通过。完整字段及错误码见 [FITNESS_BACKEND_API.md](FITNESS_BACKEND_API.md)。

## 本机服务与范围

- 后端仓库：`https://github.com/AAA-Allen/agent-core`，分支 `main`。联调以交付消息中的 commit SHA 固定版本。
- 启动：仓库根目录执行 `python -m fitness_backend.server --port 8082`。当前本机进程已在 `http://127.0.0.1:8082` 运行，`GET /api/health` 可查看配置。跨设备访问和部署地址尚未提供。
- 首个动作：前端 `bodyweight_squat` 映射为业务后端 `normal_squat`。前端其他动作暂不提交分析任务。
- 浏览器来源仅允许 `http://localhost:5173` 或 `http://127.0.0.1:5173`。视频和任务请求都带稳定的 `X-Session-Id`；这只是本机会话隔离，不是登录认证。
- 不接算法的当前状态：视频上传、任务创建、查询、取消都可调用；任务最终返回 `FAILED` 且 `reason_code=ALGORITHM_NOT_CONFIGURED`。当前没有真实分析结果可供结果页展示。Agent 也只有在算法成功后才运行。

## 前端接线顺序

1. 页面首次打开时生成会话 ID，保存于同源 `localStorage`；刷新后沿用。切换用户或需要隔离数据时换一个 ID。
2. 选定 `normal_squat` 后选择 MP4/WebM 文件。`POST /api/videos` 的请求体直接传 `File`，设置文件的 `Content-Type`，可设置 `X-Filename` 和每次上传固定的 `X-Upload-Key`。不要使用 `FormData` 或把本地 `blob:` 预览地址传给后端。
3. 收到 `video_id` 后再 `POST /api/analyses`，提交 `video_id`、`exercise_id: "normal_squat"`、可选 `camera_view` 和本次练习固定的 `idempotency_key`。响应中读取 `task.task_id`，不要用上传 ID 代替任务 ID。
4. 每 2 秒 `GET /api/analyses/{task_id}`。`QUEUED`、`ANALYZING`、`AGENT_PROCESSING` 继续等待；`COMPLETED`、`FAILED`、`CANCELED` 停止。进入新练习或组件卸载时停止旧轮询，旧任务响应不得覆盖当前选择。刷新后可用 `GET /api/analyses` 找回同会话任务。
5. 取消时 `POST /api/analyses/{task_id}/cancel`，以响应及后续查询的状态为准；停止轮询不等于取消任务。

浏览器请求的核心形态：

```ts
const base = 'http://127.0.0.1:8082';
const sessionId = localStorage.getItem('fitness-session-id') ?? crypto.randomUUID();
localStorage.setItem('fitness-session-id', sessionId);

const upload = await fetch(`${base}/api/videos`, {
  method: 'POST',
  headers: {
    'X-Session-Id': sessionId,
    'X-Upload-Key': uploadKey,
    'Content-Type': file.type,
  },
  body: file,
});
const uploadBody = await upload.json();
if (!upload.ok) throw new Error(uploadBody.error.code);
const { video_id } = uploadBody;

const created = await fetch(`${base}/api/analyses`, {
  method: 'POST',
  headers: { 'X-Session-Id': sessionId, 'Content-Type': 'application/json' },
  body: JSON.stringify({
    video_id,
    exercise_id: 'normal_squat',
    camera_view: 'side',
    idempotency_key: practiceKey,
  }),
});
const createdBody = await created.json();
if (!created.ok) throw new Error(createdBody.error.code);
const { task } = createdBody;
```

实际代码应先检查 `response.ok`，失败时读取 `error.code`。上传键和练习键分别在一次操作的网络重试中复用；换文件或开始新练习时生成新键。文件限制是 MP4/WebM、不超过 100 MiB。浏览器若无法提供这两种 MIME 类型，应在选择文件时明确提示。

## 页面状态映射

| 后端状态 | 前端行为 |
| --- | --- |
| `QUEUED` | 排队中，继续轮询 |
| `ANALYZING` | 分析中，继续轮询 |
| `AGENT_PROCESSING` | 反馈生成中，继续轮询；此时可能已有 `algorithm` |
| `COMPLETED` + `agent_status=COMPLETED` | 分别展示 `algorithm` 和 `agent`，保留来源标识 |
| `COMPLETED` + `agent_status=NOT_CONFIGURED/FAILED` | 只展示算法结果，提示 Agent 反馈不可用 |
| `FAILED` | 展示 `reason_code` 对应失败，不能把它当作正常结果 |
| `CANCELED` | 展示已取消，允许开始新练习 |

当前无算法时，正常创建任务后会得到 `FAILED/ALGORITHM_NOT_CONFIGURED`。这能联调上传、任务 ID、查询、失败态和取消入口，不能据此验收结果页。`QUALITY_INSUFFICIENT` 目前也是 `FAILED` 的一个原因码。任务 `status=COMPLETED` 不保证 Agent 成功，必须再看 `agent_status`。

## 对接后仍需确定

- 前端同学负责修改 `VideoUploader`、`useAnalysisTask.ts`、结果类型和页面状态映射；以实际目录为准。保留其 Java 查询服务的 `/api/v1/*`，不要将其与此服务的 `/api/*` 混为一个后端。
- 双方已确认首个动作 ID 映射；浏览器本机运行于 5173。无算法环境若准备显式模拟结果，必须标注来源，不能冒充真实算法或 Agent。
- 真实结果页和完整成功路径要等算法输出可用后再验收；正式素材、账号历史及跨设备部署尚未包含在本轮。
