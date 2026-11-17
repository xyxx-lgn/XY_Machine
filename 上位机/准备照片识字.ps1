# 仅向本上位机的虚拟环境增加CPU离线识字，不改全局Python，不替换现有OpenCV。
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskTemp = Join-Path $taskRoot '.runtime\tmp'
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.runtime\pip-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '找不到上位机的.venv，请先准备并启动主程序环境。' }
& $taskPython -B -X utf8 -c 'import cv2,numpy; print(cv2.__version__)'
if ($LASTEXITCODE -ne 0) { throw '主环境OpenCV不可用，请先修复上位机环境。' }
& $taskPython -B -X utf8 -m pip install --no-deps --no-compile --disable-pip-version-check -r (Join-Path $taskRoot 'requirements-photo-ocr.txt')
if ($LASTEXITCODE -ne 0) { throw '识字组件准备失败，可保留手工录字，检查网络后重试。' }
Push-Location -LiteralPath $taskRoot
try {
    & $taskPython -B -X utf8 -c 'from app.photo_ocr import ocr_engine; ocr_engine()'
    if ($LASTEXITCODE -ne 0) { throw '识字模型加载失败。' }
    Write-Host '离线识字环境与三个模型加载成功。识字时不联网。'
} finally { Pop-Location }
