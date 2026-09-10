# AI 可见度集成说明

## 1. 能力边界

当前 Windows EXE 的 AI 可见度有两条彼此独立的入口：

1. “回答导入 + 本机确定性分析”：操作员粘贴已经取得的回答，`POST /api/geo/visibility/analyze` 不访问外部模型。
2. “当前配置模型受控采样 + 本机确定性分析”：操作员在 EXE 中预览并确认后，`POST /api/geo/visibility/sample` 对一个 OpenAI-compatible 模型顺序调用 1–5 次。每次外发的唯一自然语言内容都是操作员填写的 `prompt`；品牌项目、产品、官网、文章、别名、竞品、媒体账号、Cookie、凭据和发布任务不外发。

已实现：

- 品牌主名、产品别名、自定义别名、竞品和域名实体构建。
- 精确、别名、域名和受限模糊匹配，以及可回看证据片段。
- 编号/项目符号推荐列表的位次解析。
- 显式 URL 规范化、去除追踪参数、注册域判断和来源分类。
- 品牌/竞品提及率、推荐位次加权 SOV、引用率、情感与置信度。
- 基于样本证据的规则建议。
- 可选本机 SQLite 历史、摘要列表和完整回放。
- 上游交接包、源文件哈希和本机集成版本溯源。
- 请求级当前模型配置、明确外发确认和逐次 1–5 次顺序采样。
- 首次调用前持久化的请求 ID/幂等键、结果回放和模糊结果禁止自动重试。
- 真实回答逐次 Token/成本凭据；provider 未返回 usage 或未配置价格时分别显示“未知”或“未定价”，不写成 0。

未实现：

- 自动登录千问、ChatGPT 或其他 provider 的网页账号。
- 定时采样、后台无人值守采样、分布式 worker/队列和多 provider 并发突发。
- 跨 provider 权重、跨时间趋势和包含全部失败执行的 provider 总体成功率。
- 联网验证 URL、抓取页面内容、跟随重定向或判断页面真实性。
- 多用户租户、云端同步和远程数据仓库。

手工导入接口中的 `responses[].provider` 和 `responses[].model` 仅作为来源标签原样保存和回显，不参与网络连接，也不证明该回答确实来自所标模型。受控采样接口中的 provider/model 来自本次实际连接配置与 provider 响应，但仍不等于该回答经过网页搜索、事实或引用核验。

当前实现是经过独立标识的 Python 移植与本机临时统计口径。其结果不能与 provider grounding 运行、包含失败执行的总体，或历史 TypeScript 引擎序列直接拼接比较。

不要混淆三条接口链路：`/api/geo/visibility/analyze` 只做手工回答的本机分析；`/api/geo/visibility/sample` 只把明确确认的 `prompt` 发送给当前配置模型，再把回答交给同一确定性分析器；内容创作适配层向同事服务约定的 `POST /api/v1/generate` 是另一条出站生成链路。可见度采样不会调用媒体平台，也不是网页账号自动化。

项目/内容五维诊断和 AI 调用成本台账是建立在这些边界之上的独立产品接口，不改变可见度样本语义。受控采样的详细外发、幂等与失败契约见 [当前配置模型真实采样契约](./visibility-live-sampling.md)；诊断、复测摘要和 Token/价格快照规则见 [GEO 诊断闭环与 AI 成本台账契约](./geo-diagnostic-cost-ledger.md)。

## 2. 组件关系

```text
AI 可见度页面
  ├─ POST /api/geo/visibility/analyze
  │    ├─ 读取本机 projects 品牌快照
  │    ├─ services.ai_visibility_service（纯分析，无网络/数据库依赖）
  │    └─ persist=true 时：services.ai_visibility_run_service
  │         └─ SQLite ai_visibility_runs
  └─ POST /api/geo/visibility/sample
       ├─ 明确确认 + 当前模型配置 + 稳定请求 ID/幂等键
       ├─ services.ai_visibility_sampling_service（1–5 次顺序调用、失败即停）
       ├─ SQLite ai_visibility_sample_requests（首次调用前落盘、防重复扣费）
       ├─ AI 成本台账（逐个成功回答保存 usage/价格状态）
       └─ 回答交给 services.ai_visibility_service；persist=true 时另存分析历史

历史摘要：GET /api/geo/visibility/runs
完整回放：GET /api/geo/visibility/runs/{run_id}
```

除认证状态、登录、首次设置、退出和健康检查外，Flask API 都受管理员会话保护。API 调试客户端需要先取得有效的 `geo_operator_session` Cookie。

## 3. 分析接口

### 请求

`POST /api/geo/visibility/analyze`

请求头：

```http
Content-Type: application/json
```

请求体示例只使用合成品牌和合成回答：

```json
{
  "project_id": 1,
  "prompt": "适合中小团队的自动化平台有哪些？",
  "responses": [
    {
      "text": "1. 示例竞品适合复杂流程。\n2. 合成云适合快速部署，参见 https://docs.synthetic.example/guide?utm_source=assistant",
      "provider": "manual-import",
      "model": "synthetic-model"
    },
    {
      "text": "合成云操作简单，但需要根据团队规模评估价格。",
      "provider": "manual-import",
      "model": "synthetic-model"
    }
  ],
  "aliases": ["Synthetic Cloud"],
  "competitors": ["示例竞品"],
  "persist": true
}
```

字段契约：

| 字段 | 必需 | 约束与语义 |
| --- | --- | --- |
| `project_id` | 是 | 已存在的正整数项目 ID；品牌主名、产品和官网从数据库读取 |
| `prompt` | 是 | 非空字符串，后端最大 4,000 字符；当前桌面 UI 限制为 2–500 字符 |
| `responses` | 是 | 1–20 项；每项可以是字符串，或只含 `text`、`provider`、`model` 的对象 |
| `responses[].text` | 是 | 非空，单条最大 50,000 字符，全部回答总计最大 250,000 字符 |
| `responses[].provider` | 否 | 最大 120 字符的溯源标签，不触发调用 |
| `responses[].model` | 否 | 最大 120 字符的溯源标签，不触发调用 |
| `aliases` | 否 | 最多 100 个品牌别名，每项最大 160 字符；产品名始终自动加入别名 |
| `competitors` | 否 | 省略时使用项目竞品；显式 `[]` 表示本批不比较竞品，最多 50 项 |
| `persist` | 否 | 布尔值，默认 `false`；为 `true` 时保存输入原文和完整结果 |

竞品项可以是名称字符串，也可以是 `{ "name", "aliases", "domains" }` 对象；桌面 UI 当前提交名称字符串。对象形式中每个竞品最多 100 个别名、20 个域名。

整个 HTTP 请求不能超过 2 MiB。输入中的 NUL、无效类型、未知回答对象字段和越界数量会被拒绝。

### 响应

成功状态为 HTTP 200，统一外层结构为：

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "run_id": 12,
    "created_at": "2026-09-09T01:23:45Z",
    "prompt": "适合中小团队的自动化平台有哪些？",
    "project": {
      "id": 1,
      "name": "合成云",
      "product": "合成套件",
      "website": "https://synthetic.example"
    },
    "summary": {
      "sample_count": 2,
      "mentioned_count": 1,
      "mention_rate": 0.5,
      "share_of_voice": 1.0,
      "average_rank": 2.0,
      "citation_count": 1,
      "citation_rate": 0.5,
      "owned_citation_count": 1,
      "owned_citation_rate": 0.5,
      "sentiment_score": 0.0,
      "confidence": {
        "level": "LOW",
        "score": 0.4405052780614552,
        "interval_95": null
      }
    },
    "entities": [],
    "samples": [],
    "citations": [],
    "recommendation_details": [],
    "recommendations": [],
    "warnings": [],
    "methodology": {
      "engine": "deterministic-local-geo-engine-port",
      "external_model_used": false,
      "sample_semantics": "MEAN_WITH_DISPERSION",
      "metric_comparability": "This ad-hoc Python port is not directly comparable with provider grounding runs, failed executions, or historical TypeScript-engine series."
    },
    "algorithm_versions": {
      "integration": "ai-visibility-python-port-1.1.0",
      "mention": "mention-python-port-1.1.0",
      "rank": "rank-python-port-1.1.0",
      "citation": "citation-inline-url-python-port-1.1.0",
      "citation_extraction": "explicit-response-url-1.0.0",
      "public_suffix_rules": "public-suffix-curated-python-1.1.0",
      "source_classifier": "source-classifier-python-1.1.0",
      "sentiment": "sentiment-python-port-1.1.0",
      "metrics": "metrics-ad-hoc-python-1.1.0",
      "recommendations": "recommendations-ad-hoc-python-1.1.0"
    },
    "source_trace": {}
  }
}
```

这是合成输入的关键字段结构示例，不是一次真实模型调用记录；为控制篇幅，证据数组在示例中留空。`run_id` 和 `created_at` 只在 `persist=true` 时出现；真实响应的数组包含逐实体、逐样本、引用和建议证据。

比例字段在后端使用 `0..1`；桌面前端再显示为百分比。推荐位次只有在回答中识别到结构化推荐序列时才有值。少于 5 个样本时，95% 区间为 `null`。

### 受控真实采样接口

`POST /api/geo/visibility/sample` 只由 Windows EXE 中的操作员显式触发。请求必须包含 `confirm_external_call=true`、稳定的 `X-Request-ID`、`Idempotency-Key` 和 1–5 的 `sample_count`。EXE 会在调用前展示 prompt、服务地址、精确模型 ID、调用次数以及不发送的数据清单。

- 每次 Chat Completions 请求只有一个 `user` 消息，其 `content` 为同一个 `prompt` 原文；模型标识、该 AI 服务自身的鉴权头、temperature 和 max tokens 只是传输元数据。
- 项目名称、产品、介绍、官网、历史文章、别名、竞品、媒体账号、Cookie、媒体凭据和发布任务都不发送给模型，只在回答返回后参与本机分析。
- 调用按序执行，最多 5 次，不并发。任意一次失败、限流、超时或协议异常都会停止后续样本；已完成部分返回 HTTP 207，首个样本失败返回 `need_action`。
- 请求级 `ai_config` 缺一项即在联网前拒绝；完全省略时优先使用当前 Windows 用户以 DPAPI 保护的统一 AI 配置，再回退完整的 `AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL`。所有来源都不完整时不会调用 provider，状态接口不会返回 API Key。
- 幂等记录在第一次外部调用前写入 SQLite。同键同参数只回放已保存结果；同键不同参数返回 409；进程中断、读取超时或结果不明确时标记需要人工处理，绝不自动重试或静默改用新键。
- 接口没有传入网页搜索工具、检索上下文或引用材料，因此回答的时效性、事实与 URL 可达性均未被本接口核验。
- 当前没有定时、后台无人值守或分布式采样入口。不同 provider 只能由操作员逐次选择配置并分别确认，不能并发发起。

完整请求、响应、数据披露、费用凭据和失败语义见 [当前配置模型真实采样契约](./visibility-live-sampling.md)。

## 4. 历史接口

### 摘要列表

`GET /api/geo/visibility/runs?project_id=1&limit=20`

- `project_id` 必须存在。
- `limit` 默认为 20，范围为 1–100；桌面页面当前读取最近 10 条。
- 按 `created_at DESC, id DESC` 返回。
- 列表只返回 `id`、`project_id`、`prompt`、`summary`、`warnings_count`、`algorithm_version` 和 `created_at`，不返回回答原文。

### 完整回放

`GET /api/geo/visibility/runs/{run_id}`

返回：

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": 12,
    "project_id": 1,
    "prompt": "适合中小团队的自动化平台有哪些？",
    "input_samples": [],
    "coverage_items": {
      "aliases": [],
      "competitors": []
    },
    "result": {},
    "algorithm_version": "ai-visibility-python-port-1.1.0",
    "created_at": "2026-09-09T01:23:45Z"
  }
}
```

`input_samples` 在真实记录中包含完整回答文本；详情接口不能当作轻量列表使用。项目删除时，其 `ai_visibility_runs` 通过 SQLite 外键级联删除。

## 5. 状态码

| HTTP | 场景 |
| --- | --- |
| 200 | 分析、列表、详情、完整采样或同参数幂等回放成功 |
| 207 | 受控采样已取得部分回答，后续样本因错误停止 |
| 400 | JSON、字段类型、范围、长度、`limit` 或算法输入不合法 |
| 401 | 缺少有效管理员会话 |
| 404 | 项目或历史记录不存在 |
| 409 | 采样幂等键/请求 ID 冲突，或此前执行结果仍不明确 |
| 413 | 分析请求超过 2 MiB，或采样请求超过 128 KiB |
| 429 | 本机或 provider 对采样限流；不会自动重试 |
| 502 | provider 协议/响应错误，或取得回答后的本机收口异常 |
| 503 | 缺少完整模型配置、provider 不可连接，或本机采样状态无法可靠保存 |
| 504 | provider 读取超时、远端结果不明确；不会自动重试 |
| 500 | 已保存的本地历史无法按严格 JSON/时间契约读取 |

错误仍使用 `{ "code", "msg", "data" }` 外层结构。调用方应同时判断 HTTP 状态和 `code`，不要只解析文案。

## 6. 算法语义

### 实体提及

匹配优先覆盖品牌主名、别名和域名，再进行有计算上限的模糊匹配。输出包含 UTF-16 位置、原文片段、匹配类型、置信度、歧义和 `needs_review`。短词、多个实体争用同一模糊表述等场景不会被当作无条件高置信命中。

### 推荐位次与 SOV

位次只从能够验证为连续推荐列表的编号或项目符号中提取。SOV 使用：

```text
position_score(rank) = 1 / log2(rank + 1)
```

未排位但有效的品牌提及使用权重 1。当前单次接口对 prompt、provider 和样本使用统一权重 1，不代表跨 provider 的统计总体。

### 引用

只从回答原文提取显式 `http://`、`https://` 和 `www.` URL。规范化会删除 fragment、默认端口和 `utm_*` 等追踪参数，并保留有语义的查询参数。来源分类包括 `OWNED`、`COMPETITOR_OWNED`、`PUBLISHER`、`COMMUNITY`、`REVIEW`、`SOCIAL`、`GOVERNMENT_ACADEMIC` 和 `UNKNOWN`。

引擎不发起 HTTP 请求，不验证 URL 可达性，也不从“据某报告”这类无链接文本推断引用。

### 置信度

品牌置信分综合样本充足度、提及分布稳定度和解析置信度：

```text
sqrt(sample_count / 5)（最大为 1）
  × (0.35 × provider_success_rate
     + 0.30 × stability
     + 0.35 × parser_confidence)
```

当前导入接口只接受非空成功回答，因此可观察总体中的 `provider_success_rate` 固定为 1；它没有采集失败样本，不能被解读为真实 provider 成功率。至少 5 条样本才计算区间；等级阈值和稳定性字段应连同样本数一起展示。

指标算法标识为 `metrics-ad-hoc-python-1.1.0`，特意与上游及 provider grounding 指标分开。除非以后建立同一采样总体、失败语义、权重和版本迁移校准，否则不要把两类分数做同比、环比或趋势线。

### 建议

建议至少需要 2 条样本。规则会根据品牌缺席、竞品覆盖、自有/第三方引用差距等事实生成，并带 `rule_id`、规则版本、样本 ID、优先分和抑制键。它们是确定性规则输出，不是模型自由生成文本。

## 7. 版本与证据溯源

当前集成版本：

| 部件 | 版本 |
| --- | --- |
| Python 集成 | `ai-visibility-python-port-1.1.0` |
| 提及 | `mention-python-port-1.1.0` |
| 位次 | `rank-python-port-1.1.0` |
| 正文 URL 引用 | `citation-inline-url-python-port-1.1.0` |
| 显式 URL 提取 | `explicit-response-url-1.0.0` |
| 公共后缀规则 | `public-suffix-curated-python-1.1.0` |
| 来源分类 | `source-classifier-python-1.1.0` |
| 情感 | `sentiment-python-port-1.1.0` |
| 临时指标 | `metrics-ad-hoc-python-1.1.0` |
| 临时建议 | `recommendations-ad-hoc-python-1.1.0` |

每次分析结果的 `source_trace` 固定记录：

- 交接包：`GEO-AI-Visibility-SaaS-v3.0-dev-handoff-20260908-181903.zip`
- 交接包 SHA-256：`2ff20604dad3b68807c47d0f3aef3d59ce613bf927dcde0b90ad80bc4b96e96c`
- 清单 SHA-256：`e0c5bdc1ced83f82ce2ecf6c9f12f646885fc283b6d11e40472492ce3a0db239`
- 上游包：`packages/geo-engine`，版本 `0.1.0`
- `mention.ts`、`rank.ts`、`citation.ts`、`metrics.ts`、`perception.ts`、`recommendations.ts` 的独立 SHA-256
- 本机实现模块和 Python 集成版本

上游哈希证明参考源码快照；运行结果由 Python 移植版本标识。两者不能混写成“直接运行了上游 TypeScript 包”。

独立审计还确认交接 ZIP 未包含 `LICENSE` 或 `NOTICE`。这不自动证明代码不可内部评估，但也不能推定已经取得外部分发、转授权或商业再分发权。任何对外 EXE、客户交付物或源代码分发在包含该移植实现前，都必须取得同事/权利人的书面内部授权，或补齐可核验的许可与权利声明；当前这是外部分发阻塞项。

## 8. 数据持久化

`persist=true` 时使用 `BEGIN IMMEDIATE` 在同一 SQLite 事务内检查项目、写入记录并回读；任何异常都会回滚。JSON 使用严格序列化并拒绝 NaN、集合等非 JSON 数据，时间使用 UTC ISO-8601 `...Z` 格式。

本机表保存：

- `project_id`
- `prompt`
- `input_samples_json`（包含回答原文及 provider/model 标签）
- `coverage_items_json`（别名和竞品覆盖）
- `result_json`（完整分析结果与证据）
- `algorithm_version`
- `created_at`

这些字段未做应用内静态加密。完整回答可能含敏感信息，导入前应脱敏，并按本机数据管理要求备份和传输。

## 9. 回滚与开关

需要区分三个独立边界：

1. AI 可见度分析与受控采样：手工分析始终保持本机运行；真实采样只在操作员提供完整当前配置、核对外发内容并显式确认后按请求执行，没有后台开关、定时任务或分布式 worker。出现回归时停止使用采样入口并回退到已验收旧 EXE；`ai_visibility_runs` 和 `ai_visibility_sample_requests` 可以保留，旧版本不会主动重发。当前仍没有名为 `AI_VISIBILITY_ENABLED` 的运行时开关。
2. AI 内容生成：由 `CONTENT_ENGINE` 和 `CONTENT_ENGINE_FALLBACK` 选择 `qwen` 或未来的 `colleague` 适配器。这些变量不控制 AI 可见度。外部同事服务未验收时保持 `CONTENT_ENGINE=qwen`，并避免配置同名重复 fallback。
3. 真实发布：`safe` 桌面 profile 强制 `ALLOW_REAL_PUBLISHING=false`，调度器固定 `allow_real=False`；`authorized_real` profile 只有在 Google Chrome、Playwright 和 Patchright 的成品离线探针全部通过后，才在当前进程开放受控发布任务。Bilibili 视频兼容链路另需 EXE 外部固定版本运行时通过文件大小、SHA-256 与离线版本探针；桌面版不下载也不打包该运行时。旧版直接发布入口保持关闭。这些是发布安全开关，不改变可见度算法。

若未来增加定时、无人值守或分布式回答采集，应另建默认关闭、可审计的独立采集开关；当前代码中尚无这类能力和开关，不应在桌面交付文档中伪造名称或状态。

## 10. 定时/分布式 provider 待接项

当前操作员受控的 OpenAI-compatible 顺序采样已经实现。把它扩展为定时、无人值守、分布式采样，或接入同事的远程内容生成服务前，仍需要一次性收齐并验收：

- provider 地址、认证方式、Token 归属和密钥轮换方式。
- 固定模型 ID、版本或快照，及其许可和可商用边界。
- 请求/响应 JSON Schema、错误码、限流、超时、最大响应和幂等语义。
- `/health/live`、`/health/ready` 或等价健康契约。
- 问题、品牌、竞品、历史和客户数据中哪些字段允许发送。
- 日志脱敏、审计记录、保留期限、删除机制和地域要求。
- provider 失败也要形成样本状态，不能只把成功回答交给统计层后宣称成功率为 100%。
- 使用纯合成项目完成新 provider 的受控验收；验收说明必须在请求前列出将发送的字段，禁止发送现有客户或项目数据。
- 交接 ZIP 缺少 `LICENSE`/`NOTICE`；对外分发前取得书面内部授权或补齐权利声明。

推荐保持“采集器”和“确定性分析器”分离：采集器只产生 `{ text, provider, model, status, timing }` 等标准化样本，分析器继续无网络运行。这样 provider 变更不会改变算法证据语义，也便于关闭采集、保留本机手工导入作为回滚路径。

## 11. 回归命令

后端：

```powershell
uv run python -m unittest `
  tests.test_ai_visibility_service `
  tests.test_ai_visibility_run_service `
  tests.test_ai_visibility_api `
  tests.test_ai_visibility_sampling_service `
  tests.test_ai_visibility_sampling_database `
  tests.test_ai_visibility_sampling_api -v
```

前端：

```powershell
Set-Location sau_frontend
node --test tests/visibilityResult.test.js tests/visibilitySampling.test.js
npm run build
```

测试不得调用真实 provider 或执行真实发布。真实采样验收只能在 EXE 中由操作员先核对将外发的精确 prompt，再使用隔离凭据和合成测试项目显式确认；没有凭据时保持未执行状态。
