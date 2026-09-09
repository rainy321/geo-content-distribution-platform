# AI 可见度集成说明

## 1. 能力边界

当前 AI 可见度是“回答导入 + 本机确定性分析”，不是“调用模型并测量模型”。调用方必须先取得回答，再把回答和已有品牌项目 ID 传入接口。

已实现：

- 品牌主名、产品别名、自定义别名、竞品和域名实体构建。
- 精确、别名、域名和受限模糊匹配，以及可回看证据片段。
- 编号/项目符号推荐列表的位次解析。
- 显式 URL 规范化、去除追踪参数、注册域判断和来源分类。
- 品牌/竞品提及率、推荐位次加权 SOV、引用率、情感与置信度。
- 基于样本证据的规则建议。
- 可选本机 SQLite 历史、摘要列表和完整回放。
- 上游交接包、源文件哈希和本机集成版本溯源。

未实现：

- 自动登录千问、ChatGPT 或其他 provider。
- 向任何外部模型发送问题或项目资料。
- provider 失败率、跨 provider 权重、跨时间趋势和定时采样。
- 联网验证 URL、抓取页面内容、跟随重定向或判断页面真实性。
- 多用户租户、云端同步和远程数据仓库。

`responses[].provider` 和 `responses[].model` 仅作为来源标签原样保存和回显，不参与网络连接，也不证明该回答确实来自所标模型。

当前实现是经过独立标识的 Python 移植与本机临时统计口径。其结果不能与 provider grounding 运行、包含失败执行的总体，或历史 TypeScript 引擎序列直接拼接比较。

不要混淆三条接口链路：本页的 `/api/geo/visibility/*` 是已实现的本机回答分析；内容创作适配层向同事服务约定的 `POST /api/v1/generate` 是另一条出站生成链路；未来用于自动取得千问等回答的 provider 采集契约目前尚未定义和接入。

## 2. 组件关系

```text
AI 可见度页面
  └─ POST /api/geo/visibility/analyze
       ├─ 读取本机 projects 品牌快照
       ├─ services.ai_visibility_service（纯分析，无网络/数据库依赖）
       └─ persist=true 时：services.ai_visibility_run_service
            └─ SQLite ai_visibility_runs

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
| 200 | 分析、列表或详情成功 |
| 400 | JSON、字段类型、范围、长度、`limit` 或算法输入不合法 |
| 401 | 缺少有效管理员会话 |
| 404 | 项目或历史记录不存在 |
| 413 | 分析请求超过 2 MiB |
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

1. AI 可见度本地分析：当前没有外部 provider，也没有 `AI_VISIBILITY_ENABLED` 运行时开关。出现回归时，先停止使用入口并回退到已验收旧 EXE；附加的 `ai_visibility_runs` 表可以保留，不会被旧版本主动执行。
2. AI 内容生成：由 `CONTENT_ENGINE` 和 `CONTENT_ENGINE_FALLBACK` 选择 `qwen` 或未来的 `colleague` 适配器。这些变量不控制 AI 可见度。外部同事服务未验收时保持 `CONTENT_ENGINE=qwen`，并避免配置同名重复 fallback。
3. 真实发布：桌面宿主强制 `ALLOW_REAL_PUBLISHING=false`、`ENABLE_BILIBILI_RUNTIME=false`，调度器固定 `allow_real=False`。这些是发布安全开关，也不改变可见度算法。

未来接入自动回答采集时，应先增加一个默认关闭、可审计的独立采集开关；当前代码中尚无这一开关，不应在部署文档中伪造名称或状态。

## 10. 外部 provider 待接项

在自动化采集上线前，需要一次性收齐并验收：

- provider 地址、认证方式、Token 归属和密钥轮换方式。
- 固定模型 ID、版本或快照，及其许可和可商用边界。
- 请求/响应 JSON Schema、错误码、限流、超时、最大响应和幂等语义。
- `/health/live`、`/health/ready` 或等价健康契约。
- 问题、品牌、竞品、历史和客户数据中哪些字段允许发送。
- 日志脱敏、审计记录、保留期限、删除机制和地域要求。
- provider 失败也要形成样本状态，不能只把成功回答交给统计层后宣称成功率为 100%。
- 使用纯合成项目完成受控验收；验收说明必须在请求前列出将发送的字段，禁止发送现有客户或项目数据。
- 交接 ZIP 缺少 `LICENSE`/`NOTICE`；对外分发前取得书面内部授权或补齐权利声明。

推荐保持“采集器”和“确定性分析器”分离：采集器只产生 `{ text, provider, model, status, timing }` 等标准化样本，分析器继续无网络运行。这样 provider 变更不会改变算法证据语义，也便于关闭采集、保留本机手工导入作为回滚路径。

## 11. 回归命令

后端：

```powershell
uv run python -m unittest `
  tests.test_ai_visibility_service `
  tests.test_ai_visibility_run_service `
  tests.test_ai_visibility_api -v
```

前端：

```powershell
Set-Location sau_frontend
node --test tests/visibilityResult.test.js
npm run build
```

测试不得调用真实 provider 或执行真实发布。外部 provider 联调应使用另行审批的合成数据和隔离凭据。
