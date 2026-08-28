# Dify Chatflow 真实服务验收记录

> 状态：基线生效  
> 版本：v0.1  
> 更新日期：2026-08-28  
> 验收范围：`xiaozhi-all` 模型路由器真实 Dify Chatflow 冒烟

## 一、测试环境

- 服务地址来源：`/Users/yuezhenting/gaowei/chat/chat-ai/common/config.ini` 的 `[dify] url`
- 请求路径：`POST /v1/chat-messages`
- 请求模式：`streaming`
- `conversation_id`：空字符串
- 网络代理：关闭环境代理继承，避免本机代理影响结果
- 测试文本：固定无隐私冒烟文本
- 密钥：仅从本地配置读取到内存，未写入仓库、日志或本文档

## 二、候选应用验证

| 配置候选 | HTTP 状态 | 结果 | 说明 |
| --- | ---: | --- | --- |
| `group_theater_chatflow_secret_key` | 400 | 未选用 | 应用变量契约与当前通用请求不匹配 |
| `ai_prompt_secret_key` | 200 | 通过 | 当前通用语音对话候选应用 |
| `api_key` | 401 | 未选用 | 不是当前 Chatflow 应用的有效密钥 |

## 三、通过结果

通过候选返回事件：

```text
agent_message → message_end
```

本次单样本测得：

- HTTP 状态：`200`
- 首个有效文本增量：约 `1670 ms`
- `message_end`：已收到
- 请求路径：正确
- 密钥泄露：未发现
- 结果：通过

该延迟是单次开发环境样本，不能作为性能基线。正式性能结论必须使用固定语料、多轮样本和 `P50/P95/P99` 统计。

## 四、后续动作

1. 将部署配置中的 Dify Chatflow 应用密钥按应用语义映射到 `DIFY_CHATFLOW_API_KEY`，不改变代码中的密钥字段。
2. 在接入 ASR/TTS 前，继续使用该候选完成取消和迟到事件验证。
3. Dify 基础地址当前为 HTTP，仅允许受控开发联调；生产必须切换 HTTPS。
