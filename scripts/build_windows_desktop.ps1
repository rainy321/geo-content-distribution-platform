param(
    [switch]$SkipTests,
    [switch]$AllowDirtySource
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendRoot = Join-Path $projectRoot "sau_frontend"
$releaseBaseRoot = Join-Path $projectRoot "release\windows"
$buildRoot = Join-Path $projectRoot ".build\pyinstaller"
$specPath = Join-Path $projectRoot "desktop\GEO-Desktop.spec"
$buildInfoPath = Join-Path $projectRoot ".build\desktop-build-info.json"
$stagingRoot = $null

Push-Location $projectRoot
try {
    $pyproject = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "pyproject.toml")
    $versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $versionMatch.Success) {
        throw "Unable to read project version from pyproject.toml"
    }
    $version = $versionMatch.Groups[1].Value
    $sourceCommit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sourceCommit) {
        throw "Unable to resolve source commit"
    }
    $sourceBranch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sourceBranch) {
        $sourceBranch = "detached"
    }
    $sourceDirty = @(
        git status --porcelain --untracked-files=all |
            Where-Object { $_ -and -not $_.Contains(".build/") -and -not $_.Contains("release/windows/") }
    ).Count -gt 0
    if ($sourceDirty -and -not $AllowDirtySource) {
        throw "Refusing to build a release from a dirty source tree. Commit the release source or pass -AllowDirtySource explicitly."
    }
    $buildInstant = [DateTime]::UtcNow
    $builtAtUtc = $buildInstant.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $releaseId = (
        "GEO-Content-Platform-{0}-{1}-{2}" -f `
            $version,
            $buildInstant.ToString("yyyyMMddTHHmmssZ"),
            $sourceCommit.Substring(0, 8)
    )
    $stagingRoot = Join-Path $projectRoot (".build\release-staging-" + [guid]::NewGuid().ToString("N"))
    $exePath = Join-Path $stagingRoot "GEO-Content-Platform.exe"
    $manifestPath = Join-Path $stagingRoot "GEO-Content-Platform.manifest.json"
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    $buildInfo = [ordered]@{
        schema_version = 1
        version = $version
        source_commit = $sourceCommit
        source_branch = $sourceBranch
        built_at_utc = $builtAtUtc
        source_dirty = $sourceDirty
    }
    $buildInfoDirectory = Split-Path -Parent $buildInfoPath
    New-Item -ItemType Directory -Path $buildInfoDirectory -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $buildInfoPath,
        (($buildInfo | ConvertTo-Json -Depth 4) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "[1/7] Installing locked Python desktop dependencies"
    uv sync --extra desktop --frozen

    Write-Host "[2/7] Building the Vue desktop interface"
    Push-Location $frontendRoot
    try {
        npm ci
        npm run build
    }
    finally {
        Pop-Location
    }

    if (-not $SkipTests) {
        Write-Host "[3/7] Running desktop product regression tests"
        uv run python -m unittest `
            tests.test_geo_desktop `
            tests.test_local_auth_service `
            tests.test_access_control `
            tests.test_desktop_backup_service `
            tests.test_ai_visibility_service `
            tests.test_ai_visibility_api `
            tests.test_ai_visibility_run_service `
            tests.test_real_publish_api `
            tests.test_publish_jobs_api `
            tests.test_material_api `
            tests.test_dashboard_service `
            tests.test_dashboard_api `
            tests.test_projects_api `
            tests.test_database_init `
            tests.test_desktop_product_flow `
            tests.test_frontend_delivery `
            tests.test_health_api `
            tests.test_sau_web `
            -v
        Push-Location $frontendRoot
        try {
            node --test tests/*.test.js
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host "[3/7] Tests skipped by explicit build option"
    }

    Write-Host "[4/7] Packaging one-file Windows executable"
    uv run pyinstaller --noconfirm --clean --distpath $stagingRoot --workpath $buildRoot $specPath

    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "PyInstaller did not create $exePath"
    }
    $peVersion = (Get-Item -LiteralPath $exePath).VersionInfo
    if (
        $peVersion.FileVersion -notlike "$version*" -or
        $peVersion.ProductVersion -notlike "$version*"
    ) {
        throw "Packaged PE version does not match project version $version"
    }

    Write-Host "[5/7] Running packaged executable health and auth smoke test"
    $smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("geo-desktop-smoke-" + [guid]::NewGuid().ToString("N"))
    $reportPath = Join-Path $smokeRoot "health.json"
    New-Item -ItemType Directory -Path $smokeRoot | Out-Null
    try {
        $process = Start-Process -FilePath $exePath `
            -ArgumentList @(
                "--health-check",
                "--data-dir", ('"{0}"' -f $smokeRoot),
                "--health-report", ('"{0}"' -f $reportPath)
            ) `
            -WorkingDirectory $smokeRoot -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(60000)) {
            try {
                $process.Kill($true)
                $process.WaitForExit(10000) | Out-Null
            }
            catch {
                Write-Warning "Unable to terminate timed-out packaged smoke process: $($_.Exception.Message)"
            }
            throw "Packaged health check exceeded the 60 second timeout"
        }
        if ($process.ExitCode -ne 0) {
            throw "Packaged health check failed with exit code $($process.ExitCode)"
        }
        $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
        if (
            -not $report.ok -or
            -not $report.frontend -or
            $report.health.status -ne "ok" -or
            $report.health.real_publishing_enabled -ne $false -or
            $report.health.bilibili_runtime_enabled -ne $false -or
            -not $report.auth.required -or
            -not $report.auth.setup_required -or
            $report.auth.mode -ne "desktop_local" -or
            $report.build.version -ne $version -or
            $report.build.source_commit -ne $sourceCommit
        ) {
            throw "Packaged health report did not pass"
        }
    }
    finally {
        $resolvedSmokeRoot = [System.IO.Path]::GetFullPath($smokeRoot)
        $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolvedSmokeRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "[6/7] Running packaged hidden WebView2 product flow"
    $uiSmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("geo-desktop-ui-smoke-" + [guid]::NewGuid().ToString("N"))
    $uiReportPath = Join-Path $uiSmokeRoot "ui-health.json"
    New-Item -ItemType Directory -Path $uiSmokeRoot | Out-Null
    try {
        $uiProcess = Start-Process -FilePath $exePath `
            -ArgumentList @(
                "--ui-smoke",
                "--data-dir", ('"{0}"' -f $uiSmokeRoot),
                "--health-report", ('"{0}"' -f $uiReportPath)
            ) `
            -WorkingDirectory $uiSmokeRoot -PassThru -WindowStyle Hidden
        if (-not $uiProcess.WaitForExit(60000)) {
            try {
                $uiProcess.Kill($true)
                $uiProcess.WaitForExit(10000) | Out-Null
            }
            catch {
                Write-Warning "Unable to terminate timed-out UI smoke process: $($_.Exception.Message)"
            }
            throw "Packaged native UI smoke exceeded the 60 second timeout"
        }
        if ($uiProcess.ExitCode -ne 0) {
            throw "Packaged native UI smoke failed with exit code $($uiProcess.ExitCode)"
        }
        $uiReport = Get-Content -Raw -LiteralPath $uiReportPath | ConvertFrom-Json
        if (
            -not $uiReport.ok -or
            $uiReport.ui.native_gui -ne "edgechromium" -or
            $uiReport.ui.first_run_setup -ne "passed" -or
            $uiReport.ui.route -ne "#/ai-visibility" -or
            -not $uiReport.ui.history_persisted -or
            $uiReport.ui.backup_integrity -ne "ok" -or
            -not $uiReport.ui.synthetic_data_only -or
            $uiReport.ui.external_model_used -ne $false -or
            $uiReport.ui.real_publishing_attempted -ne $false
        ) {
            throw "Packaged native UI report did not pass"
        }
    }
    finally {
        $resolvedUiSmokeRoot = [System.IO.Path]::GetFullPath($uiSmokeRoot)
        $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolvedUiSmokeRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedUiSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "[7/7] Writing verified release bundle"
    $hash = Get-FileHash -LiteralPath $exePath -Algorithm SHA256
    $size = (Get-Item -LiteralPath $exePath).Length
    $manifest = "SHA256  $($hash.Hash)`r`nBytes   $size`r`nFile    GEO-Content-Platform.exe`r`n"
    [System.IO.File]::WriteAllText(
        (Join-Path $stagingRoot "GEO-Content-Platform.sha256.txt"),
        $manifest,
        [System.Text.UTF8Encoding]::new($false)
    )

    $signature = Get-AuthenticodeSignature -LiteralPath $exePath
    $releaseManifest = [ordered]@{
        schema_version = 1
        product = "GEO Content Platform"
        version = $version
        distribution_scope = "internal_controlled_beta"
        source = [ordered]@{
            commit = $sourceCommit
            branch = $sourceBranch
            dirty = $sourceDirty
        }
        tests_skipped = [bool]$SkipTests
        built_at_utc = $builtAtUtc
        executable = [ordered]@{
            file = "GEO-Content-Platform.exe"
            bytes = $size
            sha256 = $hash.Hash.ToLowerInvariant()
            authenticode_status = [string]$signature.Status
        }
        safety = [ordered]@{
            real_publishing_enabled = [bool]$report.health.real_publishing_enabled
            bilibili_runtime_enabled = [bool]$report.health.bilibili_runtime_enabled
            external_model_called_during_tests = $false
            customer_data_used_during_tests = $false
        }
        algorithm = [ordered]@{
            integration_version = "ai-visibility-python-port-1.1.0"
            handoff_archive_sha256 = "2ff20604dad3b68807c47d0f3aef3d59ce613bf927dcde0b90ad80bc4b96e96c"
            source_rights_status = "pending_written_authorization"
            directly_comparable_to_provider_grounding = $false
        }
        packaged_smoke = [ordered]@{
            health = "ok"
            database = [string]$report.health.database
            auth_mode = [string]$report.auth.mode
            first_run_setup_required = [bool]$report.auth.setup_required
            native_gui = [string]$uiReport.ui.native_gui
            synthetic_product_flow = [bool]$uiReport.ui.ok
            history_persisted = [bool]$uiReport.ui.history_persisted
            backup_integrity = [string]$uiReport.ui.backup_integrity
        }
    }
    [System.IO.File]::WriteAllText(
        $manifestPath,
        (($releaseManifest | ConvertTo-Json -Depth 8) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )

    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\首次使用.md") `
        -Destination (Join-Path $stagingRoot "首次使用.md") -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\ai-visibility-integration.md") `
        -Destination (Join-Path $stagingRoot "AI可见度接口契约.md") -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\windows-desktop.md") `
        -Destination (Join-Path $stagingRoot "Windows桌面技术说明.md") -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") `
        -Destination (Join-Path $stagingRoot "LICENSE.txt") -Force
    Copy-Item -LiteralPath $buildInfoPath `
        -Destination (Join-Path $stagingRoot "build-info.json") -Force

    $zipName = "GEO-Content-Platform-" + $version + "-windows-x64.zip"
    $zipPath = Join-Path $stagingRoot $zipName
    Compress-Archive -LiteralPath @(
        $exePath,
        (Join-Path $stagingRoot "GEO-Content-Platform.sha256.txt"),
        $manifestPath,
        (Join-Path $stagingRoot "首次使用.md"),
        (Join-Path $stagingRoot "AI可见度接口契约.md"),
        (Join-Path $stagingRoot "Windows桌面技术说明.md"),
        (Join-Path $stagingRoot "LICENSE.txt"),
        (Join-Path $stagingRoot "build-info.json")
    ) -DestinationPath $zipPath -CompressionLevel Optimal -Force

    New-Item -ItemType Directory -Path $releaseBaseRoot -Force | Out-Null
    $finalReleaseRoot = Join-Path $releaseBaseRoot $releaseId
    if (Test-Path -LiteralPath $finalReleaseRoot) {
        throw "Refusing to overwrite existing immutable release: $finalReleaseRoot"
    }
    Move-Item -LiteralPath $stagingRoot -Destination $finalReleaseRoot
    $stagingRoot = $null
    $finalExePath = Join-Path $finalReleaseRoot "GEO-Content-Platform.exe"
    $finalZipPath = Join-Path $finalReleaseRoot $zipName

    Write-Host "Build complete: $finalExePath"
    Write-Host "SHA256: $($hash.Hash)"
    Write-Host "Bundle: $finalZipPath"
}
finally {
    if ($stagingRoot -and (Test-Path -LiteralPath $stagingRoot)) {
        $resolvedStagingRoot = [System.IO.Path]::GetFullPath($stagingRoot)
        $resolvedBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot ".build"))
        if ($resolvedStagingRoot.StartsWith(
            $resolvedBuildRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            Remove-Item -LiteralPath $resolvedStagingRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Pop-Location
}
