# Harness 开源项目调研与取舍

调研日期：2026-10-28  
目的：为跨领域 `agent-core` 的 Harness 层确定可借鉴的设计机制。  
范围：公开仓库 README 与官方设计文档；本轮没有对第三方源码做逐行审计，也没有运行第三方项目。

DeepSeek Harness 的 `master` 在本轮调研时指向 [`f3803c9`](https://github.com/deepseek-ai/deepseek-harness/tree/f3803c96974ad4a94195fb97e5c637e9c2a3afe3)；其文档仍在快速迭代，下述结论以这一版公开资料为准。

## 参考项目

| 项目 | 已核对的机制 | 对本项目的启发 |
| --- | --- | --- |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | [架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.zh.md)将模型适配器、工具注册表、会话日志和 Agent loop 都作为可替换插件；[会话设计](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/session.zh.md)以追加事件为交互事实，模型历史从中派生；[工具流水线](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.zh.md)明确调用前、守卫、执行、调用后和结果记录的顺序。 | Harness 首先是跨领域 Agent 的组合与执行架构，不只是预算和审批；应定义可替换的模型、工具、循环与上下文接口，以及稳定的调用事件顺序。 |
| [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai) | [Harness 总览](https://pydantic.dev/docs/ai/harness/)将记忆、guardrails、持久化等作为可组合能力；[Guardrails](https://pydantic.dev/docs/ai/harness/guardrails/)区分输入、输出和工具调用的检查点，并用明确的 allow、block、replace、retry、approve 结果。 | 将策略实现为小型、可组合的执行钩子；策略决定使用结构化结果，调用前与调用后分开。 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts.md)通过持久化状态暂停并恢复，明确提醒中断前副作用要幂等；[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence.md)区分线程内 checkpoint 与跨线程 store。 | 批准必须有真实等待状态和恢复契约；checkpoint、会话记忆与调用审计保持不同作用域。 |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | [Human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)要求批准请求绑定具体工具调用，恢复时由服务端持有待批准状态，并指出批准本身不能保证外部副作用恰好执行一次；[Tracing](https://openai.github.io/openai-agents-python/tracing/)提供调用追踪与敏感内容控制。 | 批准 ID 绑定工具名、参数摘要与运行；不接收客户端替换的执行快照；审计默认只保存安全摘要，完整内容需显式配置。 |
| [smolagents](https://github.com/huggingface/smolagents) | [Agent API](https://huggingface.co/docs/smolagents/reference/agents)保留模型、工具、`max_steps` 和步骤回调等少量核心概念；仓库强调模型和工具可替换。 | 保持 Harness 公共接口小而清楚，先解决执行边界，不把大量领域集成塞进核心。 |

调研时也检查了 [AutoGen 仓库](https://github.com/microsoft/autogen)。其 README 已标明项目归档且不再维护，因此只作为历史参考，不作为当前实现的主基线。[OpenHands 仓库](https://github.com/All-Hands-AI/OpenHands)当前 README 主要介绍面向编码 Agent 的控制中心，其产品范围与本项目的通用 Harness 不同，暂不作为核心架构样板。

## DeepSeek Harness 的关键启发

DeepSeek Harness 的官方 [中文 README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.zh.md)将自身定位为“一切皆插件”的开源 Harness，仍处于开发者预览。它和本项目的共同点是希望通过可替换的模型与工具适配不同任务；区别是它已经覆盖桌面、Web、SDK、沙箱、自动化和大量具体能力，规模远大于当前 `agent-core`。

值得优先借鉴的机制有三项：

1. **先定义能力接口，再装配实现。** 它把模型、工具、会话和循环各自做成可替换服务。我们可以先以 Python 协议和 Agent 配置实现同样的分离，不急于引入完整插件树、热卸载和 profile 系统。
2. **模型历史从持久事实派生。** 它的会话日志保存用户、模型、工具请求和结果等事件，历史是投影。我们现有会话历史和运行 Trace 分开，M1 应建立私有的模型可见事实记录，并从中投影历史；对外审计只给脱敏视图，避免两份历史相互矛盾，也避免用摘要冒充完整上下文。
3. **工具执行是有顺序的流水线。** 请求先记录，再经过前置策略和不可绕过的守卫，随后执行、后置处理、规范化结果并记录。我们应在 M1/M2 固定这些阶段与失败语义，避免每个节点各自实现一套工具检查。

不直接照搬的部分：Cordis 的全插件运行时、动态热卸载、复杂 profile 覆盖、桌面端与大规模内置工具集合。这些增加的复杂度需要独立的产品需求和稳定扩展契约；当前 Python 框架先保持小接口。

## 具体设计取舍

### 1. 采用能力组合，但不立即引入插件平台

Pydantic AI 的能力拆分适合长期扩展。我们先定义少量稳定的执行切入点：`before_model`、`after_model`、`before_tool`、`after_tool`、`before_output`。预算、授权、脱敏和审计在这些位置执行。第一阶段先以内部接口实现，等顺序、错误与恢复语义稳定后再开放第三方插件注册。

不照搬完整的 capability 包体系：当前项目规模和依赖目标都更小，过早开放插件会把尚未定稿的执行语义变成公共 API。

### 2. 批准是持久化等待，不是一次布尔回调

LangGraph 和 OpenAI Agents SDK 都表明，人工批准会跨越进程或请求边界。我们需要 `WAITING_APPROVAL` 一类明确状态、待批准请求的服务端持久化、决定 ID 的去重，以及对工具名和参数摘要的再校验。

批准不等于工具执行成功。进程在外部动作完成后、结果落盘前崩溃时仍可能重放；对关键副作用必须使用外部系统认可的幂等键，无法确认时记录未知结果并停止自动重试。

### 3. 三类状态分别管理

借鉴 LangGraph 对 checkpoint 与 store 的区分，我们保持：

- Runtime checkpoint：图执行到哪里，哪些状态更新已经提交。
- 会话存储：跨运行的对话历史与应用记忆。
- Harness 审计：模型与工具尝试、预算消耗、批准决定及外部调用结果。

三者通过 `run_id`、执行 ID 和调用 ID 关联，但不能互相代替。尤其不能从 checkpoint 缺少某个结果推断外部工具没有执行。

### 4. 审计内容默认收敛

OpenAI Agents SDK 的追踪文档说明模型和工具输入输出可能包含敏感信息。我们的默认审计只存调用身份、时间、状态、原因码及经过脱敏的摘要；完整内容由应用显式开启并提供存储与保留策略。调用事实必须可查，原始秘密不必进入日志。

### 5. 保持跨领域最小核心

借鉴 smolagents 的小接口目标，Harness 的公共概念先限制在策略、预算、调用记录、上下文和批准请求。领域专用工具、RAG、浏览器、代码沙箱和第三方服务集成留在应用或独立适配包，不让 `agent_core` 变成集成集合。

## 对现有开发规划的影响

1. M1 审计底座必须先定义稳定调用 ID 和失败时保留事实的写入顺序；它是跨恢复预算、批准与幂等的共同依赖。
2. M2 工具策略使用结构化决定，例如允许、阻断、替换或要求批准；输出检查与调用前授权分开。
3. M4 批准只从服务端保存的待办恢复，客户端提交决定，不提交替换后的工具请求或运行快照。
4. M5 工具重试以外部幂等契约为前提；单纯的本地完成标志不能承诺恰好一次。

这些是设计参考，不是把其他项目的 API 或源码复制进本项目。具体字段与公共接口仍以 [Harness PRD](HARNESS_PRD.md) 的阶段验收和本项目兼容政策为准。
