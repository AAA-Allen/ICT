# ICT2 健身项目

2026-11-19：深蹲算法已接入后端与前端。按下方启动两个终端后，选择股四头肌 → 徒手深蹲 → 选择视频 → 设置动作类型与机位 → 上传分析，可查看真实标签候选与生物力学指标。后端自动发现 `squat-algorithm-handoff-20261119` 包及其 `.venv`。当前语言模型反馈未配置，页面明确展示“仅算法结果”。框架联调复现入口见 `fitness-backend/README.md`。下方“本次验证”是 11 月 7 日的历史记录。

2026-11-07 已完成前后端本机启动验证，健身业务已从原 `D:\AAA-My-project\agent-core` 迁出。

| 目录 | 内容 |
| --- | --- |
| `fitness-agent-ict/fitness-agent-web` | Vue 前端 |
| `fitness-agent-ict/fitness-query-service` | Java 肌群、动作与示范资料查询服务 |
| `fitness-backend` | Python 视频上传、任务、算法适配和 Agent 反馈；包含独立测试与文档 |
| `agent-core` | 通用框架本机副本 |

## 安装和启动

先按 [前端与 Java 查询服务](fitness-agent-ict/README.md) 和 [Python 后端](fitness-backend/README.md) 安装依赖。从仓库根目录为算法创建 Python 3.12 环境：

```powershell
cd squat-algorithm-handoff-20261119
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-squat-skeleton.txt
```

以下两个终端均从仓库根目录开始。真实算法联调时，通过 `--video` 指定自己的视频。

终端一：

```powershell
cd fitness-agent-ict
node scripts/start-demo.mjs
```

终端二：

```powershell
cd fitness-backend
.\.venv\Scripts\python.exe -m fitness_backend.server --port 8082
```

打开 http://127.0.0.1:5173。Vue 使用 5173，Java 查询服务使用 8081，Python 业务服务使用 8082。首次安装见两个子项目的 README。

## 本次验证

- 前端拉取远端：`Feather919/fitness-agent-ict` 的 `2b39af5` 为验证时最新远端版本。
- 原通用框架迁移后 384 项测试通过，独立健身后端 16 项测试通过。
- 前端类型检查、生产构建、Lint、格式检查全部通过；Java HTTP 检查 60 项、启动器检查 17 项通过。
- 浏览器从 Java 加载资料，选择股四头肌和徒手深蹲，预览 WebM、上传、创建及查询任务，并找回最近会话任务。
- 当前算法与模型未配置，终态为 `FAILED/ALGORITHM_NOT_CONFIGURED`。验证用的是生成的 1 秒灰色视频，仅用于接口联调，不作为深蹲算法效果证据。原 MP4 黑色视频在本次内置浏览器中无法解码，WebM 预览成功。
