# Harness M3 阶段开发文档

状态：上下文装配与真实 usage 记录已接入并通过测试

## 本轮交付

- `ContextLimits` 提供可选的消息数和字符数上限。
- `ContextAssembler` 在模型调用前统一装配消息。
- 裁剪规则固定：保留首个 system 消息及当前完整用户轮次；从近到远保留完整历史轮次，不拆散工具调用与工具结果。
- `agent_loop` 和 `llm` 节点支持 `max_context_messages` 与 `max_context_chars`；未配置时保持原消息序列。
- 必需消息本身超限时返回 `CONTEXT_LIMIT_EXCEEDED`，模型不会收到截断后的请求。
- `llm` 普通与流式调用经过模型流水线；审计只记录适配器明确返回的非负整数 token usage。
- 模型请求审计包含原始、实际及裁剪掉的消息数量；必需消息超限写入 `model.rejected` 和 `CONTEXT_LIMIT_EXCEEDED`。

## 边界

- 字符数是确定性的 JSON 字符长度，不冒充真实 token 用量。
- 裁剪只改变本次模型请求，成功后会话存储仍保留完整历史；失败不写回历史。
- 工具结果仍由循环追加到消息序列，下一次模型调用前再次经过装配器。
