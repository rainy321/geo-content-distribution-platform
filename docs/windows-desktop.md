# GEO Windows 桌面版交付说明

## 当前交付定位

`GEO-Content-Platform.exe` 是 Windows 本机单用户便携 Beta，不是正式网站的快捷方式，也不是多租户客户端。它在本机启动 Flask/Waitress 服务，在随机的 `127.0.0.1` 端口提供 API，再由 Edge WebView2 显示内置的 Vue 界面。

当前产品版本为 `1.0.0`。EXE 没有安装向导、自动更新器、Windows 服务或开机启动项；同一数据目录一次只允许一个实例运行。

同事交接 ZIP 未包含 `LICENSE` 或 `NOTICE`。在取得权利人的书面内部授权或补齐可核验的许可声明前，包含其 Python 移植算法的构建只用于内部受控 Beta，不作为已完成外部商业分发授权的版本。

面向运营人员的首次使用步骤见 [首次使用](./首次使用.md)，AI 可见度后端契约和算法边界见 [AI 可见度集成说明](./ai-visibility-integration.md)。

## 产品边界

- 延续品牌项目、内容生成、统一内容引擎适配、本地 GEO 规则分、内容库、素材、媒体账号和发布任务逻辑。
- 新增 AI 可见度工作台：导入已经取得的回答，使用本机确定性规则完成品牌提及、推荐位次、引用、情感、声量和建议分析。
- AI 可见度页面不会主动请求千问、ChatGPT 或其他外部模型。页面中的 `provider`、`model` 只是用户填写的样本溯源标签。
- 选择“保存到本机分析历史”后，问题、回答原文、覆盖项和完整结果会写入本机 SQLite；不保存时只在当前请求和界面中处理。
- 桌面宿主不会读取正式服务器数据库，也不会把网站上的已有客户或项目数据复制到本机。
- AI 内容创作是另一条功能链路；只有在另行配置模型凭据时才可能调用外部内容模型。不能把“可见度本地分析”理解为整个软件完全离线。

## 运行架构

```text
GEO-Content-Platform.exe
  ├─ 桌面宿主：路径、单实例、生命周期、日志与备份
  ├─ Waitress：仅监听 127.0.0.1 的随机端口
  ├─ Flask：认证、业务 API、SQLite 与安全闸门
  ├─ Vue：内置产品界面
  └─ Edge WebView2：本机窗口与浏览器存储
```

应用关闭时，桌面宿主会停止安全调度器和内置 HTTP 服务。程序异常退出后，下次启动仍从 SQLite 持久状态恢复；启动过程不会清空现有数据。

## 本机目录

默认根目录为 `%LOCALAPPDATA%\GEOContentPlatform`。标题栏右侧的“本机数据”按钮可以打开该目录。

| 路径 | 内容 | 备份建议 |
| --- | --- | --- |
| `data\geo.db` | 项目、文章、生成记录、可见度历史、账号索引和发布任务 | 必须备份 |
| `media\` | 上传和生成的本机素材 | 需要随业务数据备份 |
| `accounts\` | 媒体账号会话文件 | 敏感；只交给获授权人员 |
| `config\auth.json` | 会话签名密钥、口令盐和摘要 | 敏感；不能公开或单独删除 |
| `backups\` | 经过 SQLite 完整性检查的数据库快照 | 默认保留最近 5 份 |
| `logs\desktop.log` | 桌面启动与生命周期日志 | 故障排查时提供，先检查敏感内容 |
| `webview\` | WebView2 的本机浏览器状态 | 一般不作为业务恢复依据 |

可以通过 `GEO_DESKTOP_DATA_DIR` 或命令行 `--data-dir` 指定隔离目录；该能力主要用于测试或受控运维。使用不同目录会得到完全独立的数据库、口令和单实例锁。

## 登录与本机安全

首次启动必须设置本机管理员口令。口令经随机盐和 600,000 次 PBKDF2-HMAC-SHA256 计算后保存；软件不保存可找回的明文口令。登录状态由 HttpOnly、SameSite=Lax 会话 Cookie 维护，默认会话有效期为 12 小时。

这是应用级访问边界，不是磁盘加密：SQLite、素材和账号文件没有使用应用内静态加密。Windows 管理员、本机恶意软件或能够读取当前用户目录的人仍可能读取这些文件。交付设备应启用 Windows 登录保护、磁盘加密和最小权限。

## 数据库备份

- 已有数据库非空时，每次启动会先创建一份 SQLite 在线快照，并执行 `PRAGMA integrity_check`。
- 登录后点击标题栏的“立即备份”，可以按需创建相同类型的快照。
- 自动和手动数据库备份共用 `backups\` 目录，默认只保留最新 5 份 `geo-backup-*.db`。
- 数据库快照不包含 `media\`、`accounts\`、`config\`、`logs\` 或 `webview\`。完整迁移或灾难恢复应在软件完全退出后复制整个 `%LOCALAPPDATA%\GEOContentPlatform`。
- 恢复前先保留当前目录副本；不要在软件运行时直接替换 `geo.db`。

## 发布安全边界

桌面 Beta 的真实发布使用多层关闭，而不是只依赖一个界面按钮：

1. 桌面宿主每次启动都会覆盖系统环境，强制设置 `ALLOW_REAL_PUBLISHING=false` 和 `ENABLE_BILIBILI_RUNTIME=false`。
2. 桌面调度器固定以 `allow_real=False` 启动；真实执行 API 和旧版 `/postVideo`、`/postVideoBatch` 也在进入发布器前检查总闸门。

因此当前 EXE 可以准备内容、维护任务和查看状态，但不会执行真实平台发布。它也没有打包 Playwright、Patchright、Chromium 或 Bilibili 运行时。不能通过设置 Windows 环境变量永久解锁这一构建；未来若交付发布 Worker，应使用独立、可审计且经过授权的安装包。

## 安装、升级、回滚与卸载

### 安装

把 EXE 和随附的 `GEO-Content-Platform.sha256.txt` 放在普通用户可读目录即可，不需要管理员安装。当前 Beta 未做 Authenticode 代码签名，Windows 可能显示“未知发布者”或 SmartScreen 提示。只有在来源可信且 SHA-256 与交付清单完全一致时才继续；企业策略禁止未签名软件时，应改用后续签名构建。

### 升级

1. 退出工作区并关闭窗口，确认进程已经结束。
2. 点击“立即备份”，并在关闭后复制整个本机数据目录。
3. 校验新 EXE 的 SHA-256 和版本说明。
4. 用新 EXE 替换旧程序文件；不要覆盖 `%LOCALAPPDATA%\GEOContentPlatform`。
5. 启动新版本，检查登录、项目、可见度历史和健康状态。

数据库建表使用兼容式 `CREATE TABLE IF NOT EXISTS`，不会在正常升级中清空既有表；但这不替代升级前备份。

### 回滚

关闭当前版本后，换回已经验收且哈希已固定的旧 EXE，再用同一数据目录启动。AI 可见度历史表是附加表，旧版本即使不读取它也不会自动删除数据。当前没有名为 `AI_VISIBILITY_ENABLED` 的运行时开关，不能把不存在的环境变量当作回滚手段。

### 卸载

关闭软件后删除 EXE 即可移除程序，本机数据仍会保留。只有在确认备份有效且确实不再需要项目、素材、账号会话和历史后，才删除 `%LOCALAPPDATA%\GEOContentPlatform`；删除该目录不可由软件恢复。

## 构建与验收

在 Windows PowerShell 的仓库根目录执行：

```powershell
.\scripts\build_windows_desktop.ps1
```

构建流程会同步锁定的 Python 桌面依赖、执行 `npm ci`、构建 Vue、运行脚本中列出的桌面/认证/可见度/发布闸门/健康回归和前端测试，并在 GUID staging 目录打包单文件 EXE。成品随后从独立临时工作目录完成两次验收：HTTP 健康与首次认证状态，以及隐藏 Edge WebView2 中的首次设密、创建合成项目、本地可见度分析、历史留存和原生备份。全部通过后，staging 才会整体提升为不可覆盖的版本目录。

产物目录形如：

- `release\windows\GEO-Content-Platform-1.0.0-<UTC时间>-<提交短SHA>\GEO-Content-Platform.exe`
- 同目录的 ZIP、`GEO-Content-Platform.sha256.txt`、`GEO-Content-Platform.manifest.json`、`build-info.json`、`首次使用.md`、`AI可见度接口契约.md`、`Windows桌面技术说明.md` 和 `LICENSE.txt`

只有在同一提交上已经独立跑完回归时，才可显式使用 `-SkipTests`；manifest 会如实记录该状态。该参数仍不会跳过两个成品 EXE 冒烟测试。默认拒绝脏工作树，构建成功也不等于完成代码签名；签名状态会写入 manifest，外部商业交付仍必须单独完成签名与许可门禁。

## 故障定位

支持人员可以在隔离目录中运行成品健康检查，不触碰用户数据库：

```powershell
$geoCheckDir = Join-Path $env:TEMP "geo-desktop-health"
.\GEO-Content-Platform.exe --health-check --data-dir $geoCheckDir --health-report (Join-Path $geoCheckDir "health.json")
Get-Content -Raw (Join-Path $geoCheckDir "health.json")
```

优先排查：

- 提示“已在运行”：切换到现有窗口；不要反复启动相同数据目录。
- 空白窗口或 WebView 初始化失败：确认 Microsoft Edge WebView2 Runtime 可用。
- 启动失败：查看 `%LOCALAPPDATA%\GEOContentPlatform\logs\desktop.log`。
- 无法登录：确认键盘布局、大小写和首尾空格；连续失败可能触发临时频率限制。
- “本地认证配置不可用”：保留 `config\auth.json` 和整个数据目录的副本，不要直接编辑 JSON；交给维护人员核验。
- 可见度没有命中品牌：核对项目主名、产品名、别名和回答原文；短词、模糊命中与歧义会降低置信度或要求人工复核。
- 引用数量为零：当前引擎只识别回答原文中显式出现的 `http://`、`https://` 或 `www.` 地址，不会联网推断来源。

## 外部引擎待接边界

同事的纯分析算法已经以本机 Python 版本接入 AI 可见度，但外部回答采集器和同事内容生成服务不能据此视为已经上线。内容生成适配层要求版本化 `POST /api/v1/generate`、健康检查、受限内部地址、独立 Token、超时和响应大小限制；在实际地址、Token、镜像摘要、许可和数据发送边界完成验收前，应保持现有引擎设置，不发送真实客户或项目数据。

算法当前标识为 `ai-visibility-python-port-1.1.0`，临时统计标识为 `metrics-ad-hoc-python-1.1.0`。这些结果不能与 provider grounding 运行或历史 TypeScript 指标直接拼接；详细迁移和权利阻塞见 AI 可见度集成说明。
