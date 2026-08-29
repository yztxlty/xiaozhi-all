# OpenAI 兼容模型提供方技术设计

## 目标

在不修改实时会话、ASR 和 TTS 契约的前提下，引入可替换的 OpenAI 兼容模型提供方。首个接入目标为 DeepSeek，Dify 实现继续保留，通过环境变量切换。

## 架构决策

- `model-router` 内新增 `openai_compatible` Provider，负责请求映射、SSE 解析、错误映射和取消传播。
- `realtime-server` 作为组合根，只根据 `LLM_PROVIDER` 选择具体 Provider，不解析任何 DeepSeek 协议。
- DeepSeek 使用 `POST /chat/completions`、`stream=true`；语音场景默认 `deepseek-v4-flash`，并设置 `thinking.type=disabled` 降低首字延迟。
- Provider 继续输出统一的 `LLMStarted`、`LLMTextDelta`、`LLMCompleted`、`LLMFailed`、`LLMCancelled` 事件。
- LLM 文本增量持续进入现有按句 TTS 队列，实现 LLM 与 TTS 流水并行。

## 配置契约

```dotenv
LLM_PROVIDER=deepseek
OPENAI_COMPATIBLE_BASE_URL=https://api.deepseek.com
OPENAI_COMPATIBLE_API_KEY=本地密钥
OPENAI_COMPATIBLE_MODEL=deepseek-v4-flash
OPENAI_COMPATIBLE_THINKING=disabled
```

切回 Dify 时只需设置：

```dotenv
LLM_PROVIDER=dify
```

## 请求映射

- `role_profile` 映射为系统消息。
- `short_history` 中合法的 `system/user/assistant` 消息按顺序映射。
- 当前 `user_text` 作为最后一条用户消息。
- 不把模型思考内容发送给 TTS，也不写入短期上下文。

## 失败与取消

- 鉴权、限流、上游故障、连接超时和协议错误映射为稳定错误码。
- 用户打断后关闭 SSE 响应并输出 `LLMCancelled`，现有 TTS 任务同时收到共享取消信号。
- 密钥仅保存在被 Git 忽略的根目录 `.env`，不得写入源码、测试夹具或文档。

## 验收

1. Provider 契约测试验证有序增量、完成、失败和取消。
2. 真实 DeepSeek 请求收到至少一个文本增量。
3. H5 文字输入收到 DeepSeek 增量和 TTS PCM 音频。
4. H5 语音输入完成 ASR、DeepSeek、TTS 闭环。
5. 打断后不再发送新的文本或音频帧。
