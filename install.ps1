$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$installRoot = Join-Path $env:LOCALAPPDATA 'SeoulDocumentRegister\Program'
$sourceExe = Join-Path $packageRoot 'SeoulDocumentRegister.exe'
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
    throw 'SeoulDocumentRegister.exe is missing. Extract the entire ZIP first.'
}
$running = Get-Process -Name 'SeoulDocumentRegister' -ErrorAction SilentlyContinue
if ($running) { throw 'Close SeoulDocumentRegister before installing or updating.' }
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Copy-Item -LiteralPath $sourceExe -Destination (Join-Path $installRoot 'SeoulDocumentRegister.exe') -Force
$setupArguments = @('--prepare-install', ('"' + $packageRoot + '"'))
$setupProcess = Start-Process -FilePath (Join-Path $installRoot 'SeoulDocumentRegister.exe') -ArgumentList $setupArguments -WindowStyle Hidden -Wait -PassThru
if ($setupProcess.ExitCode -ne 0) { throw '기존 설정 보존 및 온라인 업데이트 연결 설정을 완료하지 못했습니다.' }
foreach ($fileName in @('사용설명서.html', 'THIRD_PARTY_NOTICES.txt')) {
    $sourceFile = Join-Path $packageRoot $fileName
    if (Test-Path -LiteralPath $sourceFile) {
        Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $installRoot $fileName) -Force
    }
}
$licenseSource = Join-Path $packageRoot 'licenses'
if (Test-Path -LiteralPath $licenseSource -PathType Container) {
    Copy-Item -LiteralPath $licenseSource -Destination $installRoot -Recurse -Force
}
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut((Join-Path $desktopPath '서울센터 공문 수신등록.lnk'))
$shortcut.TargetPath = Join-Path $installRoot 'SeoulDocumentRegister.exe'
$shortcut.WorkingDirectory = $installRoot
$shortcut.Description = '다운로드한 공문 분류, 저장 및 수신등록'
$shortcut.Save()
$spacingMessage = ''
try {
    $modelFeed = Join-Path $packageRoot '업데이트배포'
    $modelArguments = @('--prepare-spacing-model', ('"' + $modelFeed + '"'))
    $modelProcess = Start-Process -FilePath (Join-Path $installRoot 'SeoulDocumentRegister.exe') -ArgumentList $modelArguments -WindowStyle Hidden -Wait -PassThru
    if ($modelProcess.ExitCode -ne 0) { throw '한국어 띄어쓰기 모델 설치를 완료하지 못했습니다.' }
} catch {
    $spacingMessage = "`n띄어쓰기 준비: " + $_.Exception.Message
}
$updateMessage = "`n앞으로 실행할 때 GitHub에서 새 버전을 내려받아 자동 업데이트합니다. 공문 양식과 기존 설정은 이 PC에 보관됩니다."
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(('설치했습니다. 바탕화면의 서울센터 공문 수신등록을 실행해 주세요.' + $updateMessage + $spacingMessage), '설치 완료') | Out-Null
