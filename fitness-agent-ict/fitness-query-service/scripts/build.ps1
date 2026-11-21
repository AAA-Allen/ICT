$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$moduleRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$classesPath = Join-Path $moduleRoot 'build\classes'
$jarPath = Join-Path $moduleRoot 'build\fitness-query-service.jar'
foreach ($toolName in @('java', 'javac', 'jar')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) {
        throw "JDK 17+ required: command '$toolName' is missing."
    }
}
New-Item -ItemType Directory -Path $classesPath -Force | Out-Null
$sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $moduleRoot 'src\main\java') -Recurse -Filter '*.java' | ForEach-Object { $_.FullName })
& javac -encoding UTF-8 --release 17 --add-modules jdk.httpserver -d $classesPath @sourceFiles
if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed.' }
& jar --create --file $jarPath --main-class cn.fitness.query.FitnessQueryApplication -C $classesPath . -C (Join-Path $moduleRoot 'src\main\resources') .
if ($LASTEXITCODE -ne 0) { throw 'JAR packaging failed.' }
Write-Host "Built: $jarPath"
