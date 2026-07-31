[CmdletBinding()]
param(
    [ValidateRange(5, 100)]
    [int]$PairCount = 10,

    [int]$StartSeed = 6000,

    [ValidateRange(1, 3600)]
    [double]$MaxSeconds = 60,

    [switch]$Visible,

    [string]$EngineRoot = "C:\Program Files\Epic Games\UE_5.8"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$episodesRoot = Join-Path $repositoryRoot "Saved\SimTrace\episodes"
$benchmarkRoot = Join-Path $repositoryRoot "Saved\SimTrace\benchmarks"
$runScript = Join-Path $PSScriptRoot "run_simtrace.ps1"
$powerShellPath = (Get-Process -Id $PID).Path
$benchmarkId = "capture_" + [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$outputDirectory = Join-Path $benchmarkRoot $benchmarkId
$partialPath = Join-Path $outputDirectory "design.partial.json"
$finalPath = Join-Path $outputDirectory "design.json"

New-Item -ItemType Directory -Path $episodesRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

function Get-EpisodeDirectoryNames {
    if (-not (Test-Path -LiteralPath $episodesRoot -PathType Container)) {
        return @()
    }
    return @(
        Get-ChildItem -LiteralPath $episodesRoot -Directory |
            Select-Object -ExpandProperty Name
    )
}

$script:knownEpisodeNames = @{}
foreach ($name in Get-EpisodeDirectoryNames) {
    $script:knownEpisodeNames[$name] = $true
}

function Invoke-CaptureCondition {
    param(
        [Parameter(Mandatory = $true)][int]$Seed,
        [Parameter(Mandatory = $true)][ValidateSet(0, 1)][int]$Capture
    )

    $arguments = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        $runScript
        "-Mode"
        "bot"
        "-Seed"
        $Seed
        "-BatchCount"
        1
        "-Capture"
        $Capture
        "-MaxSeconds"
        $MaxSeconds
        "-EngineRoot"
        $EngineRoot
    )
    if (-not $Visible) {
        $arguments += "-Headless"
    }
    & $powerShellPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "SimTrace exited with code $LASTEXITCODE for seed $Seed capture=$Capture"
    }

    $created = @(
        Get-EpisodeDirectoryNames |
            Where-Object { -not $script:knownEpisodeNames.ContainsKey($_) }
    )
    if ($created.Count -ne 1) {
        throw "Expected one new episode for seed $Seed capture=$Capture, found $($created.Count)."
    }
    $script:knownEpisodeNames[$created[0]] = $true
    $episodeDirectory = Join-Path $episodesRoot $created[0]
    $manifestPath = Join-Path $episodeDirectory "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Benchmark episode did not finish: $episodeDirectory"
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $expectedCaptureHz = if ($Capture -eq 1) { 10 } else { 0 }
    if (
        $manifest.mode -ne "bot" -or
        [int]$manifest.seed -ne $Seed -or
        [double]$manifest.capture_hz -ne $expectedCaptureHz -or
        $manifest.complete -ne $true
    ) {
        throw "Benchmark manifest does not match seed $Seed capture=$Capture."
    }
    return [string]$manifest.episode_id
}

$gitRevision = (& git -C $repositoryRoot rev-parse --short=12 HEAD).Trim()
$design = [ordered]@{
    schema_version = 1
    benchmark_id = $benchmarkId
    method = "paired capture on/off episode medians by course seed"
    condition_order = "alternating"
    pair_count = $PairCount
    start_seed = $StartSeed
    git_revision = $gitRevision
    created_utc = [DateTime]::UtcNow.ToString("o")
    complete = $false
    pairs = @()
}

for ($index = 0; $index -lt $PairCount; $index++) {
    $seed = $StartSeed + $index
    $order = if ($index % 2 -eq 0) { @(0, 1) } else { @(1, 0) }
    $episodes = @{}
    foreach ($capture in $order) {
        $episodeId = Invoke-CaptureCondition -Seed $seed -Capture $capture
        $condition = if ($capture -eq 1) { "capture_on" } else { "capture_off" }
        $episodes[$condition] = $episodeId
    }
    $design.pairs += [ordered]@{
        seed = $seed
        order = @(
            $order | ForEach-Object {
                if ($_ -eq 1) { "capture_on" } else { "capture_off" }
            }
        )
        capture_off_episode_id = $episodes.capture_off
        capture_on_episode_id = $episodes.capture_on
    }
    $design | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $partialPath -Encoding utf8
}

$design.complete = $true
$design.completed_utc = [DateTime]::UtcNow.ToString("o")
$design | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $partialPath -Encoding utf8
Move-Item -LiteralPath $partialPath -Destination $finalPath

uv run python (Join-Path $PSScriptRoot "validate_dataset.py") report $episodesRoot
if ($LASTEXITCODE -ne 0) {
    throw "Dataset report generation failed with code $LASTEXITCODE."
}

Write-Output $finalPath
