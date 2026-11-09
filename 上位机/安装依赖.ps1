$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# 所有依赖、临时文件和pip缓存都保存在当前目录。
$taskTemporary = Join-Path $PSScriptRoot '.runtime\tmp'
$taskCache = Join-Path $PSScriptRoot '.runtime\pip-cache'
New-Item -ItemType Directory -Path $taskTemporary, $taskCache -Force | Out-Null
$env:TEMP = $taskTemporary
$env:TMP = $taskTemporary
$env:PYTHONDONTWRITEBYTECODE = '1'

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.11 -B -m venv '.venv'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment.' }
}

& '.\.venv\Scripts\python.exe' -B -m pip --isolated --cache-dir $taskCache install -r '.\requirements.txt'
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
