[CmdletBinding()]
param(
    [string]$EngineRoot = "C:\Program Files\Epic Games\UE_5.8"
)

$ErrorActionPreference = "Stop"
$projectPath = Join-Path $PSScriptRoot "..\UnrealSimTrace.uproject"
$buildScript = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"

if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw "Unreal build script was not found: $buildScript"
}
if (-not (Test-Path -LiteralPath $projectPath -PathType Leaf)) {
    throw "Unreal project was not found: $projectPath"
}

& $buildScript UnrealSimTraceEditor Win64 Development $projectPath `
    -WaitMutex -NoHotReloadFromIDE
exit $LASTEXITCODE
