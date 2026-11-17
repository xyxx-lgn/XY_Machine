$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$hostPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $hostPython)) { throw '请先运行安装依赖.ps1，准备上位机主环境。' }
$env:TEMP = Join-Path $PSScriptRoot '.runtime/tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$offline = $args -contains '--offline'

if ($offline) {
    & $hostPython -B -X utf8 tools/prepare_trajectory_model.py --offline
} else {
    & $hostPython -B -X utf8 tools/prepare_trajectory_model.py
}
if ($LASTEXITCODE -ne 0) { throw '轨迹模型资源准备未完成，请查看上方具体错误。' }

$aiPython = Join-Path $PSScriptRoot '.runtime/sdt/venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $aiPython)) {
    if ($offline) { throw '未找到AI运行环境，请联网运行一次本脚本。' }
    & $hostPython -B -X utf8 -m venv .runtime/sdt/venv
    if ($LASTEXITCODE -ne 0) { throw 'AI独立环境创建失败。' }
}
& $aiPython -B -X utf8 -c "import torch, numpy"
if ($LASTEXITCODE -ne 0) {
    if ($offline) { throw 'AI运行依赖不完整，请联网运行一次本脚本。' }
    # 轨迹模型只需PyTorch与NumPy，不额外下载SDT源码及SDT权重。
    & $aiPython -B -X utf8 -m pip install --cache-dir .runtime/pip-cache torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
    if ($LASTEXITCODE -ne 0) { throw 'CUDA版PyTorch安装失败。' }
    & $aiPython -B -X utf8 -m pip install --cache-dir .runtime/pip-cache numpy==1.26.4
    if ($LASTEXITCODE -ne 0) { throw 'NumPy安装失败。' }
}
& $aiPython -B -X utf8 -c "import sys, torch; print('CUDA可用:', torch.cuda.is_available()); print('设备:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无'); sys.exit(0 if torch.cuda.is_available() else 1)"
if ($LASTEXITCODE -ne 0) { throw 'AI运行环境检查失败。' }
Write-Host '轨迹扩字模型准备完成。生成阶段使用本地资源，不访问网络；请在字库管理中选择“离线AI轨迹扩字”。'
