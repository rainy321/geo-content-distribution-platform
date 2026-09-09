# 当前配置模型真实采样契约

`POST /api/geo/visibility/sample` 是 Windows EXE 本地后端的受控采样入口。它只调用当前请求指定或本机环境配置的一个 OpenAI-compatible 模型，不会调用媒体发布平台，也不会修改服务器端网站。

## 外发数据边界

每次模型请求的 `messages` 只有一个 `user` 消息，其 `content` 是请求中的 `prompt` 原文。传输层仍必须携带模型标识、该 AI 服务自身的鉴权头、温度和最大 Token 参数；这些不是业务上下文。以下业务内容仅留在本机用于回答分析，不发送给模型：

- 项目名称、产品、项目介绍、官网和历史文章；
- `aliases` 和 `competitors`；
- 媒体账号、Cookie、登录态和发布任务；
- 其他项目或客户资料。

接口不会传入网页搜索工具、检索上下文或引用材料。因此返回值中的 `grounding.status` 固定为 `not_requested_unverified`：不能把模型给出的链接或时效性事实描述成“已联网核验”。

## 请求

必须同时提供稳定的请求标识和幂等键：

```http
X-Request-ID: visibility-ui-20260910-001
Idempotency-Key: visibility-sample-20260910-001
Content-Type: application/json
```

```json
{
  "project_id": 1,
  "prompt": "中国中小企业想部署 AI 助手，有哪些产品值得考虑？",
  "sample_count": 3,
  "aliases": ["DongClaw"],
  "competitors": ["RanClaw"],
  "persist": true,
  "confirm_external_call": true,
  "ai_config": {
    "base_url": "https://provider.example/v1",
    "api_key": "只从当前前端会话传入，不保存",
    "model": "provider-model-id"
  }
}
```

- `sample_count` 只能是 1 到 5；同一个问题按顺序调用，禁止并发突发。
- `confirm_external_call` 必须严格为 `true`。
- `ai_config` 可省略；省略时读取本机 `AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL`。请求级密钥不会写入数据库或响应。
- Qwen、DeepSeek、豆包只有在各自服务提供 OpenAI-compatible Chat Completions 地址、有效模型名和密钥时才能分别采样；切换供应商必须使用新的幂等键。

## 幂等与失败

系统在第一次模型调用前写入 `ai_visibility_sample_requests`：

- 同一幂等键、相同参数：返回首次保存的完整结果，响应头 `Idempotency-Replayed: true`，不会再次调用模型或重复计费；
- 同一幂等键、不同参数/模型：`409`；
- 上一次进程中断且状态仍为 `started`：`409 need_action`，不自动重发。先核对模型账单和原请求，再决定是否使用新键；
- 第一个样本失败：返回 `need_action`，HTTP 状态按限流、超时或服务错误区分；
- 已有回答后失败：返回 `207 partial`，保留已完成回答、分析与费用凭据，并停止其余样本；
- 读取超时或连接中断导致远端结果未知：`failure.outcome_unknown=true`，绝不自动重试。

即使 `persist=false`，幂等请求结果仍会保存在本机请求表中，以避免重试误扣费；该选项只控制是否另存为可见度历史 `ai_visibility_runs`。

## 结果、成本与历史

响应中的关键字段：

- `data.data_disclosure`：本次允许外发和明确未外发的数据清单、prompt SHA-256；
- `data.grounding`：网页搜索和引用核验边界；
- `data.executions[]`：供应商、实际模型、供应商请求号、耗时、reported/unknown 用量及成本凭据；
- `data.captured_responses[]`：本次取得的原始回答，仅在本地响应和幂等记录中保存；
- `data.analysis`：复用 `ai-visibility-python-port` 的提及、排名、引用、情感、SOV 和置信度分析；
- `data.run_id`：`persist=true` 且历史保存成功时返回，可通过现有 `/api/geo/visibility/runs` 查询。

每个成功取得的模型回答写一条 `operation=visibility_sample` 的 AI 成本凭据。供应商未返回 usage 时记为 `unknown`，价格目录未配置时记为 `unpriced`，两者都不会伪装成 0 Token 或 0 成本。平台发布本身仍不计 Token。

## 测试边界

自动化测试全部使用本地假响应，不向任何真实模型发送内容。真实验收必须在 EXE 中由操作员预览 prompt、确认本次外发并提供当前会话的供应商配置后触发。
