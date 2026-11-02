param(
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'SeoulDocumentRegister'),
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$ProgressPreference = 'SilentlyContinue'
function Get-RecoveryFile([string]$Uri, [string]$Destination) {
    $curlPath = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curlPath -PathType Leaf) {
        & $curlPath '--fail' '--location' '--proto' '=https' '--proto-redir' '=https' '--retry' '2' '--connect-timeout' '20' '--max-time' '180' '--output' $Destination $Uri
        if ($LASTEXITCODE -ne 0) { throw 'Windows 다운로드에 실패했습니다. 기존 프로그램은 변경하지 않았습니다.' }
    } else {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri $Uri -OutFile $Destination
    }
}
$version = '2.0.1'
$expectedHash = '7465175f86910f5a9c3a50a4801d84fb2c8362f6ced07019b139f2c27f9c246f'
$fileName = 'SeoulDocumentRegister-2.0.1.exe'
$baseUrl = 'https://github.com/raphlow23/seoul-document-register-updates/releases/download/v2.0.1'
$dataPath = [IO.Path]::GetFullPath($DataRoot)
if ((Split-Path -Leaf $dataPath) -ne 'SeoulDocumentRegister') { throw 'Invalid application data directory.' }
$target = [IO.Path]::GetFullPath((Join-Path $dataPath 'Program\SeoulDocumentRegister.exe'))
if (-not $target.StartsWith(($dataPath.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid installation path.' }
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw '설치된 프로그램을 찾지 못했습니다. GitHub의 최신 설치 ZIP을 받아 install.cmd를 실행해 주세요.'
}
$active = Get-Process -Name 'SeoulDocumentRegister' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $target }
if ($active) { throw '공문 수신등록 프로그램을 먼저 종료한 뒤 복구를 다시 실행해 주세요. 저장하지 않은 작업을 자동으로 닫지 않습니다.' }

if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    $cache = Join-Path $dataPath ('updates\recovery-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $payload = Join-Path $cache $fileName
    $manifestPath = Join-Path $cache 'latest.json'
    Write-Host 'Windows HTTPS로 수정된 프로그램을 내려받습니다. 기존 파일과 등록 이력은 보존됩니다.'
    Get-RecoveryFile ($baseUrl + '/latest.json') $manifestPath
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.version -ne $version -or $manifest.filename -ne $fileName -or $manifest.sha256 -ne $expectedHash) { throw '배포 안내가 준비된 복구 버전과 다릅니다. 기존 프로그램은 변경하지 않았습니다.' }
    Get-RecoveryFile ($baseUrl + '/' + $fileName) $payload
    if ((Get-Item -LiteralPath $payload).Length -ne $manifest.size -or (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '다운로드 검증에 실패했습니다. 기존 프로그램은 변경하지 않았습니다.' }

    Write-Host '검증된 실행파일을 점검합니다.'
    $health = Start-Process -FilePath $payload -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
    if ($health.ExitCode -ne 0) { throw '수정본의 실행 검사에 실패했습니다. 기존 프로그램은 변경하지 않았습니다.' }

    # The installed, trusted updater verifies its pinned Ed25519 signature offline.
    $helper = Join-Path $cache 'recovery-helper.exe'
    Copy-Item -LiteralPath $target -Destination $helper
    $finished = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -ArgumentList '/d /c exit 0' -WindowStyle Hidden -Wait -PassThru
    $plan = @{parent_pid=$finished.Id;target=$target;staged=$payload;manifest=$manifest}
    $planPath = Join-Path $cache 'plan-recovery.json'
    $json = $plan | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($planPath, $json, (New-Object Text.UTF8Encoding($false)))
    Write-Host '기존 프로그램의 서명 검사기를 이용해 안전하게 교체합니다.'
    $install = Start-Process -FilePath $helper -ArgumentList @('--test-apply-update', ('"' + $planPath + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw ('서명 검사 또는 교체에 실패했습니다. 기록: ' + (Join-Path $cache 'last-result.json')) }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '설치 후 파일 검증에 실패했습니다.' }
}

# An empty setup directory changes only the online update URL and auto-update switch.
# It does not replace account settings, HWP forms, or registration history.
$setupRoot = Join-Path $dataPath ('updates\recovery-settings-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $setupRoot -Force | Out-Null
$previousAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Split-Path -Parent $dataPath
    $setup = Start-Process -FilePath $target -ArgumentList @('--prepare-install', ('"' + $setupRoot + '"')) -WindowStyle Hidden -Wait -PassThru
} finally {
    $env:LOCALAPPDATA = $previousAppData
}
if ($setup.ExitCode -ne 0) { throw '프로그램 교체는 끝났으나 자동 업데이트 연결 설정을 확인하지 못했습니다.' }
Write-Host '복구 완료: v2.0.1. 앞으로 실행할 때 새 버전을 자동으로 확인합니다.' -ForegroundColor Green
if (-not $NoLaunch) { Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Hidden }
