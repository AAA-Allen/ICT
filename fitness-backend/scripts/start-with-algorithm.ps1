param([int]$Port = 8082)
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Split-Path $backendRoot -Parent
$algorithmRoot = Join-Path $workspaceRoot 'squat-algorithm-handoff-20261119'
$env:FITNESS_ALGORITHM_SCRIPT = Join-Path $algorithmRoot 'scripts/squat_offline_api.py'
$env:FITNESS_ALGORITHM_MODEL = Join-Path $algorithmRoot 'models/pose_landmarker_lite.task'
$env:FITNESS_ALGORITHM_PYTHON = Join-Path $algorithmRoot '.venv/Scripts/python.exe'
foreach ($target in @($env:FITNESS_ALGORITHM_SCRIPT, $env:FITNESS_ALGORITHM_MODEL, $env:FITNESS_ALGORITHM_PYTHON)) {
    if (!(Test-Path -LiteralPath $target -PathType Leaf)) { throw "Required file missing: $target" }
}
& (Join-Path $backendRoot '.venv/Scripts/python.exe') -X utf8 -m fitness_backend.server --port $Port --data-dir (Join-Path $backendRoot 'outputs/fitness_backend')
exit $LASTEXITCODE
