param(
    # Folder the consuming project resolves the SDK wheel from (--find-links).
    [string]$ConsumerVendor = (Join-Path $PSScriptRoot "..\..\backend\vendor")
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - not releasing." }

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
python -m build --wheel
if ($LASTEXITCODE -ne 0) { throw "Build failed." }

New-Item -ItemType Directory -Force -Path $ConsumerVendor | Out-Null
# Replace older SDK wheels so the consumer only sees the intended release.
Remove-Item (Join-Path $ConsumerVendor "rag_platform_sdk-*.whl") -ErrorAction SilentlyContinue
Copy-Item dist\*.whl $ConsumerVendor
Write-Host "Published to $ConsumerVendor"
Get-ChildItem $ConsumerVendor

