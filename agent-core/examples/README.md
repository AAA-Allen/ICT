# 示例应用：文本处理管线

受控参考 Agent：`python -m examples.reference_agent` 运行离线资料审阅并等待草稿保存审批；`--action serve --port 8001` 从现有工作台提交。真实模式用 `--mode real`，缺少配置明确未执行。参数、路径边界和恢复说明见 [M13](../docs/HARNESS_M13.md)。

资源基线：`python -m examples.resource_baseline --rounds 200`，执行固定脚本模型的顺序 HTTP 工作流，测量缓存、线程、文件与耗时，详见[资源基线说明](../docs/M12_RESOURCE_BASELINE.md)。该工具从源码运行，不安装到 wheel。

用纯文本处理这个领域跑通 `agent_core`,证明框架核心不含任何业务词汇——换一个领域,只是换一张图。

## 跑法

```bash
python examples/text_pipeline.py
# 或
python -m examples.text_pipeline
```

Graph Runtime 示例：

```bash
python -m examples.graph_pipeline
```

## 流程

```
normalize（工具）→ validate（自定义步骤）→ analyze（工具）→ classify（工具）
```

- 工具经 `ToolRegistry` 注册、`tool_step` 绑定,全部进入同一条 Trace
- 空文本在 `validate` 处 **BLOCKED**（原因码 `EMPTY_TEXT`），后续步骤不执行——框架的 fail-closed 决策边界
- 运行记录落盘 `outputs/examples/text_runs/`（`RunStore`,与所有应用同一机制）

## 行为保护

`tests/test_examples.py` 覆盖完成路径、阻断路径与 Trace 完整性，示例改坏会被测试抓住。

`graph_pipeline.py` 展示 v0.1 的 `Graph`、条件路由、状态更新和 `BLOCKED` 终态。

## 自定义节点受管调用

执行 `python -m examples.managed_calls`，验证自定义节点的工具和模型调用共用 Harness 审计与运行级额度。该示例把两个逻辑调用拆成两个图节点，使用明确标注的脚本模型，不代表真实模型效果；实际接入可向 `build_agent(directory, model=...)` 注入适配器。

## 应用工作流验收

`python -m examples.workflow_acceptance --mode offline --repeat 2` 用本机 HTTP 替身运行资料审阅和任务分流的 14 个固定用例，各两次，报告保存在 `outputs/acceptance/`。输入与预期位于 `acceptance_cases.json`。

配置服务端 `AGENT_MODEL_*` 三项环境变量后，可执行 `python -m examples.workflow_acceptance --mode real --repeat 2`。缺少配置时退出码为 2，报告标为 NOT_RUN。脚本按固定动作批准/拒绝，只写入批次目录的本地示例记录。详见 [M10](../docs/HARNESS_M10.md)。
