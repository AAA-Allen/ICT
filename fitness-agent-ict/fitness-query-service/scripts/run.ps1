param([ValidateRange(1024, 65535)][int]$Port = 8081)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build.ps1')
$moduleRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
& java --add-modules jdk.httpserver -jar (Join-Path $moduleRoot 'build\fitness-query-service.jar') "--port=$Port"
exit $LASTEXITCODE
