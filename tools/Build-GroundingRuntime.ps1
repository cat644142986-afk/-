param(
    [string]$OutputRoot = "",
    [string]$ModelPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location -LiteralPath $ProjectRoot

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "This grounding runtime entry point currently builds the Windows pack only"
}

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $ProjectRoot "build\grounding-runtime-candidate"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$BuildBase = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$token = [Guid]::NewGuid().ToString("N")
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $BuildBase ".grounding-runtime-work-$token"))
$DistRoot = [System.IO.Path]::GetFullPath((Join-Path $BuildBase ".grounding-runtime-dist-$token"))
$Candidate = Join-Path $DistRoot "grounding-runtime"

if ($OutputRoot -eq [System.IO.Path]::GetPathRoot($OutputRoot)) {
    throw "Grounding runtime output cannot be a filesystem root"
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw "Grounding runtime output already exists; choose a new candidate path"
}
if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    throw "python.exe is required"
}
python.exe tools\verify_build_requirements.py
if ($LASTEXITCODE -ne 0) { throw "Pinned PyInstaller build tools are unavailable" }

$dirty = @(& git.exe status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Git worktree" }
if ($dirty.Count -gt 0) {
    throw "Grounding runtime candidates require a clean worktree so source hashes match the Git commit"
}
$commit = (& git.exe rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') {
    throw "Could not resolve a full Git commit"
}

try {
    python.exe -m PyInstaller grounding-runtime.spec --distpath $DistRoot --workpath $BuildRoot
    if ($LASTEXITCODE -ne 0) { throw "Grounding runtime build failed" }
    if (-not (Test-Path -LiteralPath (Join-Path $Candidate "grounding-runtime.exe") -PathType Leaf)) {
        throw "Built grounding runtime entrypoint is missing"
    }

    python.exe tools\build_grounding_runtime_manifest.py `
        --runtime-root $Candidate `
        --project-root $ProjectRoot `
        --git-commit $commit
    if ($LASTEXITCODE -ne 0) { throw "Grounding runtime manifest generation failed" }

    if ($ModelPath) {
        $probe = & (Join-Path $Candidate "grounding-runtime.exe") --probe --model-path ([System.IO.Path]::GetFullPath($ModelPath))
        if ($LASTEXITCODE -ne 0) { throw "Built grounding runtime probe failed" }
        $probeState = $probe | ConvertFrom-Json
        if ($probeState.status -ne "ready") { throw "Built grounding runtime is not ready for the selected model" }
    }

    # Publish the well-known candidate path only after every requested gate has
    # passed. A failed probe remains inside the unique temporary dist root and
    # is removed by the guarded finally block instead of looking releasable.
    Move-Item -LiteralPath $Candidate -Destination $OutputRoot
} finally {
    foreach ($temporaryRoot in @($BuildRoot, $DistRoot)) {
        $resolved = [System.IO.Path]::GetFullPath($temporaryRoot)
        if (-not $resolved.StartsWith($BuildBase + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a grounding runtime path outside the build directory"
        }
        if (Test-Path -LiteralPath $resolved) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}

Write-Host "Grounding runtime candidate ready: $OutputRoot" -ForegroundColor Green
