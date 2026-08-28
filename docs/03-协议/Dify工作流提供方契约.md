# Dify 工作流模型提供方契约

> 状态：草案（未来纯 Workflow App 可选 Provider，非当前 MVP 基线）
> 版本：v0.1
> 更新日期：2026-08-28
> 责任模块：大语言模型路由器
> 架构决策记录：0002

> 当前真实 `chat` 项目使用 Dify Chatflow 的 `POST /chat-messages`。ESP32 最小闭环的 Dify 请求、事件和取消契约以[最小化实时语音全链路实施方案](../02-架构/最小化实时语音全链路实施方案.md)为准。

## 背景与目标

定义 `xiaozhi-all` 与已经部署好的 Dify 工作流服务之间的稳定边界，使 Dify 实现可以替换、测试和降级，并且不改变语音会话运行时、终端协议或会话服务。

## 范围与非目标

- 覆盖内部大语言模型请求、Dify HTTP服务端事件流请求、事件映射、取消、错误和兼容规则。
- 不定义 Dify 的部署、扩缩容、数据库、执行进程、模型插件或管理后台。
- 不允许 Dify 成为对话历史和长期记忆事实源。

## 模型提供方能力声明

以下内容属于程序接口字段，按兼容要求保留原文：

```json
{
  "provider": "dify-workflow-primary",
  "kind": "llm",
  "streaming_input": false,
  "streaming_output": true,
  "cancel_supported": true,
  "conversation_state": "stateless",
  "tool_calls": "workflow-managed",
  "timeout_ms": 20000
}
```

## 内部请求

```json
{
  "session_id": "s_01J6D8J37N8B5Y",
  "turn_id": "t_01J6D8J6QAB31F",
  "generation_id": "g_01J6D8J8X5AQ52",
  "user_id": "usr_opaque_52d7",
  "device_id": "dev_opaque_a921",
  "user_text": "今天有点累。",
  "role_profile": {
    "name": "幽光",
    "persona": "温暖、自然、不过度说教",
    "relationship": "长期陪伴者"
  },
  "short_history": [
    {"role": "user", "content": "今天工作很多。"},
    {"role": "assistant", "content": "听起来你忙了一整天。"}
  ],
  "long_memories": [
    {"id": "mem_01", "content": "用户周五通常工作较忙", "score": 0.91}
  ],
  "scene": "companion_chat",
  "locale": "zh-CN",
  "response_style": {
    "spoken_language": true,
    "avoid_markdown": true,
    "max_sentences": 4
  }
}
```

### 字段规则

| 字段 | 规则 |
| --- | --- |
| `session_id` | 必填；会话内稳定，不包含用户隐私 |
| `turn_id` | 必填；每个用户轮次唯一 |
| `generation_id` | 必填；每次生成唯一，重试或降级必须更新 |
| `user_id` | 必填；内部匿名标识，不使用手机号、邮箱或开放平台标识 |
| `device_id` | 可选；内部匿名标识，仅用于路由和观测 |
| `user_text` | 必填；去除无意义空白，最多 1,000 个词元 |
| `role_profile` | 必填；最多 1,500 个词元，必须通过服务端结构校验 |
| `short_history` | 最多 8 个轮次、4,000 个词元，只包含最终用户文本和实际听到的人工智能回复文本 |
| `long_memories` | 最多 8 条、1,500 个词元，按相关度降序 |
| `scene` | 枚举：`companion_chat`、`knowledge_qa`、`tool_task` |
| `locale` | 使用 BCP 47 语言标签，最小可行版本默认 `zh-CN` |
| `response_style` | 服务端生成的受控结构，客户端不得覆盖系统安全规则 |

总输入预算为 8,000 个词元。超出时由会话服务在调用模型提供方前完成裁剪，适配器不自行摘要或调用额外模型。

## Dify 请求映射

请求：

```http
POST {DIFY_WORKFLOW_BASE_URL}/workflows/run
Authorization: Bearer {DIFY_WORKFLOW_API_KEY}
Content-Type: application/json
Accept: text/event-stream
```

请求体：

```json
{
  "inputs": {
    "session_id": "s_01J6D8J37N8B5Y",
    "turn_id": "t_01J6D8J6QAB31F",
    "generation_id": "g_01J6D8J8X5AQ52",
    "user_text": "今天有点累。",
    "role_profile_json": "{...}",
    "short_history_json": "[...]",
    "long_memories_json": "[...]",
    "scene": "companion_chat",
    "locale": "zh-CN",
    "response_style_json": "{...}"
  },
  "response_mode": "streaming",
  "user": "usr_opaque_52d7"
}
```

规则：

- 必须使用 `response_mode=streaming`。
- 工作流接口请求中不得出现 `conversation_id`。
- JSON 变量必须使用 UTF-8、确定性序列化和紧凑分隔符。
- `user` 必须是稳定匿名标识，并在启动和停止请求中保持一致。
- 鉴权请求头不得进入日志、链路追踪或错误详情。

## 服务端事件流解析

每个 `data: ` 行包含一个 JSON 事件，空行作为事件分隔；裸 `event: ping` 只用于保活。

| Dify 事件 | 内部事件 | 处理 |
| --- | --- | --- |
| `ping` | 无 | 忽略 |
| `workflow_started` | `llm.started` | 保存 `task_id`、`workflow_run_id` |
| `node_started` | 无 | 仅记录节点类型和耗时，不记录输入正文 |
| `text_chunk` | `llm.text.delta` | 提取 `data.text`，为空则忽略 |
| `reasoning_chunk` | 无 | 不下发、不存正文，只计数 |
| `node_finished` 成功 | 无 | 更新内部节点指标 |
| `node_finished` 失败 | `llm.failed` | 进入失败终态 |
| `workflow_finished` 成功 | `llm.completed` | 提取用量、输出和结束原因 |
| `workflow_finished` 失败或停止 | `llm.failed` 或 `llm.cancelled` | 按当前生成任务状态映射 |
| `error` | `llm.failed` | 提取安全错误码，不透传敏感详情 |
| `human_input_required` 或 `workflow_paused` | `llm.failed` | 错误码为 `DIFY_UNSUPPORTED_PAUSE` |
| 未知事件 | 无 | 记录事件名并继续，不记录完整载荷 |

### 文本增量顺序

适配器从 1 开始为非空 `text_chunk` 分配连续 `sequence`：

```json
{
  "type": "llm.text.delta",
  "session_id": "s_01J6D8J37N8B5Y",
  "turn_id": "t_01J6D8J6QAB31F",
  "generation_id": "g_01J6D8J8X5AQ52",
  "provider": "dify-workflow-primary",
  "sequence": 1,
  "text": "听起来"
}
```

下游必须同时校验 `generation_id` 和 `sequence`。同一生成任务的重复序号应丢弃，缺失序号触发失败，不允许乱序拼接。

## 完成事件

```json
{
  "type": "llm.completed",
  "session_id": "s_01J6D8J37N8B5Y",
  "turn_id": "t_01J6D8J6QAB31F",
  "generation_id": "g_01J6D8J8X5AQ52",
  "provider": "dify-workflow-primary",
  "finish_reason": "stop",
  "reply_text": "听起来你真的累了，我们先慢下来歇一会儿。",
  "metadata": {
    "emotion": "warm",
    "memory_candidates": [],
    "tool_results": []
  },
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

- `reply_text` 必须等于有序文本增量的拼接结果；不一致时以文本增量拼接结果为用户可见事实，并记录契约错误。
- `memory_candidates` 只是候选，必须由记忆服务校验后才能持久化。
- `tool_results` 只能包含允许下发的结构化结果，不包含工具凭证或原始响应头。

## 取消契约

收到 `generation.cancel` 后：

1. 原子地将生成任务标记为 `cancelled`。
2. 立即停止向下游发布文本增量。
3. 关闭或取消本地服务端事件流消费任务。
4. 如果已获得 `task_id`，在独立的 500 毫秒超时期限内调用停止接口。
5. 停止接口成功、失败或超时都不能改变本地已取消状态。
6. 取消后的迟到事件不得进入语音合成、历史、记忆和用户计费展示；提供方实际产生的成本仍进入内部成本指标。

停止请求：

```http
POST {DIFY_WORKFLOW_BASE_URL}/workflows/tasks/{task_id}/stop
Authorization: Bearer {DIFY_WORKFLOW_API_KEY}
Content-Type: application/json

{"user":"usr_opaque_52d7"}
```

## 重试和降级契约

- 只有在尚未发布文本增量时允许适配器网络重试，最大 1 次。
- 可重试：连接失败、429、502、503、504。
- 不可重试：400、401、403、404、结构错误、工作流暂停、用户取消。
- 已发布文本增量后发生断流，当前生成任务失败，不允许在同一生成任务拼接重试结果。
- 大语言模型路由器若切换备用模型提供方，必须创建新的 `generation_id`，并记录原提供方、目标提供方、原因和附加延迟。
- 用户取消不触发重试或降级。

## 错误模型

```json
{
  "type": "llm.failed",
  "session_id": "s_01J6D8J37N8B5Y",
  "turn_id": "t_01J6D8J6QAB31F",
  "generation_id": "g_01J6D8J8X5AQ52",
  "provider": "dify-workflow-primary",
  "code": "DIFY_STREAM_DISCONNECTED",
  "retryable": false,
  "delta_emitted": true
}
```

固定错误码：

| 错误码 | 含义 | 默认可重试 |
| --- | --- | --- |
| `DIFY_CONNECT_FAILED` | 无法建立连接 | 是，首个文本增量前 |
| `DIFY_CONNECT_TIMEOUT` | 连接超时 | 是，首个文本增量前 |
| `DIFY_READ_TIMEOUT` | 服务端事件流空闲超时 | 仅首个文本增量前 |
| `DIFY_TOTAL_TIMEOUT` | 单轮总超时 | 否 |
| `DIFY_RATE_LIMITED` | HTTP 429 | 是，首个文本增量前 |
| `DIFY_AUTH_FAILED` | HTTP 401 或 403 | 否 |
| `DIFY_BAD_REQUEST` | HTTP 400、404 或输入不匹配 | 否 |
| `DIFY_UPSTREAM_FAILED` | HTTP 502、503 或 504 | 是，首个文本增量前 |
| `DIFY_WORKFLOW_FAILED` | 工作流或节点失败 | 否 |
| `DIFY_STREAM_DISCONNECTED` | 流中途断开 | 否 |
| `DIFY_PROTOCOL_ERROR` | 服务端事件流、JSON 或字段违反契约 | 否 |
| `DIFY_UNSUPPORTED_PAUSE` | 工作流进入人工输入暂停 | 否 |
| `DIFY_CANCELLED` | 用户或上游取消 | 否 |

错误响应不得包含接口密钥、鉴权请求头、完整输入上下文、Dify 原始堆栈或用户隐私文本。

## 兼容与变更

- 当前契约版本为 `dify-workflow-provider/v1`。
- 新增可选输入或忽略型事件属于向后兼容变更。
- 删除字段、修改字段类型、改变文本增量拼接规则或取消语义属于不兼容变更，必须新增契约版本和架构决策记录。
- 每次发布 Dify 工作流必须运行契约、取消、一百轮延迟和安全扫描；测试结果关联 Dify 发布时间与模型路由器提交。

## 验收标准

- 请求中不存在 `conversation_id`。
- 首个服务端事件流 `text_chunk` 不等待完成事件即可输出统一文本增量。
- 取消、失败和完成是互斥终态，每个生成任务只能出现一个终态。
- 已有文本增量后断流不会自动重试或拼接第二份回复。
- Dify 原始事件夹具和真实测试环境均通过相同契约测试。
- 所有错误路径都不泄露密钥、提示词、记忆正文和推理内容。

## 关联资料

- [Dify 工作流模型提供方技术设计](../02-架构/Dify工作流提供方技术设计.md)
- [架构决策记录-0002](../02-架构/架构决策/架构决策-0002-无状态Dify工作流提供方.md)
- [模型提供方公共契约](模型提供方契约.md)
- [实时会话协议](实时会话协议.md)
