# GEO Windows EXE 产品化差距矩阵

更新日期：2026-09-10

评估对象：当前共享工作树中的 Windows 本机软件 `GEO-Content-Platform.exe` 及其待打包代码
参考文档：`D:\GEO_MVP\docs\GEO两天产品集成项目计划书.docx`

## 1. 评估口径与当前结论

计划书只作为需求参考。与当前用户指令冲突的旧口径不继续采用：交付形态只按 Windows EXE 评估，不安排网站部署；界面按当前全软件设计要求评估，不受计划书中“只调整 AI 内容创作页、保留青绿色视觉”的旧约束限制。

状态定义：

- **已完成**：代码、界面或构建能力已经存在，并有当前测试或成品证据；本项不再列为开发待办。
- **部分完成**：主要结构已经存在，但缺少真实环境验收、商业授权、成品打包或完整产品操作面。
- **未完成**：当前工作树没有可交付实现或可核验成品。

当前结论：

- 从功能代码看，软件已经超过内部验收 MVP：本机登录、媒体账号登录/退出、内容生成、诊断、成本、批量 20、发布预检、发布容错、Bilibili 固定运行时接入、同事算法本机移植和受控模型采样均已有实现。
- 从成品看，**当前最新 EXE 仍是 2026-09-09 的旧构建**。其 manifest 对应提交 `6fda29e0ccefebf8f144bb65602b24e3cc5fbefd`，`authenticode_status=NotSigned`、`bilibili_runtime_enabled=false`；当前工作树有 94 个变更路径，因此这些新增能力尚不能视为已经进入可交付 EXE。
- 当前适合在完成本轮清洁构建后作为“内部受控 Beta / 授权真实发布试用版”验收；在代码签名、算法及依赖权利、客户许可文本、Windows 兼容性和真实渠道验收闭环完成前，**不能判定为可对外销售的软件成品**。

## 2. 当前核验快照

| 核验项 | 结果 | 证据 |
| --- | --- | --- |
| 当前分支与 HEAD | `codex/windows-desktop-real-publish`；`6fda29e0ccefebf8f144bb65602b24e3cc5fbefd` | `git rev-parse HEAD`、`git branch --show-current` |
| 工作树 | 94 个变更路径，当前新增功能尚未形成清洁发布提交 | `git status --short` |
| 针对性后端回归 | 165 项通过，1 项按设计跳过；未访问真实模型或真实平台 | `python -m unittest`：认证、登录、退出、发布预检/执行/核验、Bilibili、诊断、成本、批量、采样和 DongClaw 评估模块 |
| 前端回归 | 52 项通过 | `sau_frontend` 下 `npm test` |
| UI 参考图核验 | Dashboard 与 AI 可见度页通过 1536×1024 和 1280×800 检查，无开放 P0/P1/P2 视觉问题 | `design-qa.md`、`sau_frontend/tests/dashboardPresentation.test.js` |
| 最新已有 EXE | 授权真实发布构建通过旧版健康/UI 冒烟，但未签名且不包含当前未提交改动 | `release/windows/GEO-Content-Platform-1.0.0-authorized-real-20260909T132007Z-6fda29e0/GEO-Content-Platform.manifest.json` |
| 测试洁净度 | 测试最终通过，但一次 Waitress 测试服务器关闭时打印 `WinError 10038` 和未关闭 socket 的 `ResourceWarning` | 本次 165 项针对性回归输出；不影响断言结果，但应在发布门禁中消除噪声 |

## 3. P0：可用性、安全与可销售前硬门槛

| 编号 | 能力 | 状态 | 当前实现与证据 | 剩余阻塞 / 放行条件 |
| --- | --- | --- | --- | --- |
| P0-01 | 本机 EXE 架构与数据隔离 | **已完成** | 单文件 PyInstaller EXE；Waitress 仅监听随机 `127.0.0.1` 端口；Edge WebView2 内嵌 Vue；本机数据位于 `%LOCALAPPDATA%\GEOContentPlatform`；单实例、健康检查、退出清理和启动备份已实现。证据：`geo_desktop.py`、`desktop/GEO-Desktop.spec`、`services/desktop_backup_service.py`、`tests/test_geo_desktop.py`、`tests/test_desktop_backup_service.py`。 | 无新增开发待办；最终只需随本轮新成品重新执行既有健康与 UI 冒烟。 |
| P0-02 | 软件管理员首次设密、登录与退出 | **已完成** | PBKDF2-HMAC-SHA256、随机盐、HttpOnly/SameSite Cookie、失败限流、12 小时会话和“退出工作区”均已接线。证据：`services/local_auth_service.py`、`sau_backend.py` 的 `/api/auth/*`、`sau_frontend/src/components/AccessGate.vue`、`sau_frontend/src/App.vue`、`tests/test_local_auth_service.py`、`tests/test_access_control.py`。 | 无新增开发待办。注意这不是磁盘加密。 |
| P0-03 | 媒体账号登录等待、断线重连、取消与明确终态 | **已完成** | 登录 SSE v2 有阶段、心跳、有限重连、二维码/浏览器状态、超时与动作提示；平台窗口弹出后不会立即把传输断开等同于登录失败。证据：`myUtils/login.py`、`sau_frontend/src/utils/loginSession.js`、`tests/test_login_sse.py`、`tests/test_all_platform_login_cancel.py`、`sau_frontend/tests/loginSession.test.js`。 | 无新增开发待办；渠道页面变化仍需在真实账号验收中监控。 |
| P0-04 | 媒体账号退出 / 解绑与任务授权撤销 | **已完成** | 账号卡片已有“退出 / 解绑”；删除本机凭证、撤销绑定任务授权并处理检查/断开竞态，不伪装成平台全设备退出。证据：`services/media_account_service.py`、`sau_backend.py` 的 `/api/media-accounts/{id}/disconnect`、`sau_frontend/src/views/AccountManagement.vue`、`tests/test_media_account_disconnect.py`。 | 无新增开发待办。 |
| P0-05 | 内容引擎统一契约、品牌快照、本地评分与可回滚适配 | **已完成** | `ContentGenerationService` 在 Qwen 与同事内容引擎适配器之间路由；请求携带项目快照；同事分数保留为 `provider_geo_score`，不覆盖本地 GEO 分；Trace、引擎、版本、耗时、警告和回退标识可追踪。证据：`services/content_engine_contract.py`、`services/content_engine_adapters.py`、`services/content_generation_service.py`、`tests/test_content_engine_*`、`tests/test_three_run_main_flow_acceptance.py`。 | 无新增开发待办；同事远程内容服务的实际地址、令牌、镜像和协议验收另见 P0-15。 |
| P0-06 | 生成幂等、状态未知保护与运行回执 | **已完成** | 生成记录持久化，幂等回放不重复调用模型；超时/状态未知不自动重试，不静默切换另一个付费模型；缺失用量保持“未知”而不是 0。证据：`services/content_generation_run_service.py`、`services/content_generation_service.py`、`tests/test_content_generation_service.py`、`tests/test_articles_generate_api.py`。 | 无新增开发待办。 |
| P0-07 | 批量生成 20 篇、逐项继续与逐篇成本 | **已完成** | 单批上限已从旧值扩大为 20；串行执行；单项失败不会中断后续项；幂等回放不会再次生成或重复计费；响应和界面均展示批次汇总及逐篇 Token/成本。证据：`sau_backend.py` 的 `BATCH_GENERATION_LIMIT=20` 与 `/api/articles/generate-batch`、`sau_frontend/src/utils/batchGeneration.js`、`sau_frontend/src/views/ContentCreation.vue`、`tests/test_p2_content_workflows.py`、`sau_frontend/tests/batchGeneration.test.js`。 | 无新增开发待办；需进入本轮新 EXE 后做一次成品批量冒烟。 |
| P0-08 | Token、价格快照与成本账单 | **已完成** | 独立“成本账单”界面可按项目、时间、操作和核算状态筛选，并展示全部匹配汇总、按操作/模型/项目/日期拆分和逐笔回执；`generate`、`optimize`、真实发生的 `platform_adapt` 和 `visibility_sample` 可记录 provider/model、输入/输出/总 Token、usage 状态、单价快照、币种和估算成本。未知用量或未配置价格不会显示为 0；纯登录/上传/发布明确为 Token 不适用。证据：`services/ai_cost_ledger_service.py`、`sau_frontend/src/views/CostLedger.vue`、`docs/geo-diagnostic-cost-ledger.md` 及对应测试。 | P0 已完成；每次商业发布仍要填入并核对当日 `AI_PRICE_CATALOG_JSON`，否则只能如实显示“未定价”。明细游标、价格目录、预算和供应商账单对账见 P1-06。 |
| P0-09 | 平台字数、标题、验证、账号频率与可引用性预检 | **已完成** | 抖音正文硬上限 1000/安全线 920、标题 20/18；快手正文 500/450；Bilibili 简介 2000；小红书正文 1000/900；其他未核验限额保持未知；创建任务前生成并展示平台副本，原稿不变，并对实际待发送标题和正文重算 `ai-citeability-rules-2.1.1` 门禁与 `2026-09-10.2` 平台离线预检；字数、Markdown、占位符、来源边界或门禁不合规时不能创建真实任务。话题仅尝试首选匹配一次，验证码/扫码/滑块提示、各渠道发布风险声明均已提供。证据：`services/publish_preflight_service.py`、`services/platform_publish_policy_service.py`、`sau_frontend/src/utils/publishPreflight.js`、`sau_frontend/src/views/DistributionCenter.vue` 及对应测试。 | 平台规则和动态审核会变化，软件只保证确定性本地约束，不保证平台审核通过；未知额度不得冒充官方硬上限，门禁通过也不代表模型必然采纳。 |
| P0-10 | 真实发布强确认、失败继续、状态更新与防重复发送 | **已完成** | 任务绑定明确账号和不可变内容指纹；单项/批次均需强确认；批次顺序执行且失败、验证或待核验不阻塞后续；发布器无法证明成功时转 `need_action`，核验 API 不重新发送；异常退出后的 `processing` 恢复为待核验，不自动重发。证据：`services/publish_job_service.py`、`services/publish_job_executor.py`、`sau_frontend/src/views/DistributionCenter.vue`、`tests/test_publish_job_executor.py`、`tests/test_publish_reconcile_api.py`、`sau_frontend/tests/publishBatchReconcile.test.js`。 | 无新增开发待办；必须在本轮新 EXE 中用授权测试内容验证状态能从执行窗口回写到界面，并逐项核对旧的模糊任务，不能自动续发。 |
| P0-11 | 十渠道能力清单与真实发布器 | **部分完成** | 知乎、头条、百家号、搜狐号、小红书、抖音、快手、Bilibili、视频号、TikTok 均有能力声明和账号类型；工厂可创建对应发布器。证据：`services/platform_capability_service.py`、`services/real_publisher_factory.py`、`tests/test_real_publisher_factory.py`、各 `uploader/*` 与发布流测试。 | 代码级覆盖不等于平台级可售承诺。抖音/快手仍标为 `experimental`，Bilibili/视频号为 `review_required`；本轮新 EXE 需要按实际 SKU 对承诺渠道逐一完成“登录—预检—提交—验证/核验—状态回写”的受控实测，未通过的渠道必须在界面和销售材料中保持实验/需人工核验标签。 |
| P0-12 | Bilibili 登录与视频发布 | **部分完成** | 已改为 EXE 外部固定 `biliup 1.2.4` 兼容运行时，并核对文件大小、固定 SHA-256 和离线版本；登录、读取凭据、上传子进程纳入桌面进程树，取消/超时/退出只清理本次进程。证据：`uploader/bilibili_uploader/runtime.py`、`services/media_publisher_adapters.py`、`tests/test_bilibili_runtime.py`、`tests/test_media_publisher_adapters.py`。 | 最新已打包 EXE manifest 仍是 `bilibili_runtime_enabled=false`；当前 Bilibili 账号尚未完成真实登录和视频链路验收；`biliup` 不随 EXE 分发且其商业使用权未获确认。对外 SKU 必须二选一：取得官方开放平台/兼容运行时授权并完成真实验收，或明确关闭 Bilibili，不得宣称已商用可用。 |
| P0-13 | GEO 客户诊断—优化—复测闭环 | **已完成** | 项目/内容五维诊断、0–100 分、等级、P0/P1/P2 缺口、行动目标、证据、历史和同口径复测已实现；新增基于行业、内容就绪度、可见度基线和本机能力的 10 渠道试验优先级，逐项展示适配分组成、目标、验证口径与风险；临时正文持久化只保存哈希。证据：`services/geo_diagnostic_service.py`、`sau_backend.py` 的 `/api/geo/diagnostics*`、`sau_frontend/src/views/AiVisibility.vue` 及对应测试。 | 无新增开发待办；渠道适配分只用于安排自己的对照试验，不是平台权重、流量预测、收录概率或审核承诺。对外表达必须保留“确定性准备度，不等于模型官方排名或长期收录效果”的边界。 |
| P0-14 | 同事 GEO 底层算法移植 | **技术完成 / 权利待确认** | 发布后分析已移植为 `ai-visibility-python-port-1.1.0`；本轮又将同事的查询建议、内容结构与证据约束产品化为 `geo-article-compiler-2.1.1` 和 `ai-citeability-rules-2.1.1`，形成发布前蓝图、7 维门禁与发布后复测闭环。证据：`services/ai_visibility_service.py`、`services/geo_article_compiler_service.py`、`docs/GEO算法融合说明.md` 及对应测试。 | 技术移植不应重复开发；但交接 ZIP 无 `LICENSE/NOTICE`，外部分发和商业再许可权仍需书面确认。分布式 worker、自动采集运行时和云端 SaaS 基础设施没有纳入 EXE，不得宣传为已集成。 |
| P0-15 | 当前配置模型的受控真实采样 | **部分完成** | EXE 后端已有 `/api/geo/visibility/sample`；只发送用户确认的 prompt，不发送项目、文章、账号或 Cookie；单次 1–5 个串行样本；幂等、防重复计费、状态未知停机、部分成功、用量与成本回执均已实现。系统设置可一次保存统一 AI 引擎，Windows 当前用户 DPAPI 保护 API Key；写稿、调优和回测共用，仍保留仅本次内存覆盖。证据：`services/unified_ai_config_service.py`、`services/ai_visibility_sampling_service.py`、`sau_frontend/src/views/SystemSettings.vue`、`sau_frontend/src/views/AiVisibility.vue` 及对应测试。 | 自动化测试只用假响应。本轮未向任何真实模型发送数据，尚无 Qwen、DeepSeek、豆包三家同口径真实样本和供应商账单核对；未完成前只能销售“本机导入分析 + 可配置采样能力”，不能销售“已验证三模型效果”。同事远程内容引擎仍缺正式地址、令牌、镜像摘要和健康契约。 |
| P0-16 | DongClaw 实例报告与效果声明 | **部分完成** | 已建立固定 7 问题 × 3 provider、基线/复测配对、地区/问题/结果矩阵、失败分母和证据边界；已有 JSON/Markdown 报告骨架。证据：`services/dongclaw_evaluation_service.py`、`scripts/run_dongclaw_evaluation.py`、`docs/dongclaw-geo-evaluation-protocol.md`、`artifacts/dongclaw-geo-evaluation/*`、`tests/test_dongclaw_evaluation_service.py`。 | 当前报告状态为 `NOT_EXECUTED`，无 Qwen/DeepSeek/豆包真实回答，不能量化 GEO 提升；RanClaw 精确实体也未核验。需在用户提供有效模型会话/凭据后按固定问题完成基线，并将真实失败同样纳入分母；短周期只可证明流程可用，不能宣称因果效果。 |
| P0-17 | 当前代码形成新的可回滚 EXE | **以发行 manifest 判定** | 源代码不写死“最新”产物目录，避免构建后再修改提交导致 EXE 与 Git SHA 脱钩；只有 `release/windows` 中版本 `1.2.2`、提交一致、健康/UI 双冒烟通过且带 SHA-256 的 `authorized_real` manifest 才是本轮成品，旧 EXE 继续作为回滚件。 | 对每个交付候选运行 `scripts/build_windows_desktop.ps1 -PublishingProfile authorized_real`；保留 EXE、ZIP、SHA-256、manifest 和旧回滚包。若任一探针失败，候选不得交付。 |
| P0-18 | 发布门禁覆盖全部新增能力 | **已完成（代码门禁）** | 本轮最终源代码回归共运行后端 780 项，其中 779 项通过、1 项按设计跳过；前端 79/79 通过，Vue 生产构建和 `git diff --check` 均通过。构建脚本已显式纳入统一 AI、GEO 编译/可引用性、10 平台预检、登录、队列恢复与既有发布测试，并继续执行成品健康/UI 冒烟。证据：`scripts/build_windows_desktop.ps1`。 | 代码级门禁无新增待办；每个发行候选仍必须以同一提交实际跑完授权 EXE 打包和两个成品冒烟，并把产物哈希、manifest 与签名状态留档。 |
| P0-19 | Authenticode 代码签名与成品来源信任 | **未完成** | 构建会计算 SHA-256，并把签名状态写入 manifest；最新 EXE 为 `NotSigned`。证据：`scripts/build_windows_desktop.ps1`、最新 release manifest。 | 取得公司代码签名证书或合规云签服务；在构建后签名、验证时间戳、再生成最终哈希/ZIP；建立证书轮换与吊销流程。未签名成品不得作为正式商业版宣称“可信发布者”。 |
| P0-20 | 第三方与同事算法商业权利 | **未完成** | 仓库已有 MIT `LICENSE` 和初步 `THIRD-PARTY-NOTICES.txt`；构建可携带 notice。 | 初步清单明确不是完整 SBOM/法律审核；同事交接包缺许可；`biliup` 不具备随包分发授权结论。对外销售前必须取得同事书面授权、生成完整依赖/SBOM、随包放置所需许可证文本，并完成平台条款、商标和再分发审核。 |
| P0-21 | 客户商业许可、隐私与操作责任文本 | **未完成** | 软件内已有平台风险提示和“建议量非官方配额”的声明；本机数据边界在 `docs/windows-desktop.md` 有说明。 | 尚无面向客户的 EULA/商业许可、隐私说明、模型数据发送同意、账号授权确认、数据保留/删除政策和支持边界。必须由业务/法务确定后随 EXE 展示并可留存同意版本；“封号概不负责”不能替代技术节流或有效合同条款。 |
| P0-22 | 凭据与本机敏感数据保护 | **部分完成** | 统一 AI API Key 已由 Windows 当前用户 DPAPI 加密并原子保存，状态接口不回显密钥；仅本次覆盖仍只在页面内存。日志/成本凭据有字段白名单；账号文件放入独立本机目录；应用登录不保存明文口令。 | SQLite、媒体账号 Cookie、WebView 状态和普通本机文件仍未做静态加密。对外客户版需要继续保护媒体凭据，并提供敏感数据清理/导出控制。 |
| P0-23 | Windows 支持矩阵与前置运行时 | **部分完成** | 旧授权构建已在当前机器通过 Edge WebView2、系统 Chrome、Playwright/Patchright 纯本地探针；缺前提时健康检查会阻断真实发布。 | 尚无 Windows 10/11、不同 DPI、普通用户权限、无 Chrome、无/旧 WebView2、中文路径、杀毒软件和离线环境的正式兼容矩阵。正式商业放行前应在干净虚拟机至少覆盖支持的 OS/架构，并明确最低版本和失败提示。 |

## 4. P1：完成 P0 后的商业可运营能力

| 编号 | 能力 | 状态 | 当前实现与证据 | 剩余工作 |
| --- | --- | --- | --- | --- |
| P1-01 | Windows 安装器与标准卸载 | **未完成** | 当前为便携单 EXE + 手工 SHA/manifest；`docs/windows-desktop.md` 明确没有安装向导。 | 制作签名安装器，检查/安装 WebView2 前提、创建开始菜单入口、保留用户数据卸载选项、支持静默安装参数，并验证升级/卸载不删除项目与账号数据。 |
| P1-02 | 签名自动更新与回滚 | **未完成** | 当前升级靠手工替换 EXE，回滚靠换回旧 EXE；已有数据库启动备份和不可覆盖 release 目录。 | 建立签名更新清单、版本通道、下载完整性校验、更新前备份、失败自动回滚和离线更新包；不能只下载后直接覆盖正在运行的 EXE。 |
| P1-03 | 软件授权 / 激活 / 到期控制 | **未完成** | 当前只有本机管理员认证，没有客户许可证、席位或设备授权。 | 决定永久授权/订阅/试用模式；实现离线宽限、设备迁移、吊销、时钟回拨处理和不影响客户数据导出的到期策略。若首批采用合同 + 人工交付，也应在 manifest 中记录客户、授权范围和版本。 |
| P1-04 | 全软件 UI 一致性与可访问性验收 | **部分完成** | Dashboard、AI 可见度页和全局黑白设计已按参考图完成并通过两种窗口宽度 QA；真实数据为空时不伪造趋势。证据：`design-qa.md`、`sau_frontend/src/views/Dashboard.vue`、`AiVisibility.vue`。 | 当前视觉证据没有逐页覆盖项目、创作、内容库、素材、账号、发布、设置和关于页的所有状态。需完成全路由截图矩阵、键盘焦点、缩放 125%/150%、高对比度、空/错/长文本状态和中文截断检查。 |
| P1-05 | 客户报告导出 | **部分完成** | DongClaw 评估器可生成结构化 JSON/Markdown；诊断和可见度历史可回放。 | 增加 EXE 内一键导出 PDF/Excel，包含口径版本、样本/失败分母、地区与问题矩阵、成本、发布证据、前后变化和免责声明；禁止在无样本时生成伪效果数值。 |
| P1-06 | 账单游标、价格目录、预算与对账 | **部分完成** | 后端支持版本化 `AI_PRICE_CATALOG_JSON` 和历史价格快照；独立成本账单已区分全部匹配汇总与有限明细窗口。 | 为明细增加稳定游标翻页；增加仅管理员可编辑的本机价格目录、导入/导出、币种、预算预警和供应商账单对账。价格更新必须由人确认来源与日期，不能静默修改历史成本。 |
| P1-07 | 备份、恢复与数据迁移向导 | **部分完成** | 启动和手工 SQLite 备份带完整性检查；文档说明整目录迁移。 | 增加 EXE 内恢复前预检、全目录导出、加密备份、恢复演练和版本兼容提示；当前“复制目录/替换数据库”仍偏运维操作。 |
| P1-08 | 一键支持包与崩溃诊断 | **部分完成** | 有 `logs/desktop.log`、健康报告和错误码；发布任务保留 Trace 与状态。 | 增加脱敏支持包导出、崩溃钩子、版本/manifest/运行时探针汇总、可选择附加数据库结构信息；默认不得打包 Cookie、API Key、正文或客户回答。 |
| P1-09 | 数据删除与敏感记录生命周期 | **部分完成** | 账号解绑、项目级联删除和不保存模式已有局部能力。 | 为可见度原文、诊断、成本台账、日志、备份和 WebView 缓存分别提供可见的保留期与删除入口；删除前说明影响并保留审计摘要。 |
| P1-10 | 发布规则版本维护 | **部分完成** | 平台限制有统一版本 `2026-09-10.2`，预检与任务冻结保存实际副本。 | 建立离线规则包导入、变更说明、回归样例和回滚；平台 DOM 或字段变化时，应能禁用单一渠道，不需要整体更新所有业务数据。 |
| P1-11 | 文档一致性 | **已完成（本轮范围）** | `docs/visibility-live-sampling.md`、`docs/windows-desktop.md`、`docs/ai-visibility-integration.md` 与 `docs/首次使用.md` 已统一：区分手工导入与操作员确认的 1–5 次受控采样，明确只外发 prompt、缺凭据不调用、模糊结果不重试，并排除网页部署/服务器迁移。桌面设置文案另有自动化门禁，禁止回退到 Vercel/浏览器标签页表述。 | 后续每次增加 provider、调度能力或数据字段时同步更新数据披露、失败语义和回滚章节；本轮无遗留矛盾。 |
| P1-12 | 正式发行版本与数据库迁移治理 | **部分完成** | 本轮桌面发行版本已提升为 `1.2.2`，GEO 编译与可引用性门禁为 `2.1.1`；表使用幂等创建，构建生成 build-info/manifest/哈希。 | 增加数据库 schema 迁移版本、向前/向后兼容策略、发行说明、已知问题和版本升级测试；仅靠 `CREATE TABLE IF NOT EXISTS` 不足以支撑长期字段变更。 |

## 5. P2：规模化与长期差异化

| 编号 | 能力 | 状态 | 当前实现与证据 | 后续方向 |
| --- | --- | --- | --- | --- |
| P2-01 | 周期性 AI 可见度采样与长期趋势 | **未完成** | 当前是人工触发的 1–5 次受控串行采样或手工回答导入，短测只能验证流程。 | 在独立默认关闭开关下增加计划任务、失败分母、预算上限、模型版本变更断点和至少 14–30 天同口径趋势；仍须每次遵守数据发送授权。未完成前不得宣称豆包、千问或其他模型已长期采纳。 |
| P2-02 | 多供应商实验与因果评估 | **部分完成** | DongClaw 协议已定义 3 provider × 7 问题 × 基线/复测配对。 | 增加随机化、盲测、置信区间、样本量建议、模型/联网模式分层和变更日志；不能用当天短测宣称长期因果提升。 |
| P2-03 | 同事远程引擎与分布式队列 | **部分完成** | 统一内容适配器和回退契约已存在，纯分析算法已经本机化。 | 收齐正式服务地址、令牌、镜像摘要、许可、健康接口和容量口径后再接；保持无状态、最小数据字段和可随时切回本机/Qwen。 |
| P2-04 | 官方平台 API 逐步替换浏览器自动化 | **未完成** | 当前主要依赖本机浏览器和 Bilibili 外部兼容运行时。 | 对可申请开放平台的渠道逐一评估 OAuth、审核、配额和发布接口；官方 API 与浏览器适配器保持同一任务状态契约，按渠道切换而非全量重写。 |
| P2-05 | 多用户、团队角色与审计 | **未完成** | 当前明确为本机单用户 EXE。 | 若未来销售给团队，再增加本机多角色或企业身份接入、操作审计和授权分离；当前单机 SKU 不把它列入 P0/P1。 |
| P2-06 | 新渠道连接器 | **未完成** | 当前固定 10 个渠道。 | 只有在现有渠道稳定后，再按公开可抓取价值评估微信公众号、微博、YouTube、CSDN/掘金、LinkedIn/Medium 等；不是本轮 EXE 交付门槛。 |
| P2-07 | 远程同步、跨设备与企业备份 | **未完成** | 当前数据严格保存在本机。 | 若商业模式需要，再设计端到端加密同步、冲突解决、地域和删除机制；不能直接复用正式环境数据库。 |
| P2-08 | 可选匿名质量遥测 | **未完成** | 当前没有遥测或崩溃上报。 | 仅在显式同意、字段最小化和可关闭的前提下采集版本、错误码与运行时健康；不得上传内容、回答、Cookie 或密钥。 |

## 6. 从“内部受控 Beta”到“可售 EXE”的集中放行清单

以下只包含尚未完成或部分完成的 P0；上表标记“已完成”的能力不重复开发：

1. 冻结当前工作树，补齐正式构建门禁，跑全量后端/前端回归并消除测试服务器关闭警告。
2. 从清洁提交重打 `authorized_real` EXE，完成隔离健康检查、原生 UI 冒烟、批量 20、诊断/成本/采样入口和受控发布状态回写验收，保留旧 EXE 回滚包。
3. 完成 Authenticode 签名、可信时间戳、签后哈希与签名验证。
4. 取得同事算法书面商业授权，完成完整 SBOM/许可证/商标和 Bilibili 运行时权利审核。
5. 补齐 EULA、隐私/数据发送同意、账号操作授权、数据保留删除及支持边界。
6. 用干净 Windows 10/11 环境完成支持矩阵；确认普通用户、WebView2、Chrome、不同 DPI 和无前置运行时提示。
7. 对销售承诺的每个发布渠道完成受控真实端到端验收；未通过的渠道必须关闭或标记实验/需人工核验。
8. Bilibili 完成固定运行时探针、账号登录和视频发布核验，或从当前商业 SKU 中明确关闭。
9. Qwen、DeepSeek、豆包只有在取得各自有效配置并完成真实样本后，才能宣称“三模型采样已验证”；否则保持“可配置采样 + 手工导入分析”的准确口径。
10. DongClaw 报告只有导入真实回答、保留失败分母并完成同口径基线后，才能给出量化结果；短周期报告必须标明只能验证流程，不能证明长期 GEO 因果效果。

## 7. 明确不进入本轮 EXE 待办的事项

- 不部署、迁移或重新发布任何网站；不把网页端作为产品交付形态。
- 不重复已经完成的数据库迁移、服务器部署、内容引擎适配、本地评分、批量 20、诊断、成本台账或发布状态机。
- 不自动重新发送历史状态未知的发布任务；必须先由操作员在平台后台核验。
- 不为验收发送既有客户/项目数据；真实模型采样只发送用户预览并确认的测试 prompt。
- 不恢复已经暂停的监督自动化，也不默认开启无人值守真实发布。
