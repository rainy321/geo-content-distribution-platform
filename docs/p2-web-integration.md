# GEO Web 内容提效与 P2 分发

本文记录 GEO Web 新增的内容模板、批量生产、文章导入、自动配图，以及 P2 渠道接入范围。

## 已接入的统一工作流

### 内容模板

- 系统内置行业问题拆解、产品解决方案、竞品选择指南、问答知识库 4 个模板。
- 可以创建、编辑、复制和删除自定义模板；内置模板不可覆盖或删除。
- 单篇生成和批量生成都会把所选模板指令传给 AI，但模板本身不会替代品牌事实约束。

### 批量文章

- AI 批量生成单次最多接收 5 个去重主题。
- 每篇成功结果都独立保存为草稿；某次模型请求失败后会停止余下请求并明确标记跳过，避免无界重试和重复计费。
- Excel/CSV 导入单次最多 200 行，要求标题和正文；错误会定位到具体数据行。
- Excel 标准模板可以直接从内容库下载。

### 图片生成与自动配图

- 封面推荐优先匹配同一项目、文章标题、标签和素材标签。
- 没有合适图片时，可生成 1200×628 PNG 中性 GEO 测试封面并登记进素材库。
- 百家号、小红书、抖音、快手创建真实任务时必须有图片；开启自动配图后，系统会优先复用推荐素材，否则生成封面。

### P2 渠道

| 渠道 | Web 内容形态 | 必需素材 | 底层执行器 | 真实验收状态 |
| --- | --- | --- | --- | --- |
| 抖音 | 图片笔记 | 图片 | `DouYinNote` | 待账号登录后验收 |
| 快手 | 图片笔记 | 图片 | `KSNote` | 待账号登录后验收 |
| Bilibili | 视频 | 视频 | `biliup` | 待账号登录和视频素材后验收 |
| 视频号 | 视频 | 视频 | `TencentVideo` | 待账号登录和视频素材后验收 |
| TikTok | 视频 | 视频 | `TiktokVideo` | 待账号登录和视频素材后验收 |

上述适配器已接入统一发布任务、素材路径校验、账号解析、任务状态和超时保护。旧 uploader 如果没有返回可核验的公开链接，任务保持 `processing` 并提示人工核对，不会虚报成功，也不会因为状态不明自动重试。

## API 摘要

| 能力 | 方法与路径 |
| --- | --- |
| 模板列表/创建 | `GET/POST /api/content-templates` |
| 模板更新/删除 | `PUT/DELETE /api/content-templates/{id}` |
| 批量 AI 生成 | `POST /api/articles/generate-batch` |
| 下载 Excel 模板 | `GET /api/articles/import-template.xlsx` |
| 导入文章 | `POST /api/articles/import` |
| 推荐封面 | `GET /api/articles/{id}/images/recommend` |
| 生成封面 | `POST /api/articles/{id}/images/generate` |
| 渠道能力清单 | `GET /api/publish/platforms` |

## 部署边界

- Vercel 部署是 GEO Web 的在线 Demo/控制面，默认 `DEMO_MODE=true`，不会访问真实平台。
- Playwright、Chrome、`biliup` 和账号 Cookie 属于本地 Worker 运行面；真实发布不能仅靠 Vercel Serverless Function 完成。
- Vercel 本地文件系统和默认 SQLite 不提供持久化保证。生产环境需要接入持久数据库与对象存储，不能把生成封面、上传视频或任务历史只保存在实例文件系统。
- 真实发布还必须显式开启 `ALLOW_REAL_PUBLISHING=true`、连接有效账号，并在界面逐次确认。平台验证码、人机验证、实名和风控仍需账号持有人处理。
- 这次开发没有代替用户执行 P2 渠道的真实发布，因此“代码接入完成”不等于“每个账号的线上链路已验收”。

## 本地验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd sau_frontend
npm run build
```

真实链路验收应逐个平台进行：先登录并检测 Cookie，再准备符合平台要求的素材，最后只对用户明确授权的稿件执行一次发布；超时或状态不明时先到平台后台核对，不自动重试。
