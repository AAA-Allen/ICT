# Harness M11：候选版本交付准备

状态：独立 wheel 安装检查、开发依赖、CI 接线及兼容说明已实现。本机 Windows / Python 3.13 已验证；远端矩阵、M10 真实模型验收、许可证和发布版本决策待完成，尚未发布候选版本。

## 本阶段范围

M10 离线验收已通过，但真实模型配置仍缺失。本阶段先推进不依赖模型的交付准备，不将 M10 或 M11 整体标记完成。依据是安装、资源打包和文档校准可以独立验证，真实任务质量仍须按 M10 的固定判据另行验收。

| 任务 | 当前状态 |
| --- | --- |
| M11-01 包安装验证 | Windows / Python 3.13 独立 wheel 检查通过；运行包包含工作台和三个 JSON 配置；开发依赖已声明 |
| M11-02 跨平台回归 | CI 已接入 Windows/Linux × Python 3.10/3.13 的 wheel 检查及报告留存；远端执行待完成 |
| M11-03 公开契约 | [兼容说明](COMPATIBILITY.md)、安装与排障入口、历史文档入口已更新 |
| M11-04 发布决策 | 依赖和交付清单已整理；许可证、版本号及发布指令待维护者确认 |

## 构建和自动安装验证

从源码根目录执行，要求 Python 3.10+：

```bash
python -m pip install -e ".[dev]"
python -m build --wheel --no-isolation
python tools/verify_distribution.py dist
```

`dev` 包含 pytest、build、wheel 和 setuptools；它们是开发工具，运行包没有第三方运行依赖。显式包含 setuptools，保证没有预装构建后端的新 Python 环境也能执行 `--no-isolation`。构建使用当前环境已有的构建工具，`--no-isolation` 不会为构建另建下载环境。开发依赖的首次安装需要可用的包源。验证器要求 dist 中恰好一个 wheel；存在多个版本时应传入具体 wheel 路径。

验证器固定读取 wheel 的字节快照，以同一份内容计算哈希并安装，避免构建期间文件被替换导致证据错配。在系统临时目录创建不继承系统包的 venv，将 wheel 快照与检查脚本复制到仓库外，使用该 venv 的 `python -I` 执行检查，且不把仓库加入 PYTHONPATH。安装只使用指定 wheel，禁用包源和依赖解析；随后运行 `pip check` 验证声明的依赖齐全，缺少依赖不能通过。移除子进程中的模型配置，不访问外部模型。

检查涵盖：

- `agent_core`、`agent_lite` 从 venv 导入，包元数据与版本一致。
- 公开 `AgentBuilder`、`NodeCallPolicy` 和 `ctx.calls.tool` 可完成一次调用。
- 安装生成的 `agent-core-server` 可显示帮助、启动和加载配置。
- 工作台三个静态资源来自安装位置，HTTP 返回内容与安装文件一致。
- 内置 workbench、声明式工具流程、空输入阻断、缺少模型阻断及确定性失败均符合预期。
- 审计可查询；停止 CLI 进程后重新启动，成功、阻断和失败结果仍可查询。

检查只验证安装和执行契约，不评价真实模型效果，也不替代审批恢复故障矩阵或浏览器交互测试。

每次输出到 `outputs/distribution/wheel-<batch-id>/`：`report.json` 包含 wheel 文件名及 SHA-256；`installed.json` 记录实际 Python、平台、导入位置与逐项检查；另保留安装、依赖、启动和检查日志。依赖检查失败时不启动后续 smoke，具体原因见 `dependencies.log`。临时 venv 与运行目录在结束后清理，报告中的临时路径仅用于说明当时的导入位置。smoke 超时或中断时，Windows 清理对应进程树，POSIX 清理其独立进程组，避免只结束检查脚本而遗留服务。失败返回退出码 1；通过返回 0。

## 脱离源码的手动安装

使用已构建的 wheel 绝对路径，在仓库外的新目录执行。无需 editable 安装或下载第三方运行依赖。以下 Windows PowerShell 示例的 wheel 路径需替换为实际产物：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps D:\path\agent_core-0.4.0-py3-none-any.whl
.\.venv\Scripts\agent-core-server.exe --port 8000 --store-dir runs
```

Linux/macOS 使用同一 wheel：

```bash
python -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps /path/agent_core-0.4.0-py3-none-any.whl
.venv/bin/agent-core-server --port 8000 --store-dir runs
```

打开 `http://127.0.0.1:8000`。默认 workbench 使用确定性文本工具，不调用模型。在另一个 PowerShell 窗口提交任务并查询阻断原因：

```powershell
$body = @{agent='workbench'; input=@{task='检查安装;查看结果'}} | ConvertTo-Json -Depth 5
$completed = Invoke-RestMethod http://127.0.0.1:8000/api/runs -Method Post -ContentType 'application/json' -Body $body
$completed.run.status
$body = @{agent='workbench'; input=@{}} | ConvertTo-Json -Depth 5
$blocked = Invoke-RestMethod http://127.0.0.1:8000/api/runs -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/runs/$($blocked.run.run_id)"
```

前者应为 COMPLETED；后者应为 BLOCKED / EMPTY_TASK。用同一 `--store-dir` 重启后，仍可查询该 run_id。

wheel 内置的 `support_agent.json`、`assistant_agent.json`、`recovery_agent.json` 位于当前 Python 安装数据目录下的 `share/agent-core/examples/`。用运行服务的同一个 Python 查找目录，将所需 JSON 的绝对路径传给 `--config`：

```bash
python -c "import sysconfig; from pathlib import Path; print(Path(sysconfig.get_path('data')) / 'share' / 'agent-core' / 'examples')"
agent-core-server --config /absolute/path/to/support_agent.json --store-dir runs
```

完整 Python 示例、审批服务示例和 M10 验收脚本保留在源码仓库中，wheel 不安装 `examples` Python 包或开发工具。assistant 需要服务端模型配置及会话；缺少模型返回 MODEL_NOT_CONFIGURED。

## 排障

| 现象 | 检查与处理 |
| --- | --- |
| 找不到 agent-core-server | 使用上面的 venv 内完整可执行文件路径，核对 wheel 安装到了哪个 Python |
| API 可用但页面缺失或出现别的页面 | 默认资源查找顺序为当前目录 frontend、源码旁 frontend、安装数据目录；检查目录覆盖，或用 `--frontend-dir` 指定正确位置 |
| 第二个服务启动失败 | 同一运行目录只允许一个服务实例；停止已有实例或选择独立目录 |
| 返回 BLOCKED / MODEL_NOT_CONFIGURED | 按 README 配置模型环境变量后重启；安装验证本身不需要模型 |
| 恢复返回 HTTP 409 | 查询状态与 reason_code；按兼容说明检查审批、checkpoint 和未结算调用，不重复提交未知副作用 |
| wheel 检查失败 | 查看批次目录的 install.log、dependencies.log、smoke.log、installed.json 和启动日志；按 wheel SHA-256 定位产物 |

## 交付与发布清单

| 项目 | 事实 / 待决事项 |
| --- | --- |
| 当前包版本 | 0.4.0，Alpha；本机 wheel 为内部验证产物，未作为新候选版本发布 |
| 下一版本范围 | M8 含自定义审批存储接口和恢复行为变化，建议下一次交付按 0.x 次版本讨论 0.5.0；未修改版本号 |
| 许可证 | 仓库尚无许可证文件，待维护者选择后补齐文件及包元数据 |
| 运行依赖 | 空；构建依赖 setuptools，dev 包含 pytest/build/wheel/setuptools；Node.js 仅用于前端测试 |
| 运行包内容 | agent_core、agent_lite、三个工作台资源、三个独立 JSON 配置、元数据及 CLI 入口 |
| 本机安装证据 | Windows / Python 3.13；wheel 内容哈希及安装检查见本次生成报告 |
| 跨平台证据 | 远端 CI 矩阵尚未运行；未声称 Linux 或 Python 3.10 已验证 |
| 模型证据 | M10 离线 28 次通过；真实模型未配置，仍为 NOT_RUN |
| 公开发布 | 需要许可证、版本决策、必要验收及明确发布指令；本阶段未推送或发布 |

M11 整体仍未完成。已完成的安装准备可以复用；后续应补齐真实模型报告、远端矩阵结果和发布决策，再判断是否具备候选版本交付条件。

## 本机验证记录

2026-11-08，Windows 11 / Python 3.13.5：构建 wheel 并在仓库外 venv 安装，公开 API、CLI、资源、配置、阻断、失败与跨进程重启查询全部通过。去掉 support_agent.json 的损坏 wheel 被明确判为 FAIL，失败检查为 installed example configs。

完整 pytest 449 passed、unittest 449 tests / OK；前端 Node 测试 6 项通过，语法检查、更新文档的本地链接检查及 `git diff --check` 通过。未运行远端 CI，未执行外部模型验收，未推送或发布。

## Review 追补

复现并修复三处验收器问题：缺失声明依赖仍被判通过、wheel 在创建环境期间被替换导致报告哈希与安装内容不一致、smoke 超时遗留派生服务进程。新增 3 项回归测试，超时用例实际创建子进程监听端口并确认超时后端口关闭。正常 wheel 的 34 项安装检查继续通过；注入缺失依赖的 wheel 由原先 PASS 改为 FAIL，报告记录依赖检查失败及对应日志。

本机完整 pytest 452 passed、unittest 452 tests / OK，前端 6 项通过。文档链接、语法及 `git diff --check` 检查通过；远端 CI 与真实模型状态保持待验收。
