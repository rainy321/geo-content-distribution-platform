param([switch]$Launch)
$ErrorActionPreference = 'Stop'
$geoRoot = Split-Path -Parent $PSScriptRoot
$geoBundle = Join-Path $geoRoot 'dist/desktop-onedir/GEO_AI_Workbench'
$geoSource = Join-Path $geoBundle 'GEO_AI_Workbench.exe'
$geoInstall = Join-Path $env:LOCALAPPDATA 'Programs/GEO_AI_Workbench'
$geoExecutable = Join-Path $geoInstall 'GEO_AI_Workbench.exe'
$geoData = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.geo-workbench'
if (-not (Test-Path -LiteralPath $geoSource)) { throw '请先构建桌面程序（onedir）。' }
New-Item -ItemType Directory -Path $geoInstall -Force | Out-Null
Get-ChildItem -LiteralPath $geoBundle | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $geoInstall -Recurse -Force }
# Import only this user's existing reports/database; the distributable has no customer data.
$geoCheck = Start-Process -FilePath $geoExecutable -ArgumentList @('--import-workspace', ('"' + $geoRoot + '"'), '--smoke-test') -WindowStyle Hidden -PassThru -Wait
if ($geoCheck.ExitCode -ne 0) { throw "桌面程序验证失败，请查看 $geoData/logs/desktop.log" }
$geoVerification = Get-Content -LiteralPath (Join-Path $geoData 'logs/smoke-test.json') -Raw | ConvertFrom-Json
if (-not $geoVerification.passed) { throw '桌面程序未通过实际窗口验证。' }
$geoShell = New-Object -ComObject WScript.Shell
$geoDesktop = [Environment]::GetFolderPath('Desktop')
$geoPrograms = [Environment]::GetFolderPath('Programs')
foreach ($geoShortcutPath in @((Join-Path $geoDesktop 'GEO AI搜索工作台.lnk'), (Join-Path $geoPrograms 'GEO AI搜索工作台.lnk'))) {
    $geoShortcut = $geoShell.CreateShortcut($geoShortcutPath)
    $geoShortcut.TargetPath = $geoExecutable
    $geoShortcut.WorkingDirectory = $geoInstall
    $geoShortcut.IconLocation = $geoExecutable + ',0'
    $geoShortcut.Description = '客户 AI 搜索自动测试与来源报告'
    $geoShortcut.Save()
}
Write-Output "软件：$geoExecutable"
Write-Output "桌面快捷方式：$(Join-Path $geoDesktop 'GEO AI搜索工作台.lnk')"
if ($Launch) { Start-Process -FilePath $geoExecutable -WindowStyle Normal }
