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
$geoVite = Join-Path $geoRoot 'sau_frontend/node_modules/vite/bin/vite.js'
if (-not (Test-Path -LiteralPath $geoVite)) { throw 'Install frontend dependencies before building the desktop app.' }
$geoPriorApiBase = $env:VITE_API_BASE_URL
$geoPriorMock = $env:VITE_USE_MOCK
try {
    # A desktop bundle calls its own local Flask server, with no web proxy prefix.
    $env:VITE_API_BASE_URL = '/'
    $env:VITE_USE_MOCK = 'false'
    Push-Location (Join-Path $geoRoot 'sau_frontend')
    try {
        & node $geoVite build
        if ($LASTEXITCODE -ne 0) { throw "Desktop frontend build failed: $LASTEXITCODE" }
    } finally { Pop-Location }
} finally {
    $env:VITE_API_BASE_URL = $geoPriorApiBase
    $env:VITE_USE_MOCK = $geoPriorMock
}
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
