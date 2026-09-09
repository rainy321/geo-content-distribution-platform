# DongClaw GEO 真实诊断与短周期回测协议

## 1. 目的与证据边界

这套流程用于回答三个不同问题，不能混在一起：

1. **软件流程能否跑通**：固定问题采样、回答留证、可见度解析、诊断、内容优化、发布、复测和报告是否能闭环。
2. **模型回答是否发生变化**：相同模型与问题下，品牌提及、推荐位次、引用和竞品声量是否出现观察性差值。
3. **GEO 是否造成变化**：需要更长观察窗、稳定抓取条件、足够重复样本和对照设计；当天象征性测试不能证明因果。

本验收的授权主体固定为：

- 公司：北京同于野科技有限公司
- 产品：DongClaw 小龙虾（别名仅 `DongClaw`、`DongClaw小龙虾`）
- 指定竞品：`RanClaw`
- 模型：Qwen、DeepSeek、豆包
- 输入：本文固定的 7 个问题和上述主体名称

评估器拒绝变更主体、问题矩阵和 provider 名称，也拒绝任何字段名包含 API Key、Token、Cookie、密码、Authorization 或 Secret 的输入。它不读取 GEO 数据库、不联网、不自动登录，也不把回答原文复制到 JSON/Markdown 报告中；报告只保存 SHA-256、指标和外部证据引用。

## 2. 2026-09-10 公开检索基线

| 实体 | 当前可验证状态 | 处理规则 |
| --- | --- | --- |
| 北京同于野科技有限公司 | 检索到[第三方企业信息页](https://www.shuididp.cn/yp-4c2d8440523800360ce008a135fc3b01.html)，可作为主体存在的辅助线索，但不是企业官网 | 不据此推导 DongClaw 产品功能、客户、价格或效果 |
| DongClaw 小龙虾 | 未检索到可独立核验的官方产品页或权威第三方产品介绍 | 基线应记录为“公开产品证据不足”，不能补写卖点 |
| RanClaw | 未检索到能确认用户所指竞品的精确实体 | 搜索到的 [RunClaw](https://runclaw.sh/) 拼写不同，绝不自动合并为 RanClaw |

这意味着真实模型在首轮不认识品牌是合理的诊断结果。若模型输出了具体功能、价格、归属或对比，但没有可核验来源，应标成待核事实，而不是正向曝光。

## 3. 固定问题矩阵

| ID | 地区 | 意图 | 类型 | 问题 |
| --- | --- | --- | --- | --- |
| `cn-discovery` | 中国大陆 | 品类发现 | 非品牌 | 中国中小企业想快速部署可持续运行的 AI 助手，有哪些产品或服务值得考虑？ |
| `beijing-local-data` | 北京 | 本地数据与部署 | 非品牌 | 北京的中小企业需要兼顾本地数据与远程运行时，应该如何选择 AI 助手部署方案？ |
| `cn-low-ops` | 中国大陆 | 低运维部署 | 非品牌 | 没有专职运维团队时，如何快速部署并长期运行一个 AI 助手？ |
| `dongclaw-identity` | 中国大陆 | 品牌实体认知 | 品牌 | DongClaw 小龙虾是什么产品，由哪家公司提供，适合解决什么问题？ |
| `dongclaw-vs-ranclaw` | 中国大陆 | 竞品比较 | 品牌比较 | DongClaw 小龙虾和 RanClaw 有什么区别，分别适合什么团队？ |
| `beijing-chinese-support` | 北京 | 中文与本地服务 | 非品牌 | 如果希望在中国使用、强调中文支持和本地化服务，AI 助手部署产品该怎么选？ |
| `dongclaw-public-evidence` | 北京 | 事实与来源核验 | 品牌 | 北京同于野科技有限公司提供的 DongClaw 小龙虾有哪些可验证的公开信息？ |

非品牌问题衡量“被发现”，品牌问题衡量“实体理解和事实证据”。二者必须分栏，不能用在问题里主动写入 `DongClaw` 的样本抬高自然提及率。

## 4. 采样协议

每个 `provider × 精确模型版本 × surface × 联网开关 × question × phase` 是一个独立单元：

- 基线和复测使用完全相同的问题原文。
- 每单元建议 5 个独立新会话；同一对话里重复提问不算独立样本。
- 必须固定网页/API 界面和联网开关。官方千问文档明确说明，千问官网集成了联网搜索等工具，效果可能与直接 API 不同，因此两种 surface 不能混算。[千问官方说明](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen)
- API 采样需记录精确模型 ID。Qwen 可用 OpenAI 兼容接口，但北京地域的新端点包含 Workspace ID；[官方快速入门](https://help.aliyun.com/en/model-studio/first-api-call-to-qwen)。DeepSeek 官方接口为 `https://api.deepseek.com`，模型别名可能指向更新后的快照，因此同时记录采样日期；[DeepSeek 官方文档](https://api-docs.deepseek.com/zh-cn/)。豆包的火山方舟 API 使用 `https://ark.cn-beijing.volces.com/api/v3` 并要求 API Key；[火山方舟官方示例](https://www.volcengine.com/docs/82379/1795150)。
- 保留超时、限流、验证码、拒答和空回答。失败不能从分母删除；报告同时给出“成功回答分母”和“全部尝试分母”。
- 每条尝试保留截图、录屏、导出 JSON 或请求 ID 的 `source_reference`。评估器不读取该文件，但质量门禁检查引用是否存在。
- 不向模型发送现有客户、其他项目、文章库、媒体账号、Cookie 或发布凭据。

若使用 Web 界面，基线与复测还应固定登录状态、浏览器地区/语言、是否允许个性化、是否清空对话。若无法固定，必须在报告中把它列为混杂因素。

## 5. 指标定义

| 指标 | 分母/公式 | 解释 |
| --- | --- | --- |
| 执行成功率 | 成功回答 / 全部尝试 | 把 provider 故障和验证阻塞保留在总体中 |
| 品牌提及率（成功） | 识别 DongClaw/公司/授权别名的成功回答 / 成功回答 | 内容层面的可见度 |
| 品牌提及率（全部） | 品牌命中 / 全部尝试 | 不因只保留成功样本而虚高 |
| 首位率 / Top 3 率 | 结构化推荐位次为 1 / ≤3 的回答 / 成功回答 | 只认能审计的列表位次 |
| 平均位次 | 所有可识别品牌位次均值 | 越低越好；未排位不强行填零 |
| SOV | `sum(1/log2(rank+1))`，未排位有效提及权重 1 | 品牌与 RanClaw 的位置加权声量 |
| 引用率 | 包含显式 HTTP(S)/www URL 的成功回答 / 成功回答 | 不推断隐藏搜索来源 |
| 自有引用率 | 引用已核验官网域名的成功回答 / 成功回答 | 当前官网未核验，因此不可用 |
| 情感分 | 现有同事算法移植层的证据型情感解析 | 需人工复核，不等于事实正确 |

报告还给出地区/问题/模型矩阵和配对差值。配对键必须同时匹配 provider、精确模型、surface、联网开关、问题与重复编号；模型升级或开关变化时不计算“提升”。

## 6. 运行方式

生成固定输入模板：

```powershell
.\.venv\Scripts\python.exe scripts\run_dongclaw_evaluation.py `
  --write-template artifacts\dongclaw-geo-evaluation\input.json
```

把真实回答逐条填入 `attempts` 后生成机器可读和可展示报告：

```powershell
.\.venv\Scripts\python.exe scripts\run_dongclaw_evaluation.py `
  --input artifacts\dongclaw-geo-evaluation\input.json `
  --json-output artifacts\dongclaw-geo-evaluation\report.json `
  --markdown-output artifacts\dongclaw-geo-evaluation\report.md
```

成功尝试示例：

```json
{
  "id": "qwen-cn-discovery-baseline-r1",
  "phase": "baseline",
  "question_id": "cn-discovery",
  "provider": "qwen",
  "model": "记录界面或 API 返回的精确模型名",
  "surface": "web",
  "web_search_enabled": true,
  "replicate": 1,
  "captured_at": "2026-09-10T09:30:00+08:00",
  "status": "success",
  "response_text": "粘贴完整原始回答",
  "error_code": null,
  "source_reference": "evidence/qwen-cn-discovery-baseline-r1.png"
}
```

失败尝试不允许伪造回答：

```json
{
  "id": "doubao-cn-discovery-baseline-r2",
  "phase": "baseline",
  "question_id": "cn-discovery",
  "provider": "doubao",
  "model": "记录精确模型名",
  "surface": "web",
  "web_search_enabled": true,
  "replicate": 2,
  "captured_at": "2026-09-10T09:35:00+08:00",
  "status": "verification_required",
  "response_text": null,
  "error_code": "HUMAN_VERIFICATION_REQUIRED",
  "source_reference": "evidence/doubao-verification-r2.png"
}
```

## 7. 最低验收门槛

- 当天：允许输出 `NOT_EXECUTED`、`BASELINE_ONLY` 或 `SHORT_HORIZON_EXPLORATORY`，只验流程，不写“GEO 已有效”。
- 三天后：同配置完成至少一次配对复测；仍属于观察性结果。
- 稳定报告：三模型 × 七问题 × 两阶段 × 每单元五次成功样本，同时保留失败尝试；建议持续 14–30 天。
- 销售展示前：建立可核验的 DongClaw 官方产品页、公司归属、功能边界、版本、联系方式和稳定 URL；否则模型即使提及，也无法判断是在引用真实产品还是生成相似词。

所有外宣必须把“采样观察”“算法解析”“实际业务效果”分开。GEO 能提高内容被发现和引用的机会，但任何平台收录、模型采纳、账号安全或排名结果都不应被承诺。

## 8. 当前执行阻塞（2026-09-10 本机核验）

- 本机进程环境中 `DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`、`ARK_API_KEY`、`AI_BASE_URL` 和 `AI_MODEL` 均未配置；不能从本机直接调用三家模型。
- 已上线环境的千问配置用于内容生成，不应导出生产密钥，也不能把“生成过文章”冒充为“按固定问题完成 AI 可见度采样”。
- 社交媒体账号的登录态只授权内容发布，不等于 Qwen、DeepSeek 或豆包模型采样授权。
- 需要在已授权网页会话中逐条留证，或由项目所有者在本机安全配置 provider API；评估脚本只接收结果，不接收凭据。
- 在提供可公开核验的 DongClaw 官方产品 URL/事实表和精确 RanClaw 竞品来源之前，只能报告提及和显式 URL，不能可靠判定产品事实正确性或自有引用。
