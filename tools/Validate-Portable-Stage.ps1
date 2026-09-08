# Product Atelier validated-stage gate.
#
# This entry point never builds and never touches the formal release. It
# re-verifies one already packaged candidate, runs the packaged smoke gates,
# and publishes a hashable validation receipt only after every gate passes.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedGitCommit,
    [string]$CandidateDir = "",
    [string]$ReceiptPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$CanonicalCandidate = [System.IO.Path]::GetFullPath((Join-Path $BuildRoot "portable-candidate-current"))
$PromotionTool = Join-Path $PSScriptRoot "portable_release.py"
$TransactionPath = Join-Path $BuildRoot "portable-promotion-transaction.json"

if (-not $CandidateDir) { $CandidateDir = $CanonicalCandidate }
if (-not $ReceiptPath) { $ReceiptPath = Join-Path $BuildRoot "portable-validated-stage.json" }
$CandidateDir = [System.IO.Path]::GetFullPath($CandidateDir)
$ReceiptPath = [System.IO.Path]::GetFullPath($ReceiptPath)

function Test-SamePath([string]$Left, [string]$Right) {
    return [string]::Equals(
        [System.IO.Path]::GetFullPath($Left),
        [System.IO.Path]::GetFullPath($Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Invoke-JsonCommand([string]$FilePath, [string[]]$ArgumentList) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $FilePath @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Command failed: $FilePath $($ArgumentList -join ' '): $($output -join ' ')"
    }
    try {
        return (($output | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
    } catch {
        throw "Command did not return valid JSON: $FilePath $($ArgumentList -join ' ')"
    }
}

function Write-JsonAtomic([string]$Path, $Payload) {
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
    $token = [guid]::NewGuid().ToString("N")
    $temporary = Join-Path $parent ("." + [System.IO.Path]::GetFileName($Path) + ".tmp-" + $token)
    $backup = Join-Path $parent ("." + [System.IO.Path]::GetFileName($Path) + ".previous-" + $token)
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    $json = ($Payload | ConvertTo-Json -Depth 12) + "`n"
    try {
        [System.IO.File]::WriteAllText($temporary, $json, $utf8WithoutBom)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $Path, $backup, $true)
        } else {
            Move-Item -LiteralPath $temporary -Destination $Path
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backup) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($env:OS -ne "Windows_NT") { throw "Validated-stage smoke requires Windows." }
if (-not (Test-SamePath $CandidateDir $CanonicalCandidate)) {
    throw "Validation only accepts the canonical packaged candidate: $CanonicalCandidate"
}
if (-not (Test-SamePath (Split-Path -Parent $ReceiptPath) $BuildRoot)) {
    throw "Validation receipts must be direct children of: $BuildRoot"
}
if (Test-Path -LiteralPath $TransactionPath) {
    throw "An unfinished promotion exists: $TransactionPath"
}
foreach ($command in @("git.exe", "python.exe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required validation command is unavailable: $command"
    }
}
if (-not (Test-Path -LiteralPath $PromotionTool -PathType Leaf)) {
    throw "Portable release verifier is missing: $PromotionTool"
}

& git.exe -C $ProjectRoot cat-file -e "$ExpectedGitCommit`^{commit}"
if ($LASTEXITCODE -ne 0) { throw "Expected Git commit is unavailable: $ExpectedGitCommit" }

$identityArguments = @(
    $PromotionTool, "verify-identity",
    "--project-root", $ProjectRoot,
    "--candidate-dir", $CandidateDir,
    "--git-commit", $ExpectedGitCommit
)

Write-Host "=== Product Atelier Packaged Stage Validation ===" -ForegroundColor Cyan
Write-Host "[1/5] Verifying packaged candidate identity..." -ForegroundColor Yellow
$identityBefore = Invoke-JsonCommand -FilePath "python.exe" -ArgumentList $identityArguments
$candidateIdentitySha256 = ([string]$identityBefore.identity_receipt.sha256).Trim().ToUpperInvariant()
if ($candidateIdentitySha256 -notmatch '^[0-9A-F]{64}$') {
    throw "Candidate identity verifier returned an invalid SHA-256"
}

Write-Host "[2/5] Running packaged sidecar smoke..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "Test-Portable.ps1") `
    -PortableDir $CandidateDir `
    -ExpectedGitCommit $ExpectedGitCommit

Write-Host "[3/5] Running packaged application smoke..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "Test-Portable-App.ps1") `
    -PortableDir $CandidateDir `
    -ExpectedGitCommit $ExpectedGitCommit

Write-Host "[4/5] Running packaged schema and local-edit gate..." -ForegroundColor Yellow
& python.exe (Join-Path $PSScriptRoot "verify_packaged_schema_upgrade.py") `
    --sidecar-dir (Join-Path $CandidateDir "python-server")
if ($LASTEXITCODE -ne 0) { throw "Packaged schema upgrade and local-edit gate failed" }

Write-Host "[5/5] Re-verifying candidate identity and publishing Validated receipt..." -ForegroundColor Yellow
$identityAfter = Invoke-JsonCommand -FilePath "python.exe" -ArgumentList @(
    $identityArguments + @("--candidate-identity-sha256", $candidateIdentitySha256)
)
if (-not [string]::Equals(
    $candidateIdentitySha256,
    ([string]$identityAfter.identity_receipt.sha256).Trim(),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Candidate identity changed during validation"
}

$receipt = [ordered]@{
    format_version = 1
    kind = "product-atelier-validated-portable-stage"
    status = "validated"
    validated_at_utc = [DateTime]::UtcNow.ToString("o")
    project_root = $ProjectRoot
    candidate_dir = $CandidateDir
    git_commit = $ExpectedGitCommit.ToLowerInvariant()
    candidate_identity_sha256 = $candidateIdentitySha256
    candidate = $identityAfter.candidate
    gates = @(
        [ordered]@{ name = "portable_identity"; status = "passed" },
        [ordered]@{ name = "packaged_sidecar_smoke"; status = "passed" },
        [ordered]@{ name = "packaged_app_smoke"; status = "passed" },
        [ordered]@{ name = "packaged_schema_upgrade"; status = "passed" },
        [ordered]@{ name = "post_smoke_identity"; status = "passed" }
    )
}
Write-JsonAtomic -Path $ReceiptPath -Payload $receipt
$receiptSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReceiptPath).Hash

Write-Host "Validated stage receipt: $ReceiptPath" -ForegroundColor Green
Write-Host "Validated stage receipt SHA-256: $receiptSha256" -ForegroundColor Green
[ordered]@{
    status = "validated"
    git_commit = $ExpectedGitCommit.ToLowerInvariant()
    candidate_dir = $CandidateDir
    candidate_identity_sha256 = $candidateIdentitySha256
    validation_receipt = [ordered]@{
        path = $ReceiptPath
        sha256 = $receiptSha256
    }
} | ConvertTo-Json -Depth 8 -Compress
