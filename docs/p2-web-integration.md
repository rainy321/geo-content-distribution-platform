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
| 抖音 | 图片笔记 | 图片 | `DouYinNote` | 首次最终点击后平台无记录；待新授权复验 |
| 快手 | 图片笔记 | 图片 | `KSNote` | 首次最终点击后平台无记录；待新授权复验 |
| Bilibili | 视频 | 视频 | `biliup` | 官方 `601` 限流且内容管理为空；待冷却和新授权 |
| 视频号 | 视频 | 视频 | `TencentVideo` | 创作者中心已出现精确标题与发布时间；公开链接待取 |
| TikTok | 视频 | 视频 | `TiktokVideo` | 登录有效，但尚未上传或点击 Post；待新授权复验 |

上述适配器已接入统一发布任务、素材路径校验、账号解析、任务状态和超时保护。最终发布按钮最多点击一次；结果不明时先只读核对平台内容列表，不会自动重试。AI 测试内容在抖音、快手找不到或无法确认声明控件时，会在最终按钮前失败。旧 uploader 如果没有返回可核验的公开链接，任务保持 `processing` 并提示人工核对，不会虚报公开链接。

本轮合规视频为本地 `videoFile/geo-p2-neutral-test.mp4`：10 秒、720×1280、H.264、无音频的中性几何动画，不含人物、音乐、平台 Logo、品牌或效果承诺。视频号已取得创作者后台发布证据；其他平台的真实结果和继续条件以 `docs/GEO-MVP-ACCEPTANCE.md` 未完成项账本为准。

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
- 本次已在项目所有者登录并明确授权后执行 P2 首轮真实验收，但只有视频号取得创作者后台发布证据；不能把其余四个平台的技术尝试写成发布成功。

## 本地验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd sau_frontend
npm run build
```

真实链路验收应逐个平台进行：先登录并检测 Cookie，再准备符合平台要求的素材，最后只对用户明确授权的稿件执行一次发布；超时或状态不明时先到平台后台核对，不自动重试。
