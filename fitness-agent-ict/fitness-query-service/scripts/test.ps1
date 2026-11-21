$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build.ps1')
$moduleRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$classesPath = Join-Path $moduleRoot 'build\classes'
$testClassesPath = Join-Path $moduleRoot 'build\test-classes'
New-Item -ItemType Directory -Path $testClassesPath -Force | Out-Null
$testFiles = @(Get-ChildItem -LiteralPath (Join-Path $moduleRoot 'src\test\java') -Recurse -Filter '*.java' | ForEach-Object { $_.FullName })
& javac -encoding UTF-8 --release 17 --add-modules jdk.httpserver -cp $classesPath -d $testClassesPath @testFiles
if ($LASTEXITCODE -ne 0) { throw 'Test compilation failed.' }
$testClasspath = "$classesPath;$testClassesPath;$(Join-Path $moduleRoot 'src\main\resources')"
& java --add-modules jdk.httpserver -cp $testClasspath cn.fitness.query.QueryServiceTest
if ($LASTEXITCODE -ne 0) { throw 'Java checks failed.' }
