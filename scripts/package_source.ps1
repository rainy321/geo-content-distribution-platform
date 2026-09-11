#requires -Version 7.0

param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$releaseBaseRoot = Join-Path $projectRoot "release\source"
$buildBaseRoot = Join-Path $projectRoot ".build"
$stagingRoot = $null

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Operation,
        [Parameter(Mandatory = $true)]
        [int]$ExitCode,
        [string]$Output = ""
    )

    if ($ExitCode -eq 0) {
        return
    }
    $tail = (($Output -split "`r?`n") | Select-Object -Last 40) -join "`n"
    throw "$Operation failed with exit code $ExitCode`n$tail"
}

function Assert-PathWithin {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the expected directory: $resolvedPath"
    }
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

Push-Location $projectRoot
try {
    $pyproject = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "pyproject.toml")
    $versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $versionMatch.Success) {
        throw "Unable to read the project version from pyproject.toml"
    }
    $version = $versionMatch.Groups[1].Value
    $sourceCommit = (git rev-parse HEAD).Trim()
    Assert-NativeSuccess -Operation "Resolving source commit" -ExitCode $LASTEXITCODE
    $sourceBranch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sourceBranch) {
        $sourceBranch = "detached"
    }

    $sourceStatus = @(git status --porcelain --untracked-files=all)
    Assert-NativeSuccess -Operation "Reading Git source status" -ExitCode $LASTEXITCODE
    $dirtyEntries = @(
        $sourceStatus | Where-Object {
            $_ -and
            -not $_.StartsWith("?? output/") -and
            -not $_.StartsWith('?? "output/')
        }
    )
    if ($dirtyEntries.Count -gt 0) {
        throw "Refusing to package uncommitted source. Commit the source changes first."
    }

    $testStatus = if ($SkipTests) { "skipped_by_operator" } else { "passed" }
    if (-not $SkipTests) {
        Write-Host "[1/6] Installing locked dependencies"
        $syncOutput = (& uv sync --extra desktop --extra web --frozen 2>&1) | Out-String
        $syncExit = $LASTEXITCODE
        Assert-NativeSuccess -Operation "Installing locked Python dependencies" -ExitCode $syncExit -Output $syncOutput

        Push-Location (Join-Path $projectRoot "sau_frontend")
        try {
            $npmInstallOutput = (& npm ci 2>&1) | Out-String
            $npmInstallExit = $LASTEXITCODE
            Assert-NativeSuccess -Operation "Installing locked frontend dependencies" -ExitCode $npmInstallExit -Output $npmInstallOutput
        }
        finally {
            Pop-Location
        }

        Write-Host "[2/6] Running backend and frontend regression"
        $backendOutput = (& uv run --no-sync python -m unittest discover -s tests -q 2>&1) | Out-String
        $backendExit = $LASTEXITCODE
        Assert-NativeSuccess -Operation "Backend regression" -ExitCode $backendExit -Output $backendOutput

        Push-Location (Join-Path $projectRoot "sau_frontend")
        try {
            $frontendTestOutput = (& npm test 2>&1) | Out-String
            $frontendTestExit = $LASTEXITCODE
            Assert-NativeSuccess -Operation "Frontend regression" -ExitCode $frontendTestExit -Output $frontendTestOutput
            $frontendBuildOutput = (& npm run build 2>&1) | Out-String
            $frontendBuildExit = $LASTEXITCODE
            Assert-NativeSuccess -Operation "Frontend production build" -ExitCode $frontendBuildExit -Output $frontendBuildOutput
        }
        finally {
            Pop-Location
        }
    }

    $buildInstant = [DateTime]::UtcNow
    $builtAtUtc = $buildInstant.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $releaseId = (
        "GEO-Content-Platform-Source-Internal-{0}-{1}-{2}" -f `
            $version,
            $buildInstant.ToString("yyyyMMddTHHmmssZ"),
            $sourceCommit.Substring(0, 8)
    )
    $stagingRoot = Join-Path $buildBaseRoot ("source-package-" + [guid]::NewGuid().ToString("N"))
    $contentRoot = Join-Path $stagingRoot "content"
    $packageRoot = Join-Path $contentRoot $releaseId
    $snapshotArchive = Join-Path $stagingRoot "tracked-source.zip"
    $stagedZip = Join-Path $stagingRoot ($releaseId + ".zip")
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

    Write-Host "[3/6] Exporting the committed source snapshot"
    & git archive --format=zip "--output=$snapshotArchive" HEAD
    Assert-NativeSuccess -Operation "Exporting committed source" -ExitCode $LASTEXITCODE
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($snapshotArchive, $packageRoot)

    $scannableExtensions = @(
        ".bat", ".css", ".env", ".html", ".ini", ".js", ".json", ".lock",
        ".md", ".ps1", ".py", ".scss", ".sh", ".toml", ".ts", ".txt",
        ".vue", ".yaml", ".yml"
    )
    $secretPatterns = [ordered]@{
        private_key = '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
        openai_style_key = '(?<![A-Za-z0-9])sk-[A-Za-z0-9._-]{32,}'
        github_token = '(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}'
        aws_access_key = '(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])'
        google_api_key = '(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{30,}'
    }
    $scanCount = 0
    foreach ($file in Get-ChildItem -LiteralPath $packageRoot -File -Recurse) {
        $isTextCandidate = (
            $file.Length -le 2MB -and
            ($scannableExtensions -contains $file.Extension.ToLowerInvariant() -or $file.Name.StartsWith(".env"))
        )
        if (-not $isTextCandidate) {
            continue
        }
        $scanCount += 1
        $text = [System.IO.File]::ReadAllText($file.FullName)
        foreach ($entry in $secretPatterns.GetEnumerator()) {
            if ([regex]::IsMatch($text, $entry.Value)) {
                $relative = [System.IO.Path]::GetRelativePath($packageRoot, $file.FullName).Replace("\", "/")
                throw "High-confidence secret signature '$($entry.Key)' found in tracked file '$relative'."
            }
        }
    }

    Write-Host "[4/6] Writing source manifest and file inventory"
    $snapshotFiles = @(Get-ChildItem -LiteralPath $packageRoot -File -Recurse | Sort-Object FullName)
    $inventoryLines = @()
    $snapshotBytes = 0L
    foreach ($file in $snapshotFiles) {
        $relative = [System.IO.Path]::GetRelativePath($packageRoot, $file.FullName).Replace("\", "/")
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        $inventoryLines += "$hash  $relative"
        $snapshotBytes += $file.Length
    }
    $inventoryPath = Join-Path $packageRoot "SOURCE-FILES.sha256"
    Write-Utf8NoBom -Path $inventoryPath -Content (($inventoryLines -join "`n") + "`n")
    $inventoryHash = (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()

    Copy-Item -LiteralPath (Join-Path $packageRoot "docs\source-package.md") -Destination (Join-Path $packageRoot "源码包说明.md")
    $pythonVersion = ((& uv run --no-sync python --version 2>&1) | Out-String).Trim()
    Assert-NativeSuccess -Operation "Reading Python version" -ExitCode $LASTEXITCODE
    $nodeVersion = ((& node --version 2>&1) | Out-String).Trim()
    Assert-NativeSuccess -Operation "Reading Node.js version" -ExitCode $LASTEXITCODE
    $manifest = [ordered]@{
        schema_version = 1
        product = "GEO Content Platform"
        version = $version
        package_id = $releaseId
        package_kind = "internal_committed_source_snapshot"
        distribution_scope = "internal_engineering_handoff"
        built_at_utc = $builtAtUtc
        source = [ordered]@{
            commit = $sourceCommit
            branch = $sourceBranch
            dirty = $false
        }
        snapshot = [ordered]@{
            tracked_only = $true
            file_count = $snapshotFiles.Count
            bytes = $snapshotBytes
            inventory_file = "SOURCE-FILES.sha256"
            inventory_sha256 = $inventoryHash
        }
        verification = [ordered]@{
            tests = $testStatus
            backend_regression = $testStatus
            frontend_regression = $testStatus
            frontend_production_build = $testStatus
            high_confidence_secret_scan = "passed"
            scanned_text_files = $scanCount
        }
        dependency_locks = @("uv.lock", "sau_frontend/package-lock.json")
        build_entrypoints = @(
            "scripts/build_windows_desktop.ps1",
            "scripts/package_source.ps1"
        )
        excluded_local_state = @(
            ".git",
            ".venv",
            "node_modules",
            ".build",
            "release",
            "output",
            "cookies and account sessions",
            "databases, logs, backups and protected AI configuration"
        )
        toolchain = [ordered]@{
            python = $pythonVersion
            node = $nodeVersion
            powershell = $PSVersionTable.PSVersion.ToString()
        }
        source_rights_status = "pending_written_authorization"
    }
    $manifestPath = Join-Path $packageRoot "SOURCE-MANIFEST.json"
    Write-Utf8NoBom -Path $manifestPath -Content (($manifest | ConvertTo-Json -Depth 8) + "`n")

    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $contentRoot,
        $stagedZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    Write-Host "[5/6] Re-extracting and verifying the committed source snapshot"
    $verificationRoot = Join-Path $stagingRoot "verify"
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory($stagedZip, $verificationRoot)
    $verifiedPackageRoot = Join-Path $verificationRoot $releaseId
    if (-not (Test-Path -LiteralPath $verifiedPackageRoot -PathType Container)) {
        throw "The source ZIP does not contain the expected top-level directory."
    }
    $forbiddenSegments = @(
        ".git", ".venv", "node_modules", "__pycache__", ".build", "release",
        "output", "cookies", "cookiesFile", "logs"
    )
    foreach ($file in Get-ChildItem -LiteralPath $verifiedPackageRoot -File -Recurse) {
        $relative = [System.IO.Path]::GetRelativePath($verifiedPackageRoot, $file.FullName).Replace("\", "/")
        $segments = $relative.Split("/")
        if (@($segments | Where-Object { $forbiddenSegments -contains $_ }).Count -gt 0) {
            throw "Forbidden local-state path found in source ZIP: $relative"
        }
        if ($relative -eq ".env" -or $relative.EndsWith("/.env") -or $relative.EndsWith(".db") -or $relative.EndsWith(".sqlite")) {
            throw "Forbidden runtime file found in source ZIP: $relative"
        }
    }
    $verifiedInventoryPath = Join-Path $verifiedPackageRoot "SOURCE-FILES.sha256"
    foreach ($line in Get-Content -LiteralPath $verifiedInventoryPath) {
        if ($line -notmatch '^([0-9A-F]{64})  (.+)$') {
            throw "Invalid source inventory line."
        }
        $expectedHash = $Matches[1]
        $relative = $Matches[2]
        $candidate = Join-Path $verifiedPackageRoot $relative
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Source inventory entry is missing: $relative"
        }
        $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "Source inventory hash mismatch: $relative"
        }
    }
    $verifiedManifestPath = Join-Path $verifiedPackageRoot "SOURCE-MANIFEST.json"
    $verifiedManifest = Get-Content -Raw -LiteralPath $verifiedManifestPath | ConvertFrom-Json
    if ($verifiedManifest.source.commit -ne $sourceCommit) {
        throw "Source manifest commit does not match the packaged Git snapshot."
    }
    if ($verifiedManifest.distribution_scope -ne "internal_engineering_handoff") {
        throw "Source manifest does not preserve the internal distribution boundary."
    }

    Write-Host "[6/6] Publishing the immutable source delivery"
    $releaseDirectory = Join-Path $releaseBaseRoot $releaseId
    if (Test-Path -LiteralPath $releaseDirectory) {
        throw "Source release already exists: $releaseDirectory"
    }
    $stagedReleaseDirectory = Join-Path $stagingRoot "delivery"
    New-Item -ItemType Directory -Path $stagedReleaseDirectory | Out-Null
    $stagedFinalZip = Join-Path $stagedReleaseDirectory ($releaseId + ".zip")
    Copy-Item -LiteralPath $stagedZip -Destination $stagedFinalZip
    $zipHash = (Get-FileHash -LiteralPath $stagedFinalZip -Algorithm SHA256).Hash.ToUpperInvariant()
    $zipBytes = (Get-Item -LiteralPath $stagedFinalZip).Length
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stagedReleaseDirectory "SOURCE-MANIFEST.json")
    Write-Utf8NoBom -Path (Join-Path $stagedReleaseDirectory ($releaseId + ".sha256.txt")) -Content (
        "SHA256  $zipHash`nBytes   $zipBytes`nFile    $($releaseId).zip`n"
    )
    $receipt = [ordered]@{
        schema_version = 1
        package_id = $releaseId
        distribution_scope = "internal_engineering_handoff"
        source_commit = $sourceCommit
        archive_file = $releaseId + ".zip"
        archive_bytes = $zipBytes
        archive_sha256 = $zipHash.ToLowerInvariant()
        archive_extract_verification = "passed"
        file_inventory_verification = "passed"
        forbidden_local_state_check = "passed"
        tests = $testStatus
        verified_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    Write-Utf8NoBom -Path (Join-Path $stagedReleaseDirectory "SOURCE-PACKAGE-RECEIPT.json") -Content (
        ($receipt | ConvertTo-Json -Depth 6) + "`n"
    )
    New-Item -ItemType Directory -Path $releaseBaseRoot -Force | Out-Null
    Assert-PathWithin -Path $releaseDirectory -Parent $releaseBaseRoot
    Move-Item -LiteralPath $stagedReleaseDirectory -Destination $releaseDirectory
    $finalZip = Join-Path $releaseDirectory ($releaseId + ".zip")

    Write-Host "Source package ready: $finalZip"
    Write-Host "SHA256: $zipHash"
}
finally {
    Pop-Location
    if ($stagingRoot -and (Test-Path -LiteralPath $stagingRoot)) {
        Assert-PathWithin -Path $stagingRoot -Parent $buildBaseRoot
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
