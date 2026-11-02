# 설치 및 업데이트

[최신 배포 페이지](https://github.com/raphlow23/seoul-document-register-updates/releases/latest)에서 `SeoulDocumentRegister-Setup-버전.zip`을 다운로드하세요.

1. ZIP 전체를 폴더에 압축 해제합니다.
2. 실행 중인 이전 프로그램을 종료합니다.
3. `install.cmd`를 실행하고 바탕화면 바로가기로 시작합니다.

기존 버전을 삭제할 필요가 없습니다. 기존 설정과 등록 이력은 보존합니다. v1.x에서 온라인 업데이트 기능을 추가하려면 최초 한 번 새 설치본이 필요합니다. 이후 실행 시 GitHub에서 최신 버전을 확인하고 Ed25519 서명과 SHA-256을 검사하여 자동 업데이트합니다.

## 원본 한글 양식과 업무 설정

공개 설치본에는 원본 업무 문서, 개인 설정, 인증 정보가 없습니다. 별도로 받은 비공개 양식 보충 ZIP이 있다면 공개 설치 ZIP을 해제한 폴더에 함께 압축을 풀고 `install.cmd`를 실행하세요. 이미 설치한 개인 설정과 양식은 다시 넣지 않아도 됩니다.

## 프로그램 삭제

프로그램을 종료하고 `%LOCALAPPDATA%\SeoulDocumentRegister\Program` 폴더와 바탕화면 바로가기를 삭제합니다. 상위 `SeoulDocumentRegister` 폴더에는 설정·등록 이력이 있으므로 보관하세요.
