# 深蹲首轮业务后端交付记录

日期：2026-11-04。接收方的前端仓库为 `https://github.com/Feather919/fitness-agent-ict`；前端 `bodyweight_squat` 创建业务任务时映射为 `normal_squat`。本轮只联调上传、创建任务、查询状态。

## 代码与启动

- 业务后端仓库：`https://github.com/AAA-Allen/agent-core`，分支 `main`。以发送消息中的 commit SHA 固定本轮交付版本。
- 本机实测 Python：3.13.5。项目要求 Python 3.10+，无第三方运行依赖。
- 在干净环境中，从仓库根目录执行以下命令。`pip install -e .` 安装 `fitness-backend` 命令；也可直接用 `python -m fitness_backend.server` 从仓库根目录启动。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\fitness-backend.exe --port 8082
```

默认只监听 `127.0.0.1:8082`，数据写入 `outputs/fitness_backend/`。同一数据目录仅允许一个服务进程。`X-Session-Id` 是本机会话标识，不是安全凭证；不能将当前服务直接暴露到公网。浏览器来源允许 `localhost:5173` 和 `127.0.0.1:5173`。API 详见 [FITNESS_BACKEND_API.md](FITNESS_BACKEND_API.md)，前端接线顺序见 [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md)。

当前不需要设置算法或模型变量。日后接算法时使用 `FITNESS_ALGORITHM_SCRIPT`、`FITNESS_ALGORITHM_MODEL`、`FITNESS_ALGORITHM_RUN`，可选 `FITNESS_ALGORITHM_PYTHON`；Agent 模型使用 `AGENT_MODEL_BASE_URL`、`AGENT_MODEL_API_KEY`、`AGENT_MODEL_NAME`。密钥只放服务端。当前变量均未配置。

## 本机 HTTP 实测

实测环境：Python 3.13.5，业务服务 `127.0.0.1:8082`，独立数据目录 `outputs/fitness_handoff_data`。上传文件 `fitness_handoff_clip.mp4` 是用 FFmpeg 生成的 1 秒黑色视频，大小 4211 字节，可解码但**不是深蹲动作素材**。它仅用于验证上传和任务状态协议；不能作为算法效果证据。会话头三步均为 `X-Session-Id: handoff-20261104`。

`GET /api/health`：

```json
{"status":"ok","algorithm_configured":false,"agent_configured":false}
```

1. `POST /api/videos`，请求体为 MP4 原始字节，`Content-Type: video/mp4`，`X-Upload-Key: handoff-video-1`。响应：

```json
{"video_id":"4faf5441b6f74777bf25a18a7c1d6178","size":4211,"content_type":"video/mp4","sha256":"91cdd4111bfe85f7ea42c20ae10b8a2dd0723166623c9a4e3162c8fccf09e262","reused":false}
```

2. `POST /api/analyses`，请求体：

```json
{"video_id":"4faf5441b6f74777bf25a18a7c1d6178","exercise_id":"normal_squat","camera_view":"side","idempotency_key":"handoff-practice-1"}
```

创建响应：

```json
{"task":{"task_id":"f116f641f362472590b2371db4530923","video_id":"4faf5441b6f74777bf25a18a7c1d6178","exercise_id":"normal_squat","camera_view":"side","status":"QUEUED","reason_code":null,"algorithm":null,"agent":null,"agent_status":"NOT_AVAILABLE","agent_run_id":null,"agent_reason_code":null,"cancel_requested":false,"created_at":"2026-11-04 09:11:25","updated_at":"2026-11-04 09:11:25"},"reused":false}
```

3. `GET /api/analyses/f116f641f362472590b2371db4530923`，终态响应：

```json
{"task":{"task_id":"f116f641f362472590b2371db4530923","video_id":"4faf5441b6f74777bf25a18a7c1d6178","exercise_id":"normal_squat","camera_view":"side","status":"FAILED","reason_code":"ALGORITHM_NOT_CONFIGURED","algorithm":null,"agent":null,"agent_status":"NOT_AVAILABLE","agent_run_id":null,"agent_reason_code":null,"cancel_requested":false,"created_at":"2026-11-04 09:11:25","updated_at":"2026-11-04 09:11:25"}}
```

上述请求确认视频 ID、任务 ID 与动作 ID 在同一会话中一致。未接算法是本轮约定，故任务失败态是预期结果。结果页的真实成功路径尚未验收。

## 下一轮真实结果

业务后端由本仓库维护者负责接收并返回真实任务详情；算法同学尚未交付可运行算法代码，因此目前无法给出可信的真实结果样例和预计日期。收到算法、模型及一段可用于联调的深蹲视频后，再约定视角、问题代码、置信度和视频定位字段，并提供算法成功而 Agent 不可用的实际样例。不能把固定模板或上述黑色视频标为真实分析结果。
