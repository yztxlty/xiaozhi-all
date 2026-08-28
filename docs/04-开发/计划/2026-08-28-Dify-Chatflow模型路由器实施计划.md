# Dify Chatflow 模型路由器实施计划

> **执行要求：** 使用 `superpowers:executing-plans` 逐项执行；所有新增行为严格执行测试先失败、最小实现、测试通过、再重构。步骤使用复选框（`- [ ]`）记录进度。

**目标：** 将现有错误的纯 Workflow `/workflows/run` 实现替换为符合真实 `chat` 项目 `/chat-messages` 契约的无状态、真流式、可取消 Dify Chatflow Provider，并建立高内聚、低耦合的模型路由器框架。

**架构：** 公共 LLM 契约、路由、能力和健康检查位于 `model_router/core`；Dify 的配置、旧变量兼容、SSE、HTTP、取消、错误和指标全部位于 `providers/dify_chatflow`。`application.py` 是组合根，核心代码不得导入具体 Provider。

**技术栈：** Python 3.12、asyncio、httpx、Pydantic 2、pydantic-settings、pytest、pytest-asyncio、httpx MockTransport、Prometheus Client。

## 全局约束

- 真实接口固定为 `POST /v1/chat-messages`，停止接口为 `POST /v1/chat-messages/{task_id}/stop`。
- 每轮 `conversation_id` 固定为空，不使用 Dify 保存上下文。
- 角色、短期历史和长期记忆由 `LLMRequest` 输入，由 Provider 映射为真实旧 Dify 变量。
- 必须使用 `AsyncClient.stream()`，禁止先缓冲完整响应。
- 首个文本增量前最多重试一次；首个文本增量后禁止重试和拼接备用回复。
- 取消先完成本地终止，再尽力调用 Dify Stop API；迟到事件全部丢弃。
- API Key 只能使用 `SecretStr` 和环境变量，禁止进入日志、测试夹具和 Git。
- 当前 HTTP 基础地址仅用于受控联调，生产启用必须为 HTTPS。
- 本计划只交付模型路由器和真实 Dify 链路；ASR、TTS、Pipecat 会话运行时和小智网关分别使用后续独立计划实施。

---

### 任务 1：建立模型路由器核心边界

**文件：**

- 新建：`services/model-router/src/model_router/core/__init__.py`
- 新建：`services/model-router/src/model_router/core/contracts.py`
- 新建：`services/model-router/src/model_router/core/router.py`
- 新建：`services/model-router/src/model_router/core/capability.py`
- 新建：`services/model-router/src/model_router/core/health.py`
- 修改：`services/model-router/src/model_router/contracts.py`
- 修改：`services/model-router/src/model_router/router.py`
- 测试：`services/model-router/tests/contract/test_architecture_boundaries.py`
- 测试：`services/model-router/tests/contract/test_llm_contracts.py`

**接口：**

- 继续提供 `LLMRequest`、`LLMStarted`、`LLMTextDelta`、`LLMCompleted`、`LLMFailed`、`LLMCancelled`、`LLMProvider`。
- 新增 `LLMCapability(provider_id, streaming_output, cancel_supported, conversation_state)`。
- 新增 `ProviderHealth(provider_id, healthy, detail)`。
- 根目录 `contracts.py` 和 `router.py` 只做兼容导出，不包含实现。

- [x] **步骤 1：编写架构边界失败测试**

测试必须断言 `model_router.core` 不导入 `providers`，并能从 `model_router.core` 导入公共契约、路由、能力和健康模型。

- [x] **步骤 2：运行失败测试**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_architecture_boundaries.py services/model-router/tests/contract/test_llm_contracts.py -q
```

预期：因 `model_router.core` 不存在而失败。

- [x] **步骤 3：移动公共实现并保留兼容导出**

`core/__init__.py` 只导出稳定公共类型；根目录模块使用显式 `from .core... import ...`，禁止通配符导入。

- [x] **步骤 4：运行核心契约和全量回归**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_architecture_boundaries.py services/model-router/tests/contract/test_llm_contracts.py -q
.venv/bin/pytest services/model-router/tests -q
```

预期：全部通过。

- [x] **步骤 5：提交**

```bash
git add services/model-router/src/model_router services/model-router/tests/contract
git commit -m "refactor: establish model router core boundary"
```

### 任务 2：实现真实 Chatflow 配置和输入映射

**文件：**

- 新建：`services/model-router/src/model_router/providers/dify_chatflow/__init__.py`
- 新建：`services/model-router/src/model_router/providers/dify_chatflow/config.py`
- 新建：`services/model-router/src/model_router/providers/dify_chatflow/input_mapper.py`
- 测试：`services/model-router/tests/contract/test_dify_chatflow_input_contract.py`

**接口：**

```python
class DifyChatflowSettings(BaseSettings):
    base_url: HttpUrl
    api_key: SecretStr
    provider_id: str = "dify-chatflow-primary"

def map_chatflow_request(request: LLMRequest) -> dict[str, object]: ...
```

输出请求必须包含 `query`、`user`、`conversation_id=""`、`response_mode="streaming"`、`inputs` 和 `auto_generate_name=False`。

- [ ] **步骤 1：编写失败测试**

覆盖真实路径配置、API Key 脱敏、空 `conversation_id`、用户问题映射，以及旧 Dify 的 `person_name`、`character`、`memory`、`session_id`、`today_date` 输入。

- [ ] **步骤 2：确认测试因模块不存在而失败**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_dify_chatflow_input_contract.py -q
```

- [ ] **步骤 3：实现最小配置和映射**

旧变量只在映射器内生成；`memory` 使用短期历史和长期记忆的紧凑 JSON，最多 8,000 个字符；密钥使用 `SecretStr`。

- [ ] **步骤 4：运行测试和回归**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_dify_chatflow_input_contract.py services/model-router/tests/contract/test_llm_contracts.py -q
```

- [ ] **步骤 5：提交**

```bash
git add services/model-router/src/model_router/providers/dify_chatflow services/model-router/tests/contract/test_dify_chatflow_input_contract.py
git commit -m "feat: add stateless Dify Chatflow request mapping"
```

### 任务 3：实现 Chatflow SSE 和真流式客户端

**文件：**

- 新建：`services/model-router/src/model_router/providers/dify_chatflow/event_parser.py`
- 新建：`services/model-router/src/model_router/providers/dify_chatflow/errors.py`
- 新建：`services/model-router/src/model_router/providers/dify_chatflow/client.py`
- 测试：`services/model-router/tests/contract/test_dify_chatflow_sse_contract.py`
- 测试：`services/model-router/tests/integration/test_dify_chatflow_streaming.py`
- 测试：`services/model-router/tests/integration/test_dify_chatflow_cancellation.py`

**接口：**

```python
def parse_sse_line(line: str) -> DifyChatflowEvent | None: ...

class DifyChatflowClient:
    async def stream(self, payload: dict[str, object]) -> AsyncIterator[DifyChatflowEvent]: ...
    async def stop(self, task_id: str, user: str) -> None: ...
```

- [ ] **步骤 1：编写 SSE 与 HTTP 失败测试**

覆盖 `message`、`agent_message`、`message_end`、`workflow_started`、`workflow_finished`、`error`、`ping`、非法 JSON、非 SSE、首增量前 429 重试和首增量后断流不重试。

- [ ] **步骤 2：运行测试确认失败原因正确**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_dify_chatflow_sse_contract.py services/model-router/tests/integration/test_dify_chatflow_streaming.py services/model-router/tests/integration/test_dify_chatflow_cancellation.py -q
```

- [ ] **步骤 3：实现真流式客户端**

使用 `async with http.stream("POST", url, ...)` 和 `response.aiter_lines()`；文本增量取顶层 `answer`；停止路径固定为 `chat-messages/{task_id}/stop`。

- [ ] **步骤 4：运行目标测试和全量回归**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_dify_chatflow_sse_contract.py services/model-router/tests/integration/test_dify_chatflow_streaming.py services/model-router/tests/integration/test_dify_chatflow_cancellation.py -q
.venv/bin/pytest services/model-router/tests -q
```

- [ ] **步骤 5：提交**

```bash
git add services/model-router/src/model_router/providers/dify_chatflow services/model-router/tests
git commit -m "feat: stream and cancel Dify Chatflow requests"
```

### 任务 4：实现 Chatflow Provider、指标和组合根

**文件：**

- 新建：`services/model-router/src/model_router/providers/dify_chatflow/provider.py`
- 新建：`services/model-router/src/model_router/providers/dify_chatflow/metrics.py`
- 修改：`services/model-router/src/model_router/application.py`
- 修改：`services/model-router/src/model_router/cli.py`
- 测试：`services/model-router/tests/contract/test_dify_chatflow_provider_contract.py`
- 测试：`services/model-router/tests/contract/test_dify_chatflow_observability.py`
- 测试：`services/model-router/tests/integration/test_dify_chatflow_end_to_end.py`

**接口：**

```python
def create_dify_chatflow_router(
    settings: DifyChatflowSettings,
    http: httpx.AsyncClient,
) -> LLMRouter: ...
```

- [ ] **步骤 1：编写 Provider 失败测试**

断言事件顺序为 `LLMStarted → LLMTextDelta* → 单一终态`；取消后停止远端任务并丢弃迟到事件；指标不包含用户正文和密钥。

- [ ] **步骤 2：运行失败测试**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_dify_chatflow_provider_contract.py services/model-router/tests/contract/test_dify_chatflow_observability.py services/model-router/tests/integration/test_dify_chatflow_end_to_end.py -q
```

- [ ] **步骤 3：实现 Provider 和组合根**

`application.py` 是唯一导入 `DifyChatflowProvider` 的组合根；CLI 输出事件类型和耗时，不输出提示词、密钥和完整用户正文。

- [ ] **步骤 4：运行目标测试与全量测试**

```bash
.venv/bin/pytest services/model-router/tests -q
```

- [ ] **步骤 5：提交**

```bash
git add services/model-router
git commit -m "feat: compose Dify Chatflow model provider"
```

### 任务 5：删除错误 Workflow 实现并完成真实服务验证

**文件：**

- 删除：`services/model-router/src/model_router/providers/dify_workflow/`
- 删除：旧 `services/model-router/tests/*dify*` 中仅覆盖 `/workflows/run` 的测试和夹具
- 修改：`services/model-router/.env.example`
- 修改：`services/model-router/说明.md`
- 新建：`services/model-router/tests/real_service/test_dify_chatflow_real.py`
- 新建：`docs/05-测试/Dify-Chatflow真实服务验收记录.md`

**真实验证：**

- 从本机 `chat-ai/common/config.ini` 读取 Dify 地址和应用密钥，但不复制到仓库。
- 只发送一条无隐私测试文本，记录 HTTP 状态、事件类型、首增量耗时和总耗时。
- 测试默认跳过，只有显式设置 `RUN_DIFY_REAL_TEST=1` 才访问真实服务。

- [ ] **步骤 1：先增加迁移保护测试**

断言源码不再包含 `workflows/run`、`DIFY_WORKFLOW_` 或 `providers.dify_workflow`，并断言 `.env.example` 只有 Chatflow 变量名。

- [ ] **步骤 2：确认保护测试在删除前失败**

```bash
.venv/bin/pytest services/model-router/tests/contract/test_architecture_boundaries.py -q
```

- [ ] **步骤 3：删除旧实现并更新说明**

删除错误目录、测试和夹具；保留公共 LLM 契约、路由和降级测试。

- [ ] **步骤 4：运行静态和自动化验证**

```bash
.venv/bin/pytest services/model-router/tests -q
git diff --check
rg -n "workflows/run|DIFY_WORKFLOW_|providers\.dify_workflow" services/model-router
```

预期：测试全部通过，`rg` 无结果。

- [ ] **步骤 5：运行真实 Dify 冒烟测试**

```bash
RUN_DIFY_REAL_TEST=1 .venv/bin/pytest services/model-router/tests/real_service/test_dify_chatflow_real.py -q -s
```

预期：收到 `message` 或 `agent_message` 增量及 `message_end`，输出不包含密钥。

- [ ] **步骤 6：记录真实验收证据并提交**

```bash
git add services/model-router docs/05-测试/Dify-Chatflow真实服务验收记录.md
git commit -m "test: verify real Dify Chatflow integration"
```

## 最终验证

```bash
.venv/bin/pytest services/model-router/tests -q
git diff --check
git status --short --branch
```

最终检查：测试全部通过；只存在计划内文件；没有密钥、临时日志、缓存、`.env` 或 `config.ini`；当前分支可以安全合并回主工作区。
