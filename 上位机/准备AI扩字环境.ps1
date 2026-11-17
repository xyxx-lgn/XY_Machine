$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:TEMP = Join-Path $PSScriptRoot '.runtime/tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$hostPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$aiPython = Join-Path $PSScriptRoot '.runtime/sdt/venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $hostPython)) { throw '请先运行安装依赖.ps1，准备上位机主环境。' }

# AI依赖单独安装；模型与包较大，已校验的文件和下载分段可复用。
& $hostPython -B -X utf8 tools/prepare_sdt.py
if ($LASTEXITCODE -ne 0) { throw '模型资源准备未完成，请查看上方具体错误，恢复网络后重试。' }
if (-not (Test-Path -LiteralPath $aiPython)) {
    & $hostPython -B -X utf8 -m venv .runtime/sdt/venv
    if ($LASTEXITCODE -ne 0) { throw 'AI独立环境创建失败。' }
}
& $hostPython -B -X utf8 tools/prepare_sdt.py --cuda-wheel
if ($LASTEXITCODE -ne 0) { throw 'CUDA版PyTorch下载未完成，已下载分段会保留。' }
& $aiPython -B -X utf8 -m pip install --cache-dir .runtime/pip-cache '.runtime/sdt/downloads/torch-2.5.1+cu121-cp311-cp311-win_amd64.whl' torchvision==0.20.1 --constraint requirements-sdt.txt --index-url https://download.pytorch.org/whl/cu121
if ($LASTEXITCODE -ne 0) { throw 'CUDA运行依赖安装失败。' }
& $aiPython -B -X utf8 -m pip install --cache-dir .runtime/pip-cache -r requirements-sdt.txt
if ($LASTEXITCODE -ne 0) { throw 'AI其余依赖安装失败。' }
& $aiPython -B -X utf8 tools/prepare_sdt.py --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'AI环境启动检查失败。' }
Write-Host '环境和资源准备完成。请在“自定义字库 → AI扩展”选择样本目录，检查后先生成少量试字。'
