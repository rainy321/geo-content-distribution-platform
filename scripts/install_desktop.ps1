param([switch]$Launch)
$ErrorActionPreference = 'Stop'
$geoRoot = Split-Path -Parent $PSScriptRoot
$geoBundle = Join-Path $geoRoot 'dist/desktop-onedir/GEO_AI_Workbench'
$geoSource = Join-Path $geoBundle 'GEO_AI_Workbench.exe'
$geoInstall = Join-Path $env:LOCALAPPDATA 'Programs/GEO_AI_Workbench'
$geoExecutable = Join-Path $geoInstall 'GEO_AI_Workbench.exe'
$geoData = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.geo-workbench'
if (-not (Test-Path -LiteralPath $geoSource)) { throw '请先构建桌面程序（onedir）。' }
$geoExisting = @(Get-Process -Name 'GEO_AI_Workbench' -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -ieq [System.IO.Path]::GetFullPath($geoExecutable))
})
if ($geoExisting.Count -gt 0) { throw '请先保存未完成内容并关闭 GEO 软件，再安装更新。已保存的品牌、稿件和报告会保留。' }
New-Item -ItemType Directory -Path $geoInstall -Force | Out-Null
Get-ChildItem -LiteralPath $geoBundle | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $geoInstall -Recurse -Force }
# Import only this user's existing reports/database; the distributable has no customer data.
$geoCheckStarted = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$geoCheck = Start-Process -FilePath $geoExecutable -ArgumentList @('--import-workspace', ('"' + $geoRoot + '"'), '--smoke-test') -WindowStyle Hidden -PassThru
# Wait for the smoke executable itself; Edge background processes can outlive
# its successful exit and keep Start-Process -Wait's process-tree job open.
$geoCheck.WaitForExit()
$geoCheck.Refresh()
if ($geoCheck.ExitCode -ne 0) { throw "桌面程序验证失败，请查看 $geoData/logs/desktop.log" }
$geoVerification = Get-Content -LiteralPath (Join-Path $geoData 'logs/smoke-test.json') -Raw | ConvertFrom-Json
if (-not $geoVerification.passed) { throw '桌面程序未通过实际窗口验证。' }
if ($geoVerification.process_id -ne $geoCheck.Id -or $geoVerification.started_at_unix -lt ($geoCheckStarted - 1)) {
    throw '本次安装没有取得当前程序的新验证记录，未使用旧版记录代替。请关闭软件后重试安装。'
}
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
