# 架构决策记录-0003：采用真实 Dify Chatflow 与模块化实时语音运行时

> 状态：已接受
> 日期：2026-08-28
> 决策范围：实时协议网关、语音会话运行时、模型路由器、语音路由器
> 替代：架构决策记录-0002 中“当前 Dify 使用纯 Workflow App `/workflows/run`”的判断

## 背景

对真实 `chat-ai/common/config.ini` 和调用源码完成复核后，确认当前已部署 Dify 服务的基础地址是 `http://agent.gwcz.online/v1`，实际调用接口是 `POST /chat-messages`。该接口属于 Dify Chatflow/Advanced Chat Service API，不是纯 Workflow App 的 `POST /workflows/run`。

旧系统同时存在移动端 ASR 占位实现、腾讯 8k 文件识别、Dify 非真流式路径，以及火山 TTS 完整 MP3、Base64 和对象存储中转，无法直接作为低延迟 ESP32 全双工语音链路。

## 决策

1. 首期以 Dify Chatflow `POST /chat-messages` 为真实 LLM Provider 契约。
2. 每轮发送空 `conversation_id`，由 `xiaozhi-all` 显式传入角色、短期上下文和长期记忆。
3. 保留独立 `dify_workflow` 扩展位；只有部署纯 Workflow App 后才实现 `/workflows/run`。
4. 使用小智 WebSocket/Opus 协议作为 ESP32 首期设备协议。
5. 直接引入 Pipecat 作为实时 Frame 管线和语音会话编排基础，通过适配器接入小智协议。
6. ASR、LLM、TTS 分别通过语音路由器和模型路由器的稳定 Provider 契约接入。
7. 首期以一个 Python 进程部署模块化单体，不提前拆分微服务。
8. 腾讯实时 ASR 和火山双向流式 TTS 作为首期付费基线；FunASR、sherpa-onnx、CosyVoice 作为可替换本地候选。
9. 当前 Dify HTTP 地址只允许受控开发联调；生产发布前必须升级为可信 HTTPS。

## 影响

- 收益：真实接口与实现一致；可以立即验证真 SSE、打断和 TTFT；ESP32、Web 和 App 可共享会话核心。
- 代价：需要实现小智协议到 Pipecat Frame 的适配，以及腾讯、火山和 Dify 的独立 Provider。
- 风险：Dify HTTPS 改造是生产发布阻断项；Pipecat 升级必须通过契约和真机回归。
- 回滚：通过组合根切换备用 LLM、ASR 或 TTS Provider，不修改设备协议和会话状态机。

## 验证

- Dify 使用 `httpx.AsyncClient.stream()` 产生真实首文本增量。
- ESP32 真机完成 Opus 上行、ASR、Dify、TTS、Opus 下行和用户打断。
- 用户打断后旧 `generation_id` 的文本和音频不会进入新轮次、历史或记忆。
- 100 轮真机对话满足首音、打断、成功率和稳定性门禁。
- 架构契约测试证明核心模块不依赖具体供应商 SDK。

## 关联方案

- [最小化实时语音全链路实施方案](../最小化实时语音全链路实施方案.md)
- [架构决策记录-0001](架构决策-0001-终端无关流式架构.md)
- [架构决策记录-0002](架构决策-0002-无状态Dify工作流提供方.md)
