[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+([-.][A-Za-z0-9.]+)?$')]
    [string]$Version = '0.1.0',
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$iscc = Join-Path $projectRoot 'tools\innosetup\ISCC.exe'
$entryPoint = Join-Path $projectRoot 'src\golftrace\__main__.py'
$appIcon = Join-Path $projectRoot 'src\golftrace\assets\golftrace.ico'
$releaseRoot = Join-Path $projectRoot 'release'
$buildRoot = Join-Path $projectRoot 'build\windows'
$bundleRoot = Join-Path $projectRoot 'dist\GolfTraceOpen'

foreach ($required in @($python, $iscc, $entryPoint, $appIcon, (Join-Path $projectRoot 'requirements-lock.txt'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing build prerequisite: $required"
    }
}
New-Item -ItemType Directory -Force -Path $releaseRoot, $buildRoot | Out-Null

Push-Location $projectRoot
try {
    & $python -c "import struct, sys, cv2, numpy, PySide6, PyInstaller; assert sys.platform == 'win32' and struct.calcsize('P') == 8; print('Windows x64 build dependencies verified')"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency preflight failed' }
    & $python -m compileall -q (Join-Path $projectRoot 'src')
    if ($LASTEXITCODE -ne 0) { throw 'Source compilation failed' }
    if (-not $SkipTests) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'tests'))) { throw 'No tests directory; use -SkipTests only after an explicit independent verification.' }
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; release build stopped' }
    }

    $licenseStage = Join-Path $buildRoot 'licenses'
    & $python (Join-Path $PSScriptRoot 'collect_licenses.py') $licenseStage
    if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\licenses\THIRD_PARTY_NOTICES.txt') -Destination $licenseStage -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\licenses\qt') -Destination (Join-Path $licenseStage 'qt-upstream') -Recurse -Force

    $pyinstallerArgs = @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', 'GolfTraceOpen', '--noupx',
        '--icon', $appIcon,
        '--paths', (Join-Path $projectRoot 'src'),
        '--collect-submodules', 'golftrace',
        '--collect-data', 'golftrace',
        '--distpath', (Join-Path $projectRoot 'dist'),
        '--workpath', (Join-Path $buildRoot 'pyinstaller'),
        '--specpath', $buildRoot,
        '--add-data', "${licenseStage}:licenses",
        (Join-Path $PSScriptRoot 'frozen_launcher.py')
    )
    $taskPreviousPythonPath = $env:PYTHONPATH
    $taskPreviousPath = $env:PATH
    $taskPreviousPyinstallerConfig = $env:PYINSTALLER_CONFIG_DIR
    try {
        $sourceRoot = Join-Path $projectRoot 'src'
        $basePythonRoot = & $python -c "import sys; print(sys.base_prefix)"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot identify the isolated Python runtime' }
        $env:PYTHONPATH = if ($taskPreviousPythonPath) { "$sourceRoot$([IO.Path]::PathSeparator)$taskPreviousPythonPath" } else { $sourceRoot }
        # Exclude unrelated native tools: their ICU/UCRT DLLs can shadow Windows libraries.
        $env:PATH = @((Split-Path $python), $basePythonRoot, (Join-Path $basePythonRoot 'DLLs'), (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join [IO.Path]::PathSeparator
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot 'pyinstaller-cache'
        & $python @pyinstallerArgs
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    }
    finally {
        if ($null -eq $taskPreviousPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
        else { $env:PYTHONPATH = $taskPreviousPythonPath }
        $env:PATH = $taskPreviousPath
        if ($null -eq $taskPreviousPyinstallerConfig) { Remove-Item Env:PYINSTALLER_CONFIG_DIR -ErrorAction SilentlyContinue }
        else { $env:PYINSTALLER_CONFIG_DIR = $taskPreviousPyinstallerConfig }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot 'GolfTraceOpen.exe'))) { throw 'Application executable was not created' }

    $startupSmokeRoot = Join-Path $buildRoot ("frozen-startup-$([Guid]::NewGuid().ToString('N'))")
    New-Item -ItemType Directory -Path $startupSmokeRoot | Out-Null
    $taskPreviousRunPath = $env:PATH
    try {
        $env:PATH = @((Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join [IO.Path]::PathSeparator
        $startupArgs = @('--smoke-test', "`"$startupSmokeRoot`"", '--native-smoke')
        $startupProcess = Start-Process -FilePath (Join-Path $bundleRoot 'GolfTraceOpen.exe') -ArgumentList $startupArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $startupSmokeRoot 'stdout.log') -RedirectStandardError (Join-Path $startupSmokeRoot 'stderr.log')
        if (-not $startupProcess.WaitForExit(30000)) {
            Stop-Process -Id $startupProcess.Id -ErrorAction SilentlyContinue
            throw "Frozen application startup timed out; see $startupSmokeRoot"
        }
        $startupProcess.Refresh()
        $startupJson = Join-Path $startupSmokeRoot 'smoke.json'
        if ($startupProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $startupJson)) {
            throw "Frozen application startup failed; see $startupSmokeRoot"
        }
        $startupResult = Get-Content -LiteralPath $startupJson -Raw | ConvertFrom-Json
        if (-not $startupResult.success -or $startupResult.qt_platform -ne 'windows') {
            throw "Frozen application reported startup failure; see $startupSmokeRoot"
        }
        Write-Output "frozen_startup_verified: $startupJson"
    }
    finally { $env:PATH = $taskPreviousRunPath }

    Copy-Item -LiteralPath (Join-Path $projectRoot 'requirements-lock.txt') -Destination $bundleRoot -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\toolchain.json') -Destination $bundleRoot -Force
    foreach ($document in @('README.md', 'LICENSE')) {
        $documentPath = Join-Path $projectRoot $document
        if (Test-Path -LiteralPath $documentPath -PathType Leaf) { Copy-Item -LiteralPath $documentPath -Destination $bundleRoot -Force }
    }
    $isccArgs = @('--quiet-progress', "--define=AppVersion=$Version", "--define=BundleDir=$bundleRoot", "--define=ReleaseDir=$releaseRoot", "--define=AppIcon=$appIcon", (Join-Path $projectRoot 'packaging\installer.iss'))
    & $iscc @isccArgs
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }

    $installerPath = Join-Path $releaseRoot "GolfTraceOpen-Setup-$Version-win-x64.exe"
    $zipPath = Join-Path $releaseRoot "GolfTraceOpen-$Version-win-x64.zip"
    Compress-Archive -Path $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal -Force
    $hashLines = foreach ($artifact in @($installerPath, $zipPath)) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Missing release artifact: $artifact" }
        $artifactHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        "$artifactHash  $([IO.Path]::GetFileName($artifact))"
    }
    $hashLines | Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ascii
    Write-Output "build_complete: $installerPath"
    Write-Output "portable_zip: $zipPath"
    Write-Output 'Installation and GUI behavior require separate smoke verification.'
}
finally {
    Pop-Location
}
