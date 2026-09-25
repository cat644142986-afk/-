param(
    [string]$PortableDir = "",
    [string]$ExpectedGitCommit = "",
    [string]$EvidencePath = "",
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PortableDir) {
    $PortableDir = Join-Path $ProjectRoot "build\portable-candidate-current"
}
$PortableDir = [System.IO.Path]::GetFullPath($PortableDir)
if (-not $EvidencePath) {
    $EvidencePath = Join-Path $ProjectRoot "build\growth-system-packaged-evidence.json"
}
$EvidencePath = [System.IO.Path]::GetFullPath($EvidencePath)
$AppExe = Join-Path $PortableDir "Product Atelier.exe"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    $AppExe = Join-Path $PortableDir "product-atelier.exe"
}
$SidecarExe = Join-Path $PortableDir "python-server\python-server.exe"
$ManifestPath = Join-Path $PortableDir "python-server\sidecar-manifest.json"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) { throw "Portable app is missing: $AppExe" }
if (-not (Test-Path -LiteralPath $SidecarExe -PathType Leaf)) { throw "Portable sidecar is missing: $SidecarExe" }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Portable manifest is missing: $ManifestPath" }

if (-not $ExpectedGitCommit) {
    $ExpectedGitCommit = ((& git.exe -C $ProjectRoot rev-parse --verify HEAD) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve Git HEAD" }
}
if ($ExpectedGitCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "ExpectedGitCommit must be a full Git commit"
}
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if (-not [string]::Equals(
    ([string]$manifest.git_commit).Trim(),
    $ExpectedGitCommit,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Packaged sidecar manifest does not match ExpectedGitCommit"
}
$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$runName = "ProductAtelier-app-test-growth-" + [guid]::NewGuid().ToString("N")
$runRoot = [System.IO.Path]::GetFullPath((Join-Path $tempBase $runName))
if (
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath((Split-Path -Parent $runRoot)),
        $tempBase.TrimEnd([System.IO.Path]::DirectorySeparatorChar),
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    -not ([System.IO.Path]::GetFileName($runRoot)).StartsWith("ProductAtelier-app-test-growth-", [System.StringComparison]::Ordinal)
) {
    throw "Invalid isolated Growth acceptance path"
}
$dataDir = $runRoot
$knowledgeRoot = Join-Path $runRoot "no-knowledge-vault"
$webviewData = Join-Path $runRoot "webview2-user-data"
$legacySentinel = Join-Path $runRoot "no-legacy-config.json"
New-Item -ItemType Directory -Path $dataDir,$knowledgeRoot,$webviewData | Out-Null

$seedJson = (& python.exe (Join-Path $PSScriptRoot "seed_growth_system_fixture.py") `
    --data-dir $dataDir --knowledge-root $knowledgeRoot) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "Could not seed isolated Growth fixture" }
$seed = $seedJson | ConvertFrom-Json

function Test-ExpectedSidecar($Process, [int]$ParentId) {
    if (-not $Process -or [int]$Process.ParentProcessId -ne $ParentId) { return $false }
    if (-not $Process.ExecutablePath) { return $false }
    return [string]::Equals(
        [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath),
        [System.IO.Path]::GetFullPath($SidecarExe),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

$previous = @{
    data = $env:PRODUCT_ATELIER_DATA_DIR
    webview = $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR
    legacy = $env:PRODUCT_ATELIER_LEGACY_CONFIG
    knowledge = $env:PRODUCT_ATELIER_KNOWLEDGE_BASE
    isolation = $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION
    webviewUser = $env:WEBVIEW2_USER_DATA_FOLDER
    webviewArguments = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
}
$app = $null
$trackedSidecarPids = @()
try {
    $env:PRODUCT_ATELIER_DATA_DIR = $dataDir
    $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR = $webviewData
    $env:PRODUCT_ATELIER_LEGACY_CONFIG = $legacySentinel
    $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $knowledgeRoot
    $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION = "1"
    $env:WEBVIEW2_USER_DATA_FOLDER = $webviewData
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $null
    $app = Start-Process -FilePath $AppExe -WorkingDirectory $PortableDir -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $sidecar = $null
    $health = $null
    while ((Get-Date) -lt $deadline) {
        $app.Refresh()
        if ($app.HasExited) { throw "Packaged app exited early with code $($app.ExitCode)" }
        $matches = @(
            Get-CimInstance Win32_Process -Filter "Name='python-server.exe'" |
                Where-Object { Test-ExpectedSidecar $_ ([int]$app.Id) }
        )
        if ($matches.Count -eq 1) {
            $sidecar = $matches[0]
            $trackedSidecarPids += [int]$sidecar.ProcessId
            $portMatch = [regex]::Match([string]$sidecar.CommandLine, '(\d+)\s*$')
            if ($portMatch.Success) {
                $port = [int]$portMatch.Groups[1].Value
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3
                    break
                } catch { }
            }
        }
        Start-Sleep -Milliseconds 350
    }
    if (-not $health) { throw "Packaged Growth sidecar did not become healthy" }
    if (-not [string]::Equals(
        ([string]$health.service.git_commit).Trim(),
        $ExpectedGitCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "Running sidecar identity does not match the candidate" }

    $before = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/ledger/status" -TimeoutSec 5
    $growthStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/growth/status" -TimeoutSec 10
    if (-not $growthStatus.knowledge.read_only) { throw "Obsidian index is not read-only" }
    if ([int]$growthStatus.knowledge.executable_count -ne 1) {
        throw "Expected exactly one approved executable Obsidian fixture"
    }
    if ([int]$growthStatus.cases.reusable_count -ne 1) {
        throw "Expected exactly one reusable ledger Case"
    }

    $payload = @{
        mode = "single"
        source_asset_ids = @([string]$seed.current_source_asset_id)
        objective = "生成山茶饮料电商主图"
        user_request = "保持包装文字与 Logo，画面干净克制"
        product_name = "山茶饮料"
        project_name = "PA Tea Launch"
        brand_profile = "PA Tea"
        category = "food"
        output_kind = "ecommerce-main"
        output_spec = @{ ratio = "1:1"; resolution = "2k" }
        intent_locks = @{ packaging_text = $true; logo = $true }
        model = "gpt-image-2"
        prompt_version = "prompt_v1"
        generation_strategy = "single_pass"
    } | ConvertTo-Json -Depth 12
    $preview = Invoke-RestMethod -Method Post `
        -Uri "http://127.0.0.1:$port/api/knowledge/compile" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) `
        -TimeoutSec 20

    $caseSource = @($preview.sources | Where-Object { ([string]$_.id).StartsWith("case:") })
    $obsidianSource = @($preview.sources | Where-Object { ([string]$_.id).StartsWith("obsidian:") })
    $sourcePaths = @($preview.sources | ForEach-Object { [string]$_.relative_path }) -join ", "
    if ($caseSource.Count -ne 1) {
        throw "Relevant Case was not compiled into packaged preview (sources: $sourcePaths)"
    }
    if ($obsidianSource.Count -ne 1) {
        throw "Relevant approved Obsidian page was not compiled into packaged preview (sources: $sourcePaths)"
    }
    $compiledText = @(
        $preview.positive_rules | ForEach-Object { [string]$_.text }
        $preview.negative_rules | ForEach-Object { [string]$_.text }
    ) -join "`n"
    if ($compiledText -notmatch "PAGROWTHCASEMARKER") {
        throw "Historical Case did not change the packaged Governor rules"
    }
    if ($compiledText -notmatch "PAGROWTHKNOWLEDGEMARKER") {
        $positiveEvidence = @($preview.positive_rules | ForEach-Object {
            "$( [string]$_.id )=$( [string]$_.text )"
        }) -join " | "
        throw "Approved Obsidian knowledge did not change the packaged Governor rules ($positiveEvidence)"
    }
    if ([string]$preview.growth_snapshot.status -cne "applied") {
        throw "Growth snapshot was not frozen as applied"
    }
    $case = @($preview.growth_snapshot.cases)[0]
    if ([string]$case.feedback_id -cne [string]$seed.feedback_id) {
        throw "Growth snapshot points to another feedback record"
    }
    if ([string]$case.result_asset_id -cne [string]$seed.prior_result_asset_id) {
        throw "Growth snapshot points to another Result"
    }
    if (-not [bool]$case.applied) { throw "Retrieved Case was not applied" }

    $approvedSourceIds = @($preview.execution_context.approved_context.sources | ForEach-Object { [string]$_.id })
    if ($approvedSourceIds -notcontains [string]$caseSource[0].id) {
        throw "Case provenance is missing from execution-context"
    }
    if ($approvedSourceIds -notcontains [string]$obsidianSource[0].id) {
        throw "Obsidian provenance is missing from execution-context"
    }
    $after = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/ledger/status" -TimeoutSec 5
    foreach ($field in @("jobs", "job_items", "task_attempts", "paid_call_authorizations", "provider_call_receipts")) {
        if ([int]$after.counts.$field -ne [int]$before.counts.$field) {
            throw "Packaged read-only Growth Gate mutated $field"
        }
    }

    $receipt = [ordered]@{
        format_version = 1
        kind = "product-atelier-growth-system-packaged-evidence"
        status = "passed"
        git_commit = $ExpectedGitCommit.ToLowerInvariant()
        contract_version = [string]$health.service.contract_version
        schema_version = [int]$health.ledger.schema_version
        app_sha256 = (Get-FileHash -LiteralPath $AppExe -Algorithm SHA256).Hash
        sidecar_sha256 = (Get-FileHash -LiteralPath $SidecarExe -Algorithm SHA256).Hash
        knowledge_index = [ordered]@{
            contract_version = [string]$growthStatus.knowledge.contract_version
            snapshot_sha256 = [string]$growthStatus.knowledge.snapshot_sha256
            executable_count = [int]$growthStatus.knowledge.executable_count
            source_id = [string]$obsidianSource[0].id
        }
        case_projection = [ordered]@{
            contract_version = [string]$growthStatus.cases.contract_version
            feedback_id = [string]$case.feedback_id
            prior_result_asset_id = [string]$case.result_asset_id
            applied = [bool]$case.applied
            source_id = [string]$caseSource[0].id
        }
        execution_context_sha256 = [string]$preview.execution_context.context_sha256
        provider_calls = 0
        task_delta = 0
        validated_at_utc = [DateTime]::UtcNow.ToString("o")
    }
    $evidenceParent = Split-Path -Parent $EvidencePath
    if (-not (Test-Path -LiteralPath $evidenceParent)) {
        New-Item -ItemType Directory -Path $evidenceParent | Out-Null
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8
    Write-Host "Growth System packaged Gate passed." -ForegroundColor Green
    Write-Host "Evidence: $EvidencePath"
    Write-Host "Execution context: $($receipt.execution_context_sha256)"
    Write-Host "Provider calls: 0"
} finally {
    $env:PRODUCT_ATELIER_DATA_DIR = $previous.data
    $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR = $previous.webview
    $env:PRODUCT_ATELIER_LEGACY_CONFIG = $previous.legacy
    $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $previous.knowledge
    $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION = $previous.isolation
    $env:WEBVIEW2_USER_DATA_FOLDER = $previous.webviewUser
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $previous.webviewArguments
    if ($app -and -not $app.HasExited) {
        [void]$app.CloseMainWindow()
        try { Wait-Process -Id $app.Id -Timeout 6 -ErrorAction Stop }
        catch { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
    }
    foreach ($pidValue in ($trackedSidecarPids | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
        if ($process -and $app -and (Test-ExpectedSidecar $process ([int]$app.Id))) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $runRoot) {
        $resolvedRun = [System.IO.Path]::GetFullPath($runRoot)
        if (
            -not [string]::Equals(
                [System.IO.Path]::GetFullPath((Split-Path -Parent $resolvedRun)),
                $tempBase.TrimEnd([System.IO.Path]::DirectorySeparatorChar),
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([System.IO.Path]::GetFileName($resolvedRun)).StartsWith("ProductAtelier-app-test-growth-", [System.StringComparison]::Ordinal)
        ) {
            throw "Refusing to clean a path outside the isolated Growth acceptance root"
        }
        Remove-Item -LiteralPath $resolvedRun -Recurse -Force
    }
}
