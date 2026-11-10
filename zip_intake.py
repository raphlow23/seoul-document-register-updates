"""Bounded ZIP extraction into private app storage; one archive is one reviewable attachment group."""
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import uuid
import zipfile

DOCUMENTS={".pdf",".hwp",".hwpx"}
MAX_ARCHIVE=200*1024*1024
MAX_MEMBER=100*1024*1024
MAX_TOTAL=500*1024*1024
MAX_ENTRIES=1000
MAX_DEPTH=3


class NoDocuments(ValueError):
    pass


def discard_extraction(paths,cache):
    cache=Path(cache).resolve()
    roots={Path(p).resolve().relative_to(cache).parts[0] for p in paths}
    if len(roots)!=1:raise ValueError("ZIP 캐시 정리 경로를 확인하지 못했습니다.")
    name=roots.pop();folder=(cache/name).resolve()
    if not re.fullmatch(r"bundle-[0-9a-f]{32}",name) or not folder.is_relative_to(cache) or folder==cache:
        raise ValueError("ZIP 캐시 정리 경로가 저장 범위를 벗어났습니다.")
    shutil.rmtree(folder)


def member_name(info):
    if "\x00" in info.orig_filename:raise ValueError("ZIP 안의 파일 이름에 허용할 수 없는 문자가 있습니다.")
    name=info.filename
    if not info.flag_bits & 0x800:
        try:name=name.encode("cp437").decode("cp949")
        except (UnicodeError,LookupError):pass
    name=name.replace("\\","/")
    path=PureWindowsPath(name)
    parts=name.rstrip("/").split("/")
    if path.drive or path.is_absolute() or not parts or any(p in ("",".","..") for p in parts):
        raise ValueError("ZIP 안에 허용할 수 없는 파일 경로가 있습니다.")
    for p in parts:
        if re.search(r'[<>:"|?*\x00-\x1f]',p) or p.endswith(("."," ")) or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?",p,re.I):
            raise ValueError("ZIP 안에 Windows에서 사용할 수 없는 파일 이름이 있습니다.")
        if len(p.encode("utf-16-le"))>480:raise ValueError("ZIP 안의 파일 이름이 너무 깁니다.")
    return parts


def unpack(path,cache,progress=None):
    cache=Path(cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    if Path(path).stat().st_size>MAX_ARCHIVE:raise ValueError("ZIP은 압축 파일 크기 200MB까지 처리합니다.")
    # A unique persistent directory keeps pending work usable after Downloads is cleaned or the app updates.
    work=(cache/("bundle-"+uuid.uuid4().hex)).resolve()
    if not work.is_relative_to(cache):raise ValueError("ZIP 임시 저장 경로를 확인하지 못했습니다.")
    work.mkdir();complete=False
    budget={"entries":0,"bytes":0};files=[]
    def expand(archive,base,depth):
        if depth>MAX_DEPTH:raise ValueError("ZIP 안의 ZIP은 최대 3단계까지 처리합니다.")
        with zipfile.ZipFile(archive) as source:
            seen=set()
            for info in source.infolist():
                budget["entries"]+=1
                if budget["entries"]>MAX_ENTRIES:raise ValueError("ZIP 안의 항목 수가 1,000개를 넘습니다.")
                parts=member_name(info)
                if parts[0]=="__MACOSX" or parts[-1] in (".DS_Store","Thumbs.db"):continue
                identity="/".join(parts).casefold()
                if identity in seen:raise ValueError("ZIP 안에 같은 경로의 파일이 중복되어 있습니다.")
                seen.add(identity)
                mode=info.external_attr>>16
                if stat.S_ISLNK(mode):raise ValueError("링크가 포함된 ZIP은 자동으로 풀지 않습니다.")
                if info.flag_bits & 1:raise ValueError("암호 ZIP은 직접 압축을 풀고 파일을 추가해 주세요.")
                target=base.joinpath(*parts)
                if not target.resolve().is_relative_to(work):raise ValueError("ZIP 경로가 저장 범위를 벗어났습니다.")
                if info.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                if info.file_size>MAX_MEMBER:raise ValueError("ZIP 안의 개별 파일은 100MB까지 처리합니다.")
                if budget["bytes"]+info.file_size>MAX_TOTAL:raise ValueError("ZIP의 전체 압축 해제 크기가 500MB를 넘습니다.")
                target.parent.mkdir(parents=True,exist_ok=True)
                count=0
                with source.open(info) as src,target.open("xb") as dst:
                    while chunk:=src.read(1024*1024):
                        count+=len(chunk);budget["bytes"]+=len(chunk)
                        if count>MAX_MEMBER or budget["bytes"]>MAX_TOTAL:raise ValueError("ZIP 압축 해제 용량 제한을 넘었습니다.")
                        dst.write(chunk)
                if progress:progress("ZIP 첨부 확인: "+target.name)
                if target.suffix.lower()==".zip":
                    expand(target,target.with_name(target.name+"_내용"),depth+1)
                else:files.append(target)
    try:
        expand(path,work/"files",0)
        if not files:raise NoDocuments("ZIP 안에 처리할 첨부파일이 없어 목록에서 제외합니다.")
        # Keep the unique extraction directory in place. Windows preview/antivirus can
        # briefly hold children open, which makes renaming their parent fail with WinError 5.
        complete=True
        return files
    except (zipfile.BadZipFile,NotImplementedError,RuntimeError) as error:
        raise ValueError("ZIP을 읽지 못했습니다. 다운로드 완료 여부와 암호·압축 방식을 확인해 주세요.") from error
    finally:
        if not complete:
            try:discard_extraction([work],cache)
            except OSError:pass  # Preserve the original extraction error if a scanner holds a file.


def ingest_zip(path,store,hash_,progress=None,persist=True):
    from core import digest,inspect_document,attachment_aliases
    cache=Path(store.db.execute("PRAGMA database_list").fetchone()[2]).parent/"archive-cache"
    paths=unpack(path,cache,progress)
    from attachment_files import ZIP_DOCUMENTS,title_priority
    documents=[p for p in paths if p.suffix.lower() in ZIP_DOCUMENTS]
    if not documents:
        discard_extraction(paths,cache)
        raise NoDocuments('ZIP 안에 문서·도면·오피스 첨부파일이 없어 목록에서 제외합니다.')
    files=[]
    for p in sorted(documents,key=lambda p:(title_priority({'path':str(p)}),str(p))):
        value={"path":str(p),"hash":digest(p)}
        if value["hash"] not in {f["hash"] for f in files}:files.append(value)
    primary=next((f for f in files if Path(f["path"]).suffix.lower() in DOCUMENTS),files[0])
    text,metadata,info=inspect_document(primary['path'],progress)
    job={"text":text,"ocr":metadata,"number":"","destinations":[],"receipt_type":"공문",**info}
    job.update(hash=hash_,id=uuid.uuid4().hex,files=files,archive_hash=hash_,source_archive=str(path),
               archive_files=[{'path':str(path),'hash':hash_}],
               text_file_hash=primary["hash"],state="확인 필요",ready=False,
               reason=f"ZIP 첨부 {len(files)}개 · 제목 출처 파일과 저장 위치를 선택해 주세요.")
    job.pop("parent",None)
    if persist:
        store.put(job)
        store.put_many(attachment_aliases(job,store,'묶음에 포함'))
    return job,True
