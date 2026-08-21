$ErrorActionPreference = "Stop"

# 1. Update package.json
$pkgPath = "D:\ProductAtelier-Desktop\package.json"
$pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
$pkg.scripts | Add-Member -NotePropertyName "postbuild" -NotePropertyValue "node scripts/copy-assets.cjs" -Force
$jsonOut = $pkg | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($pkgPath, $jsonOut, [System.Text.UTF8Encoding]::new($false))
Write-Host "package.json updated"

# 2. Copy assets for dev mode
$publicShoelace = "D:\ProductAtelier-Desktop\src\public\shoelace"
New-Item -ItemType Directory -Path $publicShoelace -Force | Out-Null
$src = "D:\ProductAtelier-Desktop\node_modules\@shoelace-style\shoelace\dist\assets"
$dst = Join-Path $publicShoelace "assets"
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Copy-Item -Path $src -Destination $dst -Recurse -Force
$count = (Get-ChildItem $dst -Recurse -Filter *.svg | Measure-Object).Count
Write-Host "Dev assets ready ($count icons)"