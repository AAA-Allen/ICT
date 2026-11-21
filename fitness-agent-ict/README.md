# Fitness Agent

健身训练交互原型。Vue 前端将肌群、训练条件、动作、视频、分析和历史分为六个页面，适配手机操作。支持 11 个肌群的 3D 与二维选择。Java 服务提供肌群、动作与示范资料的只读 HTTP 接口。页面默认使用模拟分析，徒手深蹲也支持向本机业务后端上传视频并查询任务状态。

## 启动

Windows 环境需要 Node.js 22.18+（22.x）或 24.12+，以及 JDK 17+。在仓库根目录打开 PowerShell，首次运行时安装前端依赖。

```powershell
npm.cmd ci --prefix fitness-agent-web
.\start-demo.cmd
```

终端显示 `READY` 后打开 `http://127.0.0.1:5173/`。点击“读取 Java 查询服务”可从本机 Java 服务加载资料。输入 `q` 并回车可停止两个服务。

端口占用时，可指定两个空闲端口。

```powershell
.\start-demo.cmd --java-port=8083 --web-port=5174
```

## 徒手深蹲后端联调

1. 本机健身业务后端已独立迁至 `../fitness-backend`。在独立终端进入该目录，执行 `.\.venv\Scripts\python.exe -m fitness_backend.server --port 8082`。首次安装见 [后端说明](../fitness-backend/README.md)。通用框架依赖位于 `../agent-core`。
2. 按上方启动步骤运行 Vue 与 Java。真实上传请使用 `http://127.0.0.1:5173` 或 `http://localhost:5173`，这两个来源受业务服务允许。
3. 选择股四头肌 → 设置并确认训练条件 → 选择徒手深蹲 → 在“记录动作视频”页选取 MP4/WebM → 进入“分析与反馈” → 将“分析方式”切到“本机后端” → 点击上传。后端会保存所选文件。
4. 算法未配置时，预期显示视频编号、任务编号和 `ALGORITHM_NOT_CONFIGURED`。刷新后重新选择徒手深蹲，进入“分析与反馈”并切到“本机后端”，点击“查询本会话最近任务”即可查回状态。真实问题定位与效果评估待算法样例交付。

## 构建与检查

```powershell
.\check-all.cmd
```

统一检查覆盖资料同步、Java 编译与 HTTP 测试、启动参数、前端类型、构建、Lint 和格式。构建通过后可运行 `.\start-demo.cmd --preview --web-port=4173` 预览前端产物。

静态预览可单独构建：`npm.cmd run build --prefix fitness-agent-web`。部署 `fitness-agent-web/dist/` 即可体验前端和本地模拟分析；Java 查询与真实深蹲分析仍需分别启动服务。内置视频是原创的图形与文字播放测试片段，不是动作教学素材。

前端代码位于 `fitness-agent-web/src/`。查询服务的独立运行方式和请求示例见[服务说明](fitness-query-service/README.md)和[接口协议](fitness-query-service/docs/api-contract.md)。
