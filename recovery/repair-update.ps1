param(
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'SeoulDocumentRegister'),
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$version = '2.8.7'
$expectedHash = 'c155dc56da8e3ed2b55d925eafc1ba9003e167e603e8cf9663adb48e6e88a2ad'
$expectedSize = 55459367
$fileName = 'SeoulDocumentRegister-' + $version + '.exe'
$api = 'https://api.github.com/repos/raphlow23/seoul-document-register-updates/releases/'
function Get-RecoveryFile([string]$Uri, [string]$Destination) {
    $accept = 'application/vnd.github+json'
    if ($Uri.Contains('/releases/assets/')) { $accept = 'application/octet-stream' }
    $curlPath = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curlPath -PathType Leaf) {
        & $curlPath '--fail' '--location' '--proto' '=https' '--proto-redir' '=https' '--retry' '2' '--connect-timeout' '20' '--max-time' '240' '--max-filesize' '104857600' '--header' ('Accept: ' + $accept) '--header' 'User-Agent: SeoulDocumentRegister-Recovery' '--output' $Destination $Uri
        if ($LASTEXITCODE -ne 0) { throw '공식 배포 파일 다운로드에 실패했습니다. 기존 프로그램은 유지합니다.' }
    } else {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 240 -Headers @{'Accept'=$accept;'User-Agent'='SeoulDocumentRegister-Recovery'} -Uri $Uri -OutFile $Destination
    }
}
$dataPath = [IO.Path]::GetFullPath($DataRoot)
if ((Split-Path -Leaf $dataPath) -ne 'SeoulDocumentRegister') { throw 'Invalid application data directory.' }
$target = [IO.Path]::GetFullPath((Join-Path $dataPath 'Program\SeoulDocumentRegister.exe'))
if (-not $target.StartsWith(($dataPath.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid installation path.' }
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw '설치된 프로그램을 찾지 못했습니다. 기존 프로그램을 설치했던 Windows 계정에서 실행하세요.' }
$active = Get-Process -Name 'SeoulDocumentRegister' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $target }
if ($active) { throw '공문 프로그램을 먼저 종료한 뒤 다시 실행하세요. 저장하지 않은 작업을 자동으로 닫지 않습니다.' }
$cache = Join-Path (Split-Path -Parent $target) ('임시보관\업데이트복구-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $cache -Force | Out-Null
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    $releasePath = Join-Path $cache 'release.json'
    $manifestPath = Join-Path $cache 'latest.json'
    $payload = Join-Path $cache $fileName
    Write-Host 'GitHub 공식 배포 API에서 수정본을 받습니다. 기존 설정과 이력, 양식은 유지합니다.'
    Get-RecoveryFile ($api + 'tags/v' + $version) $releasePath
    if ((Get-Item -LiteralPath $releasePath).Length -gt 2097152) { throw '배포 안내가 허용 크기를 넘습니다.' }
    $release = Get-Content -LiteralPath $releasePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($release.draft -or $release.tag_name -ne ('v' + $version)) { throw '공개 배포 버전을 확인하지 못했습니다.' }
    $exeAssets = @($release.assets | Where-Object { $_.name -eq $fileName })
    $manifestAssets = @($release.assets | Where-Object { $_.name -eq 'latest.json' })
    if ($exeAssets.Count -ne 1 -or $manifestAssets.Count -ne 1) { throw '공식 배포 파일을 찾지 못했습니다.' }
    $exeAsset = $exeAssets[0]; $manifestAsset = $manifestAssets[0]
    if ($exeAsset.state -ne 'uploaded' -or $exeAsset.size -ne $expectedSize -or $exeAsset.digest -ne ('sha256:' + $expectedHash) -or [long]$exeAsset.id -le 0 -or [long]$manifestAsset.id -le 0) { throw '배포 파일이 검증된 수정본과 다릅니다.' }
    Get-RecoveryFile ($api + 'assets/' + $manifestAsset.id + '?download=1') $manifestPath
    if ((Get-Item -LiteralPath $manifestPath).Length -gt 32768) { throw '서명 안내 파일이 너무 큽니다.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.version -ne $version -or $manifest.filename -ne $fileName -or $manifest.sha256 -ne $expectedHash -or $manifest.size -ne $expectedSize) { throw '서명된 배포 안내가 준비된 수정본과 다릅니다.' }
    Get-RecoveryFile ($api + 'assets/' + $exeAsset.id + '?download=1') $payload
    if ((Get-Item -LiteralPath $payload).Length -ne $expectedSize -or (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '다운로드 검증에 실패했습니다. 기존 프로그램은 유지합니다.' }
    $health = Start-Process -FilePath $payload -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
    if ($health.ExitCode -ne 0) { throw '수정본 실행 검사에 실패했습니다. 기존 프로그램은 유지합니다.' }
    $helper = Join-Path $cache 'recovery-helper.exe'
    Copy-Item -LiteralPath $target -Destination $helper
    $finished = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -ArgumentList '/d /c exit 0' -WindowStyle Hidden -Wait -PassThru
    $planPath = Join-Path $cache 'plan-recovery.json'
    $plan = @{parent_pid=$finished.Id;target=$target;staged=$payload;manifest=$manifest}
    [IO.File]::WriteAllText($planPath, ($plan | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
    Write-Host '기존 프로그램의 전자서명 검사로 검증한 뒤 교체합니다.'
    $install = Start-Process -FilePath $helper -ArgumentList @('--test-apply-update', ('"' + $planPath + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw ('서명 검사 또는 교체에 실패했습니다. 기록: ' + (Join-Path $cache 'last-result.json')) }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '설치 후 파일 검증에 실패했습니다.' }
}
$setupRoot = Join-Path $cache 'empty-setup'
New-Item -ItemType Directory -Path $setupRoot -Force | Out-Null
$previousAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Split-Path -Parent $dataPath
    $setup = Start-Process -FilePath $target -ArgumentList @('--prepare-install', ('"' + $setupRoot + '"')) -WindowStyle Hidden -Wait -PassThru
} finally { $env:LOCALAPPDATA = $previousAppData }
if ($setup.ExitCode -ne 0) { throw '교체는 완료했으나 자동 업데이트 연결 설정을 확인하지 못했습니다.' }
# Delete only this run's known temporary files; keep unexpected additions.
$programPath = [IO.Path]::GetFullPath((Split-Path -Parent $target))
$cachePath = [IO.Path]::GetFullPath($cache)
if (-not $cachePath.StartsWith(($programPath.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid cleanup path.' }
[IO.Directory]::Delete($setupRoot, $false)
foreach ($name in @('release.json','latest.json',$fileName,'recovery-helper.exe','last-result.json')) {
    $file = Join-Path $cachePath $name
    if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
}
try { [IO.Directory]::Delete($cachePath, $false) } catch { Write-Host '추가된 파일이 있어 복구 임시 폴더를 남겼습니다.' }
Write-Host ('복구 완료: v' + $version + '. 기존 설정과 접수 이력, 양식은 유지했습니다.') -ForegroundColor Green
if (-not $NoLaunch) { Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Normal }
