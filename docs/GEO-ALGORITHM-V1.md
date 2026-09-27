# GEO 诊断与优化算法 v1

> 状态：产品/算法设计稿，不代表平台官方承诺。外部规则与价格快照日期：2026-09-08。

## 1. 产品边界与核心决策

GEO 不应在“品牌优化”和“问题优化”之间二选一，而应采用两层模型：

- **品牌是长期诊断、知识资产、效果归因和客户合同的边界。**
- **问题簇是实验、内容生产、预算消耗和月度交付的最小单元。**
- 文章只是一次干预，平台只是分发通道，AI 引擎是观测环境。

因此推荐产品表达为：

`品牌项目 -> 用户意图/问题簇 -> 可验证主张与证据 -> 内容资产 -> 平台发布 -> 抓取/检索/引用观测`

不建议销售“一个字面问题”。一个真实意图会被 AI 做 query fan-out，也会被用户用多种问法表达。建议把商业 SKU 定义成“一个核心问题簇”：1 个标准问题、8～20 个同意图变体、若干目标 AI 平台、固定的重复测量次数和一个月优化周期。

## 2. 当前系统审计结论

当前系统已经是“品牌项目 + 单题文章”的雏形，但现有 GEO 分数本质上是内容格式检查器：品牌词出现、关键词覆盖、篇幅、标题、小标题、FAQ、数字、链接、总结和关键词密度。它没有测量：

- 问题与品牌真实能力是否匹配；
- 每个品牌主张是否有证据支持；
- 与竞品相比是否具有可辩护的差异；
- 页面是否可抓取、被索引、被目标 AI 检索或引用；
- AI 回答中的提及率、引用率、位置、准确率和跨次稳定性。

现有分数应改名为 `ContentReadinessScore`，不能直接向客户展示为“GEO 效果分”。

另有三个直接工程风险：

1. AI 服务丢弃供应商返回的 `usage`，目前无法按客户、项目、文章和步骤核算真实 Token/成本。
2. 发布调度一次可提升 100 个任务，但缺少“账号 × 平台”的滚动额度、冷却时间、互斥锁和熔断器。
3. 发布成功只证明平台接受了内容，不等于公网可见、被搜索索引或被 AI 引用。

## 3. 最小数据模型

### 3.1 品牌事实与证据

- `brand_entity`：法定名、常用名、产品、行业、地区、官网、统一实体标识。
- `brand_claim`：受众、问题、能力、机制、结果、适用边界、风险等级、有效期。
- `claim_evidence`：来源 URL/文件、证据原文、独立性、权威性、发布日期、核验状态。
- `competitor_entity` / `competitor_claim`：使用相同结构保存竞品，而不是只保存竞品名称。

每条主张必须能回溯到证据；高风险行业主张没有证据时不得进入生成提示词。

### 3.2 问题与观测

- `question_cluster`：标准问题、意图、漏斗阶段、目标受众、商业价值、合规级别。
- `question_variant`：自然改写、后续追问、比较问、风险问、地域问；保存向量和意图标签用于去重。
- `ai_observation`：AI 产品、模型、是否联网、地区、日期、会话条件、问题版本、完整响应快照。
- `answer_mention` / `answer_citation`：品牌提及、竞品提及、顺序、引用 URL、事实准确性。

### 3.3 内容、发布与成本

- `content_asset` / `content_claim`：一篇内容使用了哪些品牌主张、证据和问题簇。
- `publication`：平台、账号、版本哈希、任务状态、公开 URL、声明类型、审核结果。
- `crawl_check`：HTTP、公网匿名访问、robots/noindex、平台内检索、Web 索引、AI 检索阶段。
- `account_policy` / `account_usage`：软上限、滚动计数、最小间隔、账号阶段、风险状态。
- `model_usage`：provider、model、step、input/cache/output/reasoning/image tokens、search calls、费用和币种。
- `diagnosis_snapshot`：每次诊断的原始指标、分数、样本量、置信区间和算法版本。

## 4. 评分体系：准备度和真实结果必须分开

### 4.1 文章—品牌关联度 `ABAS`（0～100）

先把正文切成可引用段落，抽取原子主张，并与品牌知识库做语义匹配和证据蕴含判断。

```text
ABAS = 25% IntentCapabilityFit
     + 20% VerifiedFactCoverage
     + 20% ClaimEvidenceSupport
     + 15% BrandAttribution
     + 10% DefensibleDifferentiation
     + 10% Naturalness
     - Penalties
```

- `IntentCapabilityFit`：用户问题与品牌已验证能力的匹配，不是关键词重合。
- `VerifiedFactCoverage`：文章覆盖的关键品牌事实权重 / 该问题所需事实权重。
- `ClaimEvidenceSupport`：由有效证据支持的主张权重 / 全部主张权重。
- `BrandAttribution`：品牌与相关能力、机制或证据是否出现在同一语义段落。
- `DefensibleDifferentiation`：相对竞品的独有、可验证差异，不奖励空泛“领先”。
- `Naturalness`：品牌出现是否必要、密度是否合理、是否真正回答用户问题。

硬闸门优先于总分：虚构事实、无证据医疗/金融结论、侵权、违法广告用语或主体信息矛盾时直接阻断。普通扣分建议包括：每个无证据关键主张 -20（总计最多 -60）、近重复内容 -20、标题正文不一致 -15、过度营销 -15。输出必须包含命中的 `claim_id`、证据 URL 和正文证据区间，避免只有一个不可解释的分数。

关联度不是越高越好：行业科普建议 30～55，解决方案/实施指南 50～70，选型对比 60～80，品牌/产品/案例 75～95。所有文章都追求 90 分，会把内容推成软广告。

### 4.2 品牌准备度 `BRS`（0～100）

```text
BRS = 15% EntityConsistency
    + 20% DemandCoverage
    + 20% EvidenceStrength
    + 15% Differentiation
    + 20% SourceReadiness
    + 10% ComplianceReadiness
```

这是可控的“准备度”，不等于 AI 已经看见或推荐品牌。

### 4.3 问题机会分 `QOS`（0～100）

```text
QOS = 100
    × Demand^0.20
    × BusinessValue^0.25
    × VisibilityGap^0.20
    × CompetitiveOpportunity^0.15
    × EvidenceReadiness^0.10
    × SourceReachability^0.10
    × ComplianceMultiplier
```

各输入先归一化到 0～1。使用加权几何平均，是为了让需求、品牌适配、缺口或可执行性中任何一个接近 0 时都无法被其他高分掩盖。`CompetitiveOpportunity` 要区分两类：竞品已强、品牌缺席的追赶缺口；以及大家都弱、但本品牌有独有证据的市场空白。后者通常更值得优先投入。

### 4.4 AI 可见度 `AVI`（结果指标）

对每个“问题簇 × AI 产品”使用多问法、多日期、独立会话重复观测：

```text
AVI = 35% BrandMentionRate
    + 25% CitationRate
    + 15% Prominence
    + 15% EntityAccuracy
    + 10% SentimentSafety
```

- `BrandMentionRate = 正确提及品牌的运行数 / 总运行数`
- `CitationRate = 引用客户目标资产的运行数 / 总运行数`
- `Prominence`：按首选/出现顺序计算位置分，而不是只看有没有名字。
- `EntityAccuracy = 正确品牌事实权重 / AI 回答全部品牌事实权重`
- 另报 `RecommendationShare`、`Coverage`、`SourceDiversity`、`CrawlLatency` 和跨次稳定性。

不提供引用功能的 AI 产品，应将 `CitationRate` 标为 N/A 并对其余权重重新归一化，不能填 0；品牌完全未被提及时，事实准确率也应标 N/A，综合指标使用 `FactDelivery = MentionRate × FactAccuracy`，防止“什么都没说所以全说对了”。

所有比例同时展示样本量和 95% Wilson 置信区间。探索阶段每个平台每题至少 3～5 次；决策级诊断建议 30 个问题 × 3 次（n=90，最坏情形误差约 ±10.3 个百分点），成熟版可做 100 × 5（n=500，约 ±4.4 个百分点）。API 结果不能冒充消费端 App 结果；必须记录具体产品、模型、联网状态、地区和日期。

### 4.5 最终诊断不是一个魔法总分

客户报告应并列显示：

1. `BRS`：品牌资产是否准备好；
2. `QOS`：哪些问题簇值得投；
3. `ABAS`：每篇文章是否真的属于这个品牌；
4. `AVI`：AI 端是否产生了真实变化；
5. `Delivery Funnel`：卡在发布、公开、索引、检索、引用还是转化。

这样能防止“把品牌词多写几次就涨分”的自我欺骗。

## 5. 诊断输出与优化方向

每个品牌先建立统一的竞争主张结构：

`受众 -> 问题 -> 能力 -> 机制 -> 证据 -> 结果 -> 适用边界`

诊断识别六类缺口：

- **需求缺口**：高价值问题没有任何品牌内容回答。
- **证据缺口**：品牌在宣传，但没有可引用的一手数据、案例或公开证据。
- **差异化缺口**：文案与竞品高度同质，没有可辩护机制。
- **实体一致性缺口**：官网和各平台的品牌名、产品、主体、地区、数字相互冲突。
- **分发/索引缺口**：有内容但不公开、不可索引或只有封闭平台页面。
- **AI 答案缺口**：页面可检索，但 AI 不提及、引用错误或被竞品压制。

每条建议都应输出：问题簇、缺口类型、证据、竞品基线、预期指标、建议内容、建议平台、成本、风险和 7/14/30 日复测计划。

## 6. 平台额度与发布安全策略

以下是产品初始保护阈值，不是平台官方最大能力。计数采用滚动 24 小时窗口。

| 平台 | 当前系统内容形态 | 官方公开硬上限 | 新号 -> 稳定号内控上限 | 最小间隔 |
|---|---|---:|---:|---:|
| 知乎 | 文章 | 未公开统一值 | 1 -> 2/日 | 6h |
| 今日头条 | 文章 | 全体裁合计 50/日 | 2 -> 5/日 | 3h |
| 百家号 | 图文文章 | 未公开统一值/动态权益 | 2 -> 3/日 | 4h |
| 搜狐号 | 文章 | 个人、企业/机构 5/日 | 2 -> 3/日 | 4h |
| 小红书 | 图文笔记 | 未公开统一值 | 1 -> 2/日 | 6h |
| 抖音 | 图文笔记 | 未公开统一值 | 1 -> 2/日 | 6h |
| 快手 | 图文笔记 | 未公开统一值 | 1 -> 2/日 | 6h |
| Bilibili | 视频，非文章 | 文章不适用 | 1 视频/日 | 12h |
| 视频号 | 视频，非文章 | 文章不适用 | 1 视频/日 | 12h |
| TikTok | 视频，非文章 | Direct Post API 动态，通常约 15/24h | 1 -> 2/日 | 6h |

稳定号建议定义：连续 14 日、至少 10 次公开成功、审核通过率不低于 95%、没有处罚或风控提示。真实放行额度：

```text
effective_cap = min(
  platform_ui_remaining_if_known,
  official_hard_cap_if_known,
  product_soft_cap,
  customer_plan_cap,
  rolling_risk_cap
)
```

创建任务和执行前各校验一次；同一 `(platform, account_id)` 只允许一个发布任务执行。验证码、实名、风险提示、频繁提示、权限异常或结果不明时立即把额度设为 0 并转人工，禁止盲目重试、换号、换 IP、伪造设备指纹或绕过验证码。

抖音、小红书、Bilibili 当前自动化通道有较高条款风险；TikTok 只有按官方 API 的审核、预览、编辑、隐私选择和用户确认流程重构后才适合规模化。缺少官方发布 API 或书面授权的平台，产品应定位为“生成草稿 + 用户预览确认 + 辅助发布”。

## 7. 可抓取与 AI 平台适配

不能保证“AI 一定抓到”。交付漏斗应分别验收：

`publish_success -> public_verified -> crawl_eligible -> web_indexed -> ai_retrievable -> cited -> correct_brand_mention -> conversion`

系统可以承诺的是“公开发布、技术上可抓取并持续验证”，不能承诺固定抓取时限、进入训练集、固定引用或排名。

自有品牌证据站应作为稳定信源中枢：HTTP 200、无需登录、服务端正文、稳定 URL、canonical、sitemap、清晰作者/时间、原始数据和证据、结构化数据与页面一致、机器人日志可观测。第三方平台做适配后的衍生内容，不应原文复制铺量。

AI 平台 v1 规则：

- ChatGPT：放行 `OAI-SearchBot`；`GPTBot` 是否用于训练可独立控制。
- Perplexity：放行 `PerplexityBot`，WAF 按官方 IP 列表更新。
- Google AI Overviews/AI Mode：核心仍是 Google 索引、排名、RAG 和 query fan-out；需要 Googlebot、可索引和 snippet 资格，不需要 `llms.txt` 或所谓 AI 专用 schema。
- Gemini Apps：`Google-Extended` 与 Google Search 收录/排名不是同一个控制面。
- Claude：区分 `Claude-SearchBot`、用户触发的 `Claude-User` 与训练用 `ClaudeBot`。
- DeepSeek、豆包：尚无可确认的公开专用搜索爬虫/站长提交规则，不能编造平台权重，只能保证公网可访问并做重复实测。
- 腾讯元宝：官方说明联网搜索以搜狗和公开 Web 为基础，并叠加腾讯内容生态；优先保证搜狗可发现性和跨来源实体一致。

## 8. 单篇 Token 与直接 API 成本

“成品字数”不是总 Token。建议按流水线预算：

| 档位 | 场景 | 输入/输出 Token | 总文本 Token | OpenAI 组合成本* | 国产混搭成本* |
|---|---|---:|---:|---:|---:|
| 低 | 1200～1800 字、3 次检索、1 图、1 版 | 14.7k / 4.2k | 18.9k | 约 ¥0.32 | 约 ¥0.38 |
| 中 | 2000～3000 字、6 次检索、2 图、3 平台版 | 37k / 8.5k | 45.5k | 约 ¥2.46 | 约 ¥0.95 |
| 高 | 3500～5000 字、12 次检索、3 图、5 平台版 | 89k / 16.5k | 105.5k | 约 ¥10.36 | 约 ¥3.37 |

\* OpenAI 组合分别按 Luna/低质量图、Terra/中质量图、Sol/高质量图；汇率预算按 1 美元 = 7.2 元。国产为文本、搜索和图片的组合估算。价格会变化，正式计费必须以供应商 `usage` 和账单为准；建议另加 10%～20% 失败重试预算。

当前仓库实际流程更轻：单次文章请求最大输出 4096 Token；本地 PIL 封面不调用图片模型；确定性浏览器上传不消耗 LLM Token。若用当前 Qwen3.8 Max（北京价格 ¥12/M 输入、¥36/M 输出）粗估一次生成 1～2k 输入、2～4k 输出，约 ¥0.08～0.17；再做一次优化大约共 ¥0.20～0.40。但它没有完整检索、逐主张证据核验、多平台改写和结果测量，不能与完整 GEO 交付等价。

成本公式：

```text
C_text = sum((input × P_in + cache × P_cache + output/reasoning × P_out) / 1e6)
C_total = C_text + search_calls + images + infrastructure + human_review + retries
```

上传本身是 0 LLM Token。商业成本中通常更重要的是 5～15 分钟人工核验、账号运营、失败处置和持续复测，所以销售定价不应按“写了几千字”解释。

## 9. 合规与风险分层

发布前先做硬闸门，再算风险分：

- `0～20`：低风险，可进入发布队列；
- `21～49`：必须人工复核；
- `50～100`：阻断发布。

硬阻断包括违法内容、虚构关键事实、无资质的医疗/金融/法律结论、严重侵权、AI 标识缺失、隐私泄露及明确的平台禁发类别。中国 AI 生成合成内容标识规则自 2025-09-01 起实施；系统必须保存生成来源和声明状态，并映射各平台的 AI/商业内容声明。

行为风险分至少考虑：账号阶段、滚动 24 小时发布数、最小间隔、近似内容比例、最近审核失败率、验证码/风控、登录环境变化和发布结果不确定性。评分只用于降低风险，不能承诺不封号。

## 10. 实施顺序

### P0：先消除运营事故风险

1. 保存所有模型 `usage`，建立步骤级成本账本。
2. 增加账号 × 平台滚动配额、互斥锁、最小间隔、幂等键和熔断器。
3. 停用前端“每天最多 55 条”作为默认可选项。
4. 增加 AI/商业声明、资质、敏感行业、主张证据和重复度检查。
5. 只有 `public_verified` 才算发布交付成功。

### P1：形成真正的 GEO 诊断闭环

1. 建品牌实体、主张和证据知识库。
2. 建问题簇和变体去重。
3. 实现 `ABAS`、`BRS`、`QOS`，保留解释证据。
4. 上线自有品牌证据站与 crawl/index 检查。
5. 对品牌和竞品做 AI 基线测量，7/14/30 日复测。

### P2：按 AI 产品学习，不手写永久权重

1. 记录每个 AI 产品的来源域偏好、提及率、引用率和稳定性。
2. 用随机对照问题簇、发布前基线和 7/14/30/60 日观测校准权重。
3. 数据足够后再用逻辑回归、梯度提升或分层贝叶斯模型预测“被检索/引用概率”。
4. 保留算法版本和置信区间；平台改变时允许权重衰减和重训。

冷启动权重只是可解释先验，不能用同一套人工分数的涨跌证明它自己有效。真实标签必须来自外部结果观测。

## 11. 商业口径建议

市场公开报价显示，有供应商约 ¥4,980/月包含 6 个核心问题巡检和 6 篇文章，也有供应商按“品牌诊断 + 1～5 个主话题 + 100～800 个高潜问答 + 内容矩阵”定价。这说明“单问题 ¥5,000/月”更像销售包装，不是算法或算力单位。

推荐报价结构：

- 基础费：品牌诊断、知识库、合规和监测；
- 问题簇费：按高价值意图簇、AI 平台数和重复测量量；
- 内容/分发费：按核心资产、平台适配版本和账号风险级别；
- 结果监测费：按 7/14/30 日观测及报告；
- 不以“保证 AI 收录/第一名”作为承诺。

## 12. 主要参考资料

- GEO 基础论文：https://arxiv.org/abs/2311.09735
- 2026 GEO 证据综述：https://arxiv.org/abs/2607.14035
- Google AI 搜索优化指南：https://developers.google.com/search/docs/fundamentals/ai-optimization-guide
- OpenAI 发布者与爬虫说明：https://help.openai.com/en/articles/12627856-publishers-and-developers-faq
- 阿里百炼模型价格：https://help.aliyun.com/zh/model-studio/model-pricing
- OpenAI API 价格：https://developers.openai.com/api/docs/pricing
- 今日头条发文数量规则：https://baike.toutiao.com/detail/211/212/570?enter_from=left_navigation
- 搜狐号发文数量说明：https://www.sohu.com/a/383498167_120635073
- TikTok Content Sharing Guidelines：https://developers.tiktok.com/docs/en/content-sharing-guidelines
- AI 生成合成内容标识规则说明：https://www.miit.gov.cn/xwfb/mtbd/wzbd/art/2025/art_5e46c60f9a7141cdb584eb139f476ce9.html
- 公开市场报价示例：https://www.semkw.com/pricing/ 、https://www.xingguangai.cn/pricing/
