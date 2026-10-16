[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolsRoot = Join-Path $projectRoot 'tools'
$downloadRoot = Join-Path $toolsRoot 'downloads'
$innoRoot = Join-Path $toolsRoot 'innosetup'
$installerName = 'innosetup-7.1.0-x64.exe'
$installerUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/$installerName"
$expectedSha256 = '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'
$installerPath = Join-Path $downloadRoot $installerName
$signaturePath = "$installerPath.issig"
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null

if (-not (Test-Path -LiteralPath $installerPath)) {
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath
}
if (-not (Test-Path -LiteralPath $signaturePath)) {
    Invoke-WebRequest -Uri "$installerUrl.issig" -OutFile $signaturePath
}

$authenticode = Get-AuthenticodeSignature -LiteralPath $installerPath
if ($authenticode.Status -ne 'Valid' -or $authenticode.SignerCertificate.Subject -notmatch 'Pyrsys B\.V\.') {
    throw "Inno Setup signature validation failed: $($authenticode.Status)"
}
$sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sha256 -ne $expectedSha256) {
    throw 'Inno Setup does not match the pinned official release SHA-256.'
}
$signatureText = Get-Content -LiteralPath $signaturePath -Raw
if ($signatureText -notmatch ('(?im)^file-hash\s+"?' + [regex]::Escape($sha256) + '"?\s*$')) {
    throw 'Inno Setup hash does not match its official .issig file.'
}

$verification = [ordered]@{
    tool = 'Inno Setup'
    version = '7.1.0'
    source = $installerUrl
    verificationDocumentation = 'https://jrsoftware.org/isdl-verify.php'
    sha256 = $sha256
    authenticodeStatus = $authenticode.Status.ToString()
    signer = $authenticode.SignerCertificate.Subject
    signerThumbprint = $authenticode.SignerCertificate.Thumbprint
    verifiedAtUtc = [DateTime]::UtcNow.ToString('o')
    installMode = 'portable; current user; project-local tools/innosetup'
}
$verification | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $toolsRoot 'innosetup-verification.json') -Encoding utf8

if (-not (Test-Path -LiteralPath (Join-Path $innoRoot 'ISCC.exe'))) {
    $installerArgs = @('/PORTABLE=1', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/NOICONS', '/TASKS=""', "/DIR=`"$innoRoot`"", "/LOG=`"$(Join-Path $toolsRoot 'innosetup-install.log')`"")
    $installProcess = Start-Process -FilePath $installerPath -ArgumentList $installerArgs -Wait -PassThru -WindowStyle Hidden
    if ($installProcess.ExitCode -ne 0) {
        throw "Inno Setup portable installation failed: $($installProcess.ExitCode)"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $innoRoot 'ISCC.exe'))) {
    throw 'ISCC.exe was not created in the project-local tool directory.'
}
Write-Output "inno_ready: $(Join-Path $innoRoot 'ISCC.exe')"
Write-Output "sha256: $sha256"
