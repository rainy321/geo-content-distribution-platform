# GEO Windows 桌面版源码交付说明

## 交付目标

源码包是当前 Git 提交的可复现快照，用于代码审计、继续开发和重新构建 Windows EXE。它不是本机业务数据备份，也不是把整个工作目录直接压缩后的副本。

当前产品交付以 Windows 桌面软件为主。`sau_frontend/` 仍然是 EXE 内嵌界面的源代码，不能因为历史公网 Web 已停用而删除；`docs/legacy-web.md`、`sau_web.py` 和部署样例仅用于兼容与历史说明，不代表需要重新部署网站。

## 源码包包含

- Python 后端、桌面宿主、服务层、发布适配器和平台 uploader；
- Vue 桌面界面源代码与 `package-lock.json`；
- `pyproject.toml`、`uv.lock` 和依赖示例配置；
- 测试、构建脚本、桌面资源、必要示例与项目文档；
- `LICENSE`、第三方声明和当前已跟踪的验收参考材料；
- 自动生成的 `SOURCE-MANIFEST.json` 与 `SOURCE-FILES.sha256`。

## 明确不包含

- `.git/` 历史、`.venv/`、`node_modules/`、`__pycache__/`；
- `.build/`、`release/`、`output/` 和前端 `dist/`；
- `%LOCALAPPDATA%\GEOContentPlatform` 下的生产数据库、账号会话、Cookie、素材、日志、备份、WebView 状态和受 Windows DPAPI 保护的 AI 配置；
- 仓库中的未跟踪文件、真实 `.env`、数据库文件和任何运行时密钥。

源码打包只读取 `git archive HEAD`，因此本机目录即使存在 Cookie、数据库或历史 EXE，也不会进入源码包。打包前还会对快照执行高置信度密钥特征扫描；测试中的合成占位凭据不会被误当作真实配置。

## 生成源码包

在仓库根目录使用 PowerShell 7：

```powershell
pwsh -NoProfile -File .\scripts\package_source.ps1
```

默认流程会：

1. 拒绝包含未提交源码修改的工作树；
2. 安装锁定依赖并运行完整 Python 与前端测试；
3. 完成前端生产构建；
4. 从当前提交生成干净快照；
5. 扫描高置信度密钥特征；
6. 生成逐文件 SHA-256 清单与源码 manifest；
7. 压缩后在独立临时目录重新解压并逐文件核验；
8. 在临时目录完整组装后，以一次原子目录移动把版本化 ZIP、校验和、manifest 和验收回执提升到 `release/source/`。

只有同一提交已经独立完成回归时，才可显式使用 `-SkipTests`。manifest 和回执会如实标记跳过状态。

## 验证交付包

```powershell
Get-FileHash -Algorithm SHA256 .\GEO-Content-Platform-Source-*.zip
Get-Content .\GEO-Content-Platform-Source-*.sha256.txt
```

ZIP 的 SHA-256 必须与同目录校验文件一致。解压后，`SOURCE-FILES.sha256` 覆盖 Git 快照内的每个文件；`SOURCE-MANIFEST.json` 记录产品版本、提交号、分支、文件数量、依赖锁和测试状态。

## 从源码重建

```powershell
uv sync --extra desktop --extra web --frozen
Push-Location .\sau_frontend
npm ci
npm test
npm run build
Pop-Location
uv run --no-sync python -m unittest discover -s tests -q
```

生成默认安全版 EXE：

```powershell
pwsh -NoProfile -File .\scripts\build_windows_desktop.ps1
```

只有取得真实发布授权并理解账号与平台风险后，才生成内部授权版：

```powershell
pwsh -NoProfile -File .\scripts\build_windows_desktop.ps1 -PublishingProfile authorized_real
```

源码中不包含生产 AI 密钥和媒体账号。首次启动重新配置不会修改源码包；生产数据迁移应在软件退出后单独备份整个 `%LOCALAPPDATA%\GEOContentPlatform`，不能塞回源码目录。

## 权利与签名边界

当前算法交接材料仍标记为 `pending_written_authorization`。源码包保留现有许可证和第三方声明，但这不替代对同事算法、平台自动化和第三方运行时的书面授权审查。重新构建的 EXE 默认也不会自动获得 Authenticode 签名，对外分发前需另行完成代码签名与合规验收。
