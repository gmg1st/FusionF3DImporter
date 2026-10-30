# FusionF3DImporter 安装脚本
# 用法：在 PowerShell 中执行
#   .\install.ps1            # 仅安装插件
#   .\install.ps1 -WithProbe # 同时安装探针脚本到 Scripts 目录
param([switch]$WithProbe)

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot

# Fusion 新旧版本的 API 目录名不同（Autodesk Fusion / Autodesk Fusion 360），优先新目录
$addinsBase = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion\API\AddIns'
if (-not (Test-Path $addinsBase)) {
    $oldBase = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
    if (Test-Path $oldBase) { $addinsBase = $oldBase }
}

$target = Join-Path $addinsBase 'FusionF3DImporter'
New-Item -ItemType Directory -Path $target -Force | Out-Null

Copy-Item (Join-Path $src 'FusionF3DImporter.manifest') $target -Force
Copy-Item (Join-Path $src 'FusionF3DImporter.py')       $target -Force
Copy-Item (Join-Path $src 'palette.html')               $target -Force
Copy-Item (Join-Path $src 'resources')                  $target -Recurse -Force

Write-Host "已安装到: $target" -ForegroundColor Green
Write-Host '下一步：重启 Fusion 360，然后 工具(UTILITIES) -> 脚本和附加模块 -> 附加模块 中勾选启用 FusionF3DImporter（可勾选"运行时启动"开机自动加载）'

if ($WithProbe) {
    $scriptsBase = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion\API\Scripts'
    if (-not (Test-Path $scriptsBase)) {
        $oldScripts = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\Scripts'
        if (Test-Path $oldScripts) { $scriptsBase = $oldScripts }
    }
    Copy-Item (Join-Path $src 'ProbeContexts') $scriptsBase -Recurse -Force
    Write-Host "探针脚本已安装到: $(Join-Path $scriptsBase 'ProbeContexts')" -ForegroundColor Green
}
