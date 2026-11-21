# ICT2 健身业务后端

健身视频上传、SQLite 任务与会话历史、深蹲算法调用和 Agent 反馈。业务代码从 `agent-core` 独立迁出，通用框架通过相邻的 `../agent-core` 提供。

## 安装和启动

从本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ..\agent-core
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m fitness_backend.server --port 8082
```

前端位于 `../fitness-agent-ict`，使用 `npm.cmd ci --prefix fitness-agent-web` 安装，然后执行 `node scripts/start-demo.mjs` 同时启动 Vue 与 Java 查询服务。浏览器地址为 http://127.0.0.1:5173。

业务测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。

## 联调

选择股四头肌、徒手深蹲，上传 MP4/WebM。本机前端默认使用真实后端分析，选择正确动作类型和机位后提交。后端自动发现相邻交付包的脚本、模型与独立 Python 环境，也可通过环境变量覆盖。模拟模式仍可手动选择，仅用于前端演示。

API 与算法配置见 [接口文档](docs/FITNESS_BACKEND_API.md)。`docs/` 中的交接记录保留了迁移前的仓库说明；当前目录与启动方式以本 README 为准。

## 2026-11-19 算法与 agent-core 联调

从本目录运行 `.\.venv\Scripts\python.exe -X utf8 -m fitness_backend.integration`。
默认使用相邻 `squat-algorithm-handoff-20261119` 包的脚本、模型与独立 Python 环境，以及人工标注为正面宽站距的 `00054719.mp4`。
可通过 `--video`、`--action`、`--camera-view` 更换输入。自定义视频时应同时设置正确的动作与机位。

此入口使用真实 `agent_core.AgentBuilder` 编排 `upload → analyze → verify_evidence`，启动临时本机 HTTP 服务并执行实际视频推理。
每次运行将任务、算法结果、框架运行 ID 与节点轨迹保存至 `outputs/integration/<run_id>/`，完成后关闭临时服务。
这是框架与算法联调；语言模型反馈仅在 `AGENT_MODEL_*` 配置齐全时执行，未配置时明确返回 `NOT_CONFIGURED`。
算法默认使用保守膝内移模式；12 类观察中的候选仍须人工复核。

长期启动业务接口，使用 `scripts/start-with-algorithm.ps1`（默认端口 8082）。
算法相似视频索引 `FITNESS_ALGORITHM_RUN` 现在可选。适配层将 `usable_candidate` 转成业务 `completed`，
将 `insufficient_pose` 映射为 `QUALITY_INSUFFICIENT`，原始证据状态不变。
公开结果的 `results` 包含 `quality`、`cycles`、`features`、`biomechanics` 与 `limitations`；`guidance` 保持独立。
目前支持 `normal_squat`、`wide_squat`、`narrow_squat` 三种动作 ID。

原业务运行数据已复制至 `outputs/fitness_backend` 和 `outputs/fitness_handoff_data`，SQLite 内的视频路径已迁移到本目录。该目录不纳入版本控制。
