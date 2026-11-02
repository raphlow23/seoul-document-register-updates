"""Reject private documents/settings and their embedded constants before publishing an update EXE."""
import json
import marshal
from pathlib import Path
from types import CodeType


def check_code(code,forbidden):
    if isinstance(code,CodeType):
        for value in code.co_consts:check_code(value,forbidden)
    elif isinstance(code,(list,tuple,set,frozenset)):
        for value in code:check_code(value,forbidden)
    elif isinstance(code,(str,bytes)):
        text=code if isinstance(code,str) else code.decode("utf-8",errors="ignore")
        if any(value and value in text for value in forbidden):raise ValueError("공개 실행파일에 개인 설정 또는 업무 문서 내용이 포함되어 있습니다.")


def verify(exe,seed_path):
    from PyInstaller.archive.readers import CArchiveReader
    seed=json.loads(Path(seed_path).read_text(encoding="utf-8"))
    forbidden=[seed.get("root",""),seed.get("spreadsheet_id","")]
    archive=CArchiveReader(str(exe))
    checked=0
    for name,item in archive.toc.items():
        normalized=name.replace("\\","/").lower()
        if normalized=="certifi/cacert.pem":
            import certifi
            if archive.extract(name)!=Path(certifi.where()).read_bytes():raise ValueError("공개 CA 인증서 묶음이 설치 라이브러리와 다릅니다.")
            continue
        if normalized=="config.json" or "hwp-templates/" in normalized or normalized.endswith((".hwp",".hwpx",".dpapi",".pem","/config.json","initial-config.json")):
            raise ValueError("공개 실행파일에 비공개 데이터 파일이 들어 있습니다.")
        if item[-1] in ("s","m"):
            check_code(marshal.loads(archive.extract(name)),forbidden);checked+=1
        if name.endswith(".pyz"):
            embedded=archive.open_embedded_archive(name)
            for module in embedded.toc:
                check_code(embedded.extract(module),forbidden);checked+=1
    return {"checked_modules":checked,"private_documents_embedded":False,"private_defaults_embedded":False}
