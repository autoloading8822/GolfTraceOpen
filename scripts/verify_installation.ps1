[CmdletBinding()]
param(
    [ValidatePattern('^[a-z0-9-]+$')][string]$RunId = 'final',
    [switch]$NativeSmoke
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskArtifacts = Join-Path $taskRoot 'artifacts'
$taskVideo = Join-Path $taskArtifacts 'qa-synthetic\synthetic-ball.avi'
$taskInstaller = Join-Path $taskRoot 'release\GolfTraceOpen-Setup-0.1.0-win-x64.exe'
$taskInstall = [IO.Path]::GetFullPath((Join-Path $taskArtifacts "installed-qa-$RunId"))
$taskRegistry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{AE593493-321C-4F1E-9FD7-C364E84B0C1D}_is1'
if (-not $taskInstall.StartsWith($taskArtifacts + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'QA installation target is outside the workspace artifacts directory' }
if (Test-Path -LiteralPath $taskInstall) { throw 'QA installation directory already exists; inspect before reusing it' }
if (Test-Path -LiteralPath $taskRegistry) { throw 'An installation with this app ID already exists; do not overwrite it during QA' }

function Invoke-Exercise([string]$Executable, [string]$Output, [bool]$CleanPath) {
    $taskSavedPath = $env:PATH
    try {
        if ($CleanPath) { $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot" }
        $taskArguments = @('--smoke-test', $Output, '--video', $taskVideo, '--exercise', '--seed-x', '60', '--seed-y', '190', '--ball-radius', '4')
        if ($NativeSmoke) { $taskArguments += '--native-smoke' }
        $taskRun = Start-Process -FilePath $Executable -ArgumentList $taskArguments -WindowStyle Hidden -PassThru -Wait
        if ($taskRun.ExitCode -ne 0) { throw "Application exercise exited $($taskRun.ExitCode); inspect $Output\smoke.json" }
        $taskSmoke = Get-Content -Raw -LiteralPath (Join-Path $Output 'smoke.json') | ConvertFrom-Json
        if (-not $taskSmoke.success -or $taskSmoke.tracked_count -lt 30 -or -not $taskSmoke.manual_edit -or -not $taskSmoke.project_saved -or -not $taskSmoke.export.readable -or $taskSmoke.export.frame_count -ne 32 -or -not $taskSmoke.controls_restored -or -not $taskSmoke.worker_finished) { throw 'Application exercise returned incomplete results' }
        if ($NativeSmoke -and $taskSmoke.qt_platform -ne 'windows') { throw 'Native Windows platform was not exercised' }
        return $taskSmoke
    } finally { $env:PATH = $taskSavedPath }
}

$taskFrozen = Invoke-Exercise (Join-Path $taskRoot 'dist\GolfTraceOpen\GolfTraceOpen.exe') (Join-Path $taskArtifacts "frozen-independent-$RunId") $false
$taskSetup = Start-Process -FilePath $taskInstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/DIR=$taskInstall", "/LOG=$(Join-Path $taskArtifacts 'install-qa.log')") -WindowStyle Hidden -Wait -PassThru
if ($taskSetup.ExitCode -ne 0) { throw "Installer exited $($taskSetup.ExitCode)" }
$taskInstalledExecutable = Join-Path $taskInstall 'GolfTraceOpen.exe'
if (-not (Test-Path -LiteralPath $taskInstalledExecutable)) { throw 'Installed executable missing' }
if (-not (Test-Path -LiteralPath $taskRegistry)) { throw 'Per-user uninstall registration missing' }
$taskRegistration = Get-ItemProperty -LiteralPath $taskRegistry
if ([IO.Path]::GetFullPath($taskRegistration.InstallLocation).TrimEnd('\') -ne $taskInstall) { throw 'Unexpected registered installation location' }
$taskInstalled = Invoke-Exercise $taskInstalledExecutable (Join-Path $taskArtifacts "installed-exercise-$RunId") $true
$taskUninstaller = Join-Path $taskInstall 'unins000.exe'
if (-not (Test-Path -LiteralPath $taskUninstaller)) { throw 'Uninstaller missing' }
$taskUninstall = Start-Process -FilePath $taskUninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $taskArtifacts 'uninstall-qa.log')") -WindowStyle Hidden -Wait -PassThru
if ($taskUninstall.ExitCode -ne 0) { throw "Uninstaller exited $($taskUninstall.ExitCode)" }
if ((Test-Path -LiteralPath $taskInstalledExecutable) -or (Test-Path -LiteralPath $taskRegistry)) { throw 'QA uninstall left the executable or registration behind' }
$taskSignature = Get-AuthenticodeSignature -LiteralPath $taskInstaller
$taskResult = [ordered]@{
    success = $true
    tested_os = 'Windows 11 x64 10.0.26100'
    synthetic_evidence_only = $true
    native_windows_platform_tested = [bool]$NativeSmoke
    frozen_exercise = $taskFrozen
    installation = @{exit_code=$taskSetup.ExitCode; path=$taskInstall; per_user_registration=$true}
    installed_exercise_clean_path = $taskInstalled
    uninstall = @{exit_code=$taskUninstall.ExitCode; executable_removed=$true; registry_removed=$true}
    installer_authenticode = [string]$taskSignature.Status
}
$taskResult | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskArtifacts "installation-verification-$RunId.json") -Encoding utf8
Write-Output 'Verified: final frozen EXE, silent per-user installation, installed EXE tracking/edit/save/export, and QA uninstall.'
Write-Output ('Installer Authenticode: ' + $taskSignature.Status)
