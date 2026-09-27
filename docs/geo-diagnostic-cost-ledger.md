# GEO 诊断闭环与 AI 成本台账契约

## 能力边界

本版本把“诊断—优化—复测”和“模型调用—Token—成本”做成可审计的后端契约。诊断全部在本机执行，不会把客户项目、正文或回答样本发送给外部模型。

已接入的同事交接算法是 `packages/geo-engine` 中可独立验证的纯分析部分：品牌/别名提及、推荐排名、显式 URL 引用与来源分类、情感/属性、声量与置信度、证据驱动建议。运行标识为 `ai-visibility-python-port-1.1.0`，来源包和文件哈希由 `/api/geo/visibility/analyze` 的 `source_trace` 返回。Windows EXE 另有本产品实现的受控模型采样入口：操作员预览并确认后，只向当前配置模型发送一个问题原文，顺序执行 1–5 次。没有接入或伪装成已完成的是同事项目中的自动采样运行时、分布式 worker/队列，以及缺少正式地址和令牌的远程内容引擎。

诊断分是本产品的确定性基线，不等同于搜索平台或大模型厂商的官方排名。要做对比，必须使用相同诊断版本、相同项目和相同采样口径。

## GEO 诊断 API

### 创建项目诊断

`POST /api/geo/diagnostics`

```json
{
  "project_id": 1,
  "persist": true
}
```

项目诊断量化五个维度：产品定位、可发现性基础、竞品参照、内容资产就绪度、AI 可见度基线。结果包含 0–100 分、A–D 等级、P0/P1/P2 缺口、建议动作、目标指标、当前基线和复测规则。

### 创建内容诊断

可传已保存文章：

```json
{"project_id": 1, "article_id": 7, "persist": true}
```

也可传临时内容：

```json
{
  "project_id": 1,
  "title": "待诊断标题",
  "content": "待诊断正文",
  "persist": true
}
```

临时正文只参与内存评分。持久化记录仅保存输入 SHA-256，不保存原始标题或正文。内容诊断复用现有 GEO 评分的实体、关键词、结构、FAQ、事实与引用维度。

### 历史与回放

- `GET /api/geo/diagnostics/runs?project_id=1&limit=20`
- `GET /api/geo/diagnostics/runs/{run_id}`

历史列表是轻量摘要；详情用于展示当时的维度、缺口和复测口径。

## AI Token 与成本账单

项目摘要接口仍可供项目页使用：

`GET /api/ai-usage/projects/{project_id}/summary?limit=50`

桌面端独立“成本账单”界面使用跨项目账单接口：

`GET /api/ai-usage/billing?project_id=&days=30&operation=&cost_status=&limit=200`

账单接口按筛选条件计算全部匹配记录的总览，并返回按操作、模型、项目、日期的拆分和一个有上限的最新明细窗口。界面会明确显示明细窗口与全部匹配数；CSV 只导出当前明细窗口，不把截断结果冒充完整账单。

台账支持真实模型操作 `generate`、`optimize` 和 `platform_adapt`：

- provider 返回 usage 时，状态为 `reported`；
- 调用方使用明确估算器时可记为 `estimated`；
- provider 不返回 usage 时为 `unknown`，不会填成 0；
- 有 Token、无价格时成本为 `unpriced`，不会填成 0；
- 有 Token 明细和价格快照时为 `calculated`，金额字段仍命名 `estimated_cost`，因为账单应以供应商最终账单为准。

浏览器上传、登录、平台发布如果没有调用模型，`token_usage_applicable=false`，不能伪造“发布 Token”。若未来平台适配确实调用模型，应单独记录 `platform_adapt`，不能把浏览器操作混入。

金额按币种分别汇总，不能把 CNY、USD 等直接相加。未知 Token、未定价调用和已核算金额分别展示：未知不是 0，未定价也不是免费。当前 P1 遗留是账单明细游标翻页、预算/超额预警和供应商账单对账。

### 单价配置

桌面管理员通过“成本账单 → 单价设置”维护本机价格目录：

- `GET /api/ai-usage/prices`：读取价格和已检测模型；
- `PUT /api/ai-usage/prices`：保存服务商/模型的输入、输出单价、币种和来源备注；
- `DELETE /api/ai-usage/prices/{id}`：停止对后续调用使用该价格。

单价单位统一为每百万 Token。保存时只有显式传入 `apply_to_unpriced=true` 才补算历史未定价凭证；已经核算的历史快照不会因目录更新或删除而改变。

`AI_PRICE_CATALOG_JSON` 仅作为没有本机精确价格时的兼容回退，格式如下：

```json
{
  "qwen/qwen-plus": {
    "input_per_million": "CURRENT_INPUT_PRICE",
    "output_per_million": "CURRENT_OUTPUT_PRICE",
    "currency": "CNY"
  }
}
```

匹配顺序是 `provider/model`、`*/model`、`provider/*`、`*/*`。阿里云托管的 Qwen 在生成、优化和平台适配中统一使用规范键 `qwen`；已有 `dashscope/qwen-*` 价格目录仍可匹配，已有账本行不会被改写，但汇总会按规范键合并。非 Qwen 的 DashScope 模型和其他 OpenAI-compatible 主机不会被归并。价格按每 1,000,000 Token 计。每条调用记录保存当时的输入/输出单价和币种快照，之后修改配置不会篡改历史金额。不要把文档示例占位符直接用于生产；应在交付当天按供应商正式价目表核对。

## 数据表与迁移

应用启动时幂等创建：

- `geo_diagnostic_runs`：项目/内容诊断结果、输入摘要、算法版本；
- `ai_usage_ledger`：操作、provider/model、Token 明细、usage 状态、价格快照、估算成本和来源引用。

生成成功时会在同一数据库事务写入一次 `generate` 台账；生成最终状态未知时写入 unknown 收据；保存文章后会把对应生成台账关联到文章。优化成功时写入 `optimize` 收据并随 API 响应返回。已有历史生成记录不会倒推未知价格或伪造成本，新台账从本版本开始完整累计。

## 验证

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.test_ai_cost_ledger_service `
  tests.test_geo_diagnostic_service `
  tests.test_geo_diagnostic_cost_api -v
```

测试数据均为合成内容，不调用外部模型，也不执行真实发布。
