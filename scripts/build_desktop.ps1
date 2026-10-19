param(
    [ValidateSet('onedir', 'onefile')][string]$Mode = 'onedir'
)
$ErrorActionPreference = 'Stop'
$geoRoot = Split-Path -Parent $PSScriptRoot
$geoPython = Join-Path $geoRoot '.tmp/desktop-build-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $geoPython)) {
    throw 'Create .tmp/desktop-build-venv and install .[desktop], playwright==1.52.0 and pyinstaller==6.22.3 first.'
}
$geoDestination = Join-Path $geoRoot "dist/desktop-$Mode"
$geoBuild = Join-Path $geoRoot "build/desktop-$Mode"
$geoEntry = Join-Path $geoRoot 'desktop_app.py'
$geoFrontend = Join-Path $geoRoot 'sau_frontend/dist'
$geoIcon = Join-Path $geoRoot 'desktop/app.ico'
$geoArguments = @(
    '-m', 'PyInstaller', $geoEntry, "--$Mode", '--windowed', '--noconfirm',
    '--name', 'GEO_AI_Workbench', '--paths', $geoRoot,
    '--distpath', $geoDestination, '--workpath', $geoBuild, '--specpath', $geoBuild,
    '--add-data', "${geoFrontend}:sau_frontend/dist",
    '--add-data', "${geoIcon}:desktop", '--icon', $geoIcon,
    '--collect-data', 'webview', '--hidden-import', 'webview.platforms.winforms',
    '--hidden-import', 'webview.platforms.edgechromium', '--hidden-import', 'clr',
    '--hidden-import', 'greenlet._greenlet', '--collect-binaries', 'greenlet',
    '--hidden-import', 'playwright.async_api',
    '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
    '--exclude-module', 'PySide2', '--exclude-module', 'PySide6',
    '--exclude-module', 'cefpython3', '--exclude-module', 'pytest'
)
& $geoPython @geoArguments
if ($LASTEXITCODE -ne 0) { throw "Desktop build failed: $LASTEXITCODE" }
