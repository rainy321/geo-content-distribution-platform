# Windows 桌面软件

桌面程序名为“GEO AI 搜索工作台”，启动文件为 `GEO_AI_Workbench.exe`。使用独立 WebView2 窗口，包含 Python 后端和已构建的界面。已在本机 Windows、实际原生窗口及打包后的程序中验证；系统 WebView2 与 Edge 浏览器在本机已可用。

软件内可以选择客户资料、通过 AI API 或已登录豆包生成固定问题、启动串行测试、停止任务及查看完整报告。首次使用豆包采集时点击“登录豆包”，在专用窗口正常登录/验证，完成后关闭登录窗口保存会话。选择与你实际账号对应的“豆包对话”或“豆包工作”；模型选项只选择正常可用项目。没有选择品牌项目时，豆包出题可以使用当前报告对应的客户资料。

报告上的“用本报告制定内容任务”和侧栏“采集驱动内容”连接后续选题、写作事实、平台稿、内容库审稿、发布链接和同题复测。生成稿先保存为草稿；写作来源与曝光采集分开记录。详见[采集到发布说明](geo-content-loop.md)。

“用豆包生成问题”完成后，在问题列表检查客户业务与问题，再点击“开始测试”。界面显示任务状态；逐题采集、固定计划、失败排除、模型记录和重复提交保护复用现有执行器。遇到登录和安全验证时在专用窗口正常处理，软件不绕过验证。停止或关闭软件会取消未完成任务，提交状态不明确的运行标记为待检查，不自动重发。

当前采集仍只接豆包网页版。桌面按钮和执行器连接经过合成测试覆盖；采集浏览器引擎经过离线实际启动验证，已保存的公司实测来自 10 月 12 日，不能把本次桌面打包验证算成新一轮客户曝光实测。

## 本机安装与数据

GitHub 下载：[2026-10-16 采集内容迭代版](https://github.com/rainy321/geo-content-distribution-platform/releases/tag/geo-workbench-2026.10.16)。下载 `GEO-AI-Workbench-2026.10.16-windows-x64.zip`，完整解压后打开 `GEO_AI_Workbench/GEO_AI_Workbench.exe`，保留同级 `_internal` 目录。历史版本在[旧版归档 Release](https://github.com/rainy321/geo-content-distribution-platform/releases/tag/geo-legacy-backup-2026.10.15)，仅用于备份恢复。

本机安装文件：`%LOCALAPPDATA%\Programs\GEO_AI_Workbench\GEO_AI_Workbench.exe`。桌面和开始菜单有“GEO AI搜索工作台”快捷方式。

数据与设置保存在 `%USERPROFILE%\.geo-workbench\`，包含数据库、报告、任务记录、当前计划、客户填写内容、正常登录后的专用浏览器会话与日志。软件菜单可以打开数据目录。旧版 `%LOCALAPPDATA%\Tongyuye\GEO_AI_Workbench\` 和启动宿主可能重定向的目录会自动迁入；原文件保留，冲突文件另存 `recovery`，不覆盖新目录中的文件。已有项目数据库与 `runtime/geo-tests` 的报告也可通过本机安装步骤导入。发行包不包含本机数据库、报告、环境文件或登录会话。

“任务记录”显示成功、失败、停止与待检查的任务，可以重新查看计划和报告。旧版只保留在内存中的任务无法完整恢复；能从已有报告、计划和出题证据找回的记录会自动导入。客户填写内容和当前计划自动保存，AI 密钥不写入此缓存。原始回答默认折叠，并标明未逐项核验，回答文字不自动成为已经核实的公司事实。

独立程序只监听本机回环地址。关闭窗口会停止内部服务。再次打开会使用已有数据；同一数据目录只允许一个窗口实例。

## 构建

```powershell
uv venv .tmp/desktop-build-venv --python .venv/Scripts/python.exe
uv pip install --python .tmp/desktop-build-venv/Scripts/python.exe ".[desktop]" playwright==1.52.0 pyinstaller==6.22.3
# 在 sau_frontend 中执行 npm run build。
./scripts/build_desktop.ps1 -Mode onedir
./scripts/install_desktop.ps1 -Launch
```

安装使用 `dist/desktop-onedir/GEO_AI_Workbench/` 整个目录，入口为其中的 `GEO_AI_Workbench.exe`，同级 `_internal` 必须一起保留。采集依赖随程序固定安装，并在打开窗口前加载。可选单文件构建仍支持 `-Mode onefile`，安装脚本使用目录版。打包只收集源代码、库、图标和前端构建；用户数据通过本机安装步骤导入。

技术接口依据：[pywebview API](https://pywebview.flowrl.com/api/)、[pywebview 打包说明](https://pywebview.flowrl.com/guide/freezing)、[PyInstaller 打包模式](https://pyinstaller.org/en/stable/operating-mode.html)。
