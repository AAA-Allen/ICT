# Harness M12：流式与持续运行边界

状态：M12-01..05 的本机实现与验收已完成，包括流式协议、资源边界和 200 次持续运行基线。远端矩阵及真实模型、许可证和发布门禁仍未完成。

## 问题与协议

原先读取进度是 deque 当前列表下标，历史淘汰后与 SSE 累计进度错位，导致漏读。现在每个流保存单调序号；淘汰旧事件不改变后续序号。同一运行跨模型节点、审批暂停与恢复继续使用同一流身份。

- `TokenBus.history(run_id, after=N)` 保持三元组形状，after 改为最后已读取事件的绝对序号，不再是保留列表的切片位置。
- 新增 `read(run_id, after=cursor)`，返回带 cursor 的事件窗口、gap、读取起点 cursor 和 closed；调用方应原样传回游标。
- token SSE 增加 id 和 JSON cursor，保留 run_id/node_id/text。生命周期和 done 不覆盖 token 的 SSE id；生命周期仍按 event_id 去重。
- 自动重连用 Last-Event-ID，重新建立页面订阅可用 cursor query。非空 Last-Event-ID 优先于初始 query。
- 畸形游标在流响应头之前返回 HTTP 400 / INVALID_STREAM_CURSOR。
- 确认终态或 WAITING_APPROVAL 后再读一次窗口，避免终态在前次读取后可见而漏发最后输出。

## 缺口

| gap.reason_code | 事实 |
| --- | --- |
| HISTORY_TRUNCATED | 起点早于保留窗口，包括首次晚订阅时历史已截断 |
| HISTORY_UNAVAILABLE | 客户端带旧游标，但服务端已无流内存记录 |
| STREAM_RESET | 运行的新流身份与旧游标不同 |
| CURSOR_AHEAD | 同一流的请求游标超过已发布进度 |

gap 在保留 token 前发送，其 cursor 指向保留窗口前一项；历史不可用时 cursor=null，并发送空 SSE id 清除浏览器的 Last-Event-ID。新建 TokenBus 或 forget 后重新建流不保证旧 token 重放。

前端遇到缺口时清空旧预览前缀，展示“流式预览不完整，请以运行结果为准”，随后只展示保留后缀。按流身份和序号丢弃已处理 token，缺口标记不会因新片段到达而自动消失。完成后继续查询权威运行结果。

旧客户端可读取原有 token 字段，但不理解 gap 的客户端不能保证预览完整，应与服务端同步升级。本增量不改变持久文件 schema 或已确认审批调用的恢复规则；内存 token 不跨服务重启持久化。

## 验证

`tests/test_streaming_cursor.py` 覆盖淘汰后续读、晚订阅、过期/超前/无效游标、节点关闭后再打开、流丢失与重建、HTTP 游标优先级、同一连接跨淘汰、终态竞争窗口，以及实际审批暂停后恢复只推送新节点。

`tests/frontend_streaming.test.cjs` 覆盖去重、缺口清空、流重建、其他运行的帧及实际 watchLiveTask 的 EventSource 接线。使用替身 EventSource，不代表浏览器视觉验收。既有审批竞争测试继续保留，CI 接入两个 Node 文件。

## 后续

M12-05 本机基线已完成，详见[持续运行记录](M12_RESOURCE_BASELINE.md)。下一阶段为 M13 工具型参考 Agent 与工作台闭环。真实模型、远端 CI、许可证及版本决策的门禁继续保留。

## 缓存容量与回收

TokenBus 默认上限：128 个缓存运行、每运行 10,000 条事件、单事件 262,144 字节、单运行 4,194,304 字节、全局 33,554,432 字节；结束运行保留 300 秒。字节统计包括 UTF-8 node_id 与文本，**不包括 Python 对象、运行结果、会话或审计的内存开销**，不是进程 RSS 上限。

SDK 可直接构造 TokenBus；应用在启动生产者之前可通过 `agent_core.streaming.configure_default_bus(...)` 配置全局总线。存在流记录时拒绝重新配置，不能在运行中替换流身份。保留期为有限非负秒数，容量为正整数，要求单事件 ≤ 单运行 ≤ 全局字节额度。

回收在 open/publish/read/history/stats/close 时触发，没有后台清理线程。空闲进程不会在 TTL 到点的瞬间主动释放，下一次访问才清理；缓存总量仍受容量上限约束。可用 stats 查看 runs、active_runs、events、payload_bytes。

模型节点关闭流时保持运行保护；Runtime 释放活动运行（完成、失败、取消或等待退出）后才开始保留计时。后续模型节点之间即使间隔很长，也不会因为节点 close 而过期。等待审批的缓存可过期，恢复后通过已有 gap 协议说明缺失，不影响持久审批事实或已确认工具结果。

运行数或全局字节不足时，优先回收最早结束的运行，允许早于 TTL 淘汰。不能整体淘汰活动运行；仍无法容纳时抛出 STREAM_CAPACITY_EXCEEDED，受管节点明确失败。单运行窗口按事件数及字节淘汰旧片段，保留稳定序号和缺口语义。拒绝新片段时不删除该流此前已接受的窗口。超过单事件上限返回 STREAM_EVENT_TOO_LARGE。

## 内置模型响应上限

OpenAICompatibleModel 新增 max_response_bytes（默认 10,000,000）与 max_stream_frame_bytes（默认 1,000,000）构造参数。普通 complete 与 complete_with_tools 都以有限读取检查响应字节；工具调用参数包含在 HTTP 响应的总额度内。SSE 使用有长度上限的 readline，检查累计读取字节和单帧（含行/空行分隔）的大小；注释和不含文本的帧也计入额度。

超限返回 MODEL_RESPONSE_TOO_LARGE，超限帧不进入 on_delta；此前已经发布的片段可保留为不完整预览，但运行失败，不重试部分输出，也不执行后续工具。仍沿用适配器超时和 Harness 预算/审计规则。回调处的流缓存额度是独立约束，可能先触发 STREAM_* 原因码。

这些限制适用于内置 HTTP 适配器及流缓存，不是任意自定义 Python 适配器、工具输出、整个进程或远端服务的强制沙箱。自定义适配器仍须约束其自身分配与返回值。

## 本机验证记录

2026-11-08，Windows / Python 3.13：完整 pytest 463 passed、unittest 463 tests / OK，前端 Node 11 项通过；离线工作流 28 次通过，重新构建的 wheel 独立安装检查通过。文档链接、JS 语法和 git diff --check 通过。没有执行远端 CI、外部模型质量验收或浏览器视觉验收。

同日 M12-03/04 验证：完整 pytest 478 passed、unittest 478 tests / OK，前端 11 项、离线工作流 28 次、重新构建 wheel 的独立安装检查通过。新增 test_stream_limits.py 的 15 项用例覆盖 TTL、UTF-8 计数、缓存压力、拒绝时保留原窗口、节点间保护与 Runtime 释放、启动配置、三种 HTTP 响应路径、部分输出不重试及后续副作用阻止。这是确定性契约验证，持续运行基线仍为待办。

同日 M12-05 收口：200 次顺序 HTTP 基线通过；完整 pytest 481 passed、unittest 481 tests / OK，前端 11 项通过。新增资源报告测试覆盖成功、模型任务失败和服务线程启动失败；文档链接及 git diff --check 通过。此处“完成”限于本机规定负载，远端 CI 尚未执行，长期/并发/真实模型负载不在本次结论内。
