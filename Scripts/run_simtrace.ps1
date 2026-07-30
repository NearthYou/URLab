[CmdletBinding()]
param(
    [ValidateSet("human", "bot", "input-replay", "native-replay")]
    [string]$Mode = "human",

    [int]$Seed = 1000,
    [ValidateRange(1, 1000)]
    [int]$BatchCount = 1,
    [ValidateSet(0, 1)]
    [int]$Capture = 1,
    [ValidateRange(1, 3600)]
    [double]$MaxSeconds = 60,
    [string]$InputPath,
    [string]$ReplayName,
    [switch]$Headless,
    [string]$EngineRoot = "C:\Program Files\Epic Games\UE_5.8"
)

$ErrorActionPreference = "Stop"
$projectPath = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\UnrealSimTrace.uproject")
)
$editorPath = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"

if (-not (Test-Path -LiteralPath $editorPath -PathType Leaf)) {
    throw "Unreal Editor was not found: $editorPath"
}
if ($Mode -eq "input-replay" -and [string]::IsNullOrWhiteSpace($InputPath)) {
    throw "input-replay mode requires -InputPath."
}
if ($Mode -eq "native-replay" -and [string]::IsNullOrWhiteSpace($ReplayName)) {
    throw "native-replay mode requires -ReplayName."
}
if (
    -not [string]::IsNullOrWhiteSpace($ReplayName) -and
    $ReplayName -notmatch "^[A-Za-z0-9_.-]+$"
) {
    throw "ReplayName may contain only letters, numbers, dot, underscore, and hyphen."
}

$resolvedInputPath = $null
if (-not [string]::IsNullOrWhiteSpace($InputPath)) {
    $resolvedInputPath = [System.IO.Path]::GetFullPath($InputPath)
    if (-not (Test-Path -LiteralPath $resolvedInputPath -PathType Leaf)) {
        throw "Input trajectory was not found: $resolvedInputPath"
    }
}

$arguments = @(
    $projectPath
    "/Engine/Maps/Entry"
    "-game"
    "-nop4"
    "-nosplash"
    "-SimTraceMode=$Mode"
    "-SimTraceSeed=$Seed"
    "-SimTraceBatchCount=$BatchCount"
    "-SimTraceCapture=$Capture"
    "-SimTraceMaxSeconds=$MaxSeconds"
)

if ($resolvedInputPath) {
    $arguments += "-SimTraceInput=$resolvedInputPath"
}
if (-not [string]::IsNullOrWhiteSpace($ReplayName)) {
    $arguments += "-SimTraceReplay=$ReplayName"
}

if ($Headless) {
    $arguments += @("-unattended", "-RenderOffscreen", "-NoSound")
} else {
    $arguments += @("-windowed", "-ResX=1280", "-ResY=720")
}

function ConvertTo-WindowsProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Argument)

    $escaped = $Argument -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

$argumentLine = (
    $arguments | ForEach-Object { ConvertTo-WindowsProcessArgument $_ }
) -join " "

$startArguments = @{
    FilePath = $editorPath
    ArgumentList = $argumentLine
    PassThru = $true
    Wait = $true
}
if ($Headless) {
    $startArguments.WindowStyle = "Hidden"
}

$process = Start-Process @startArguments
exit $process.ExitCode
