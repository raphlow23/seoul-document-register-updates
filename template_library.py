"""Private, user-managed HWP forms. Original files never enter the public update package."""
from pathlib import Path
import hashlib
import json
import os
import re
import uuid
import tempfile

import hwp_documents as docs

TOKENS={"number":"{{발신번호}}","date":"{{발신일}}","title":"{{제목}}","receiver":"{{수신처}}","sender":"{{담당자}}"}


def filename_hints(name):
    match=re.fullmatch(r"(.*?)\d{1,6}\.(\s*)\d{2}\.\d{2}\.\d{2}\.\s*(.*)",Path(name).stem)
    if not match:return {}
    suffix=re.search(r"(\([^()]*(?:발송|제출|회신)\))$",match[3])
    return {"prefix":match[1],"spacing":bool(match[2]),"suffix":suffix[1] if suffix else ""}


def number_pattern(anchor):
    match=re.fullmatch(r"(.*?)(20\d{2})(\s*[-－]\s*)(?:\d{1,6}|\{\{발신번호\}\})",anchor)
    if match:
        prefix=match[1].replace("{","{{").replace("}","}}")
        return prefix+"{year}"+match[3]+"{number:03d}"
    return "서울센터 {year}-{number:03d}"


def inspect(path):
    import olefile
    from core import extract
    path=Path(path)
    if path.suffix.lower()!=".hwp" or not path.is_file():raise ValueError("원본 .hwp 양식 파일을 선택하세요.")
    if path.stat().st_size>100*1024*1024:raise ValueError("양식은 100MB 이하 파일을 사용하세요.")
    if not olefile.isOleFile(path):raise ValueError("이 파일은 지원되는 HWP 5 문서가 아닙니다.")
    text=extract(path)
    if not text.strip():raise ValueError("문구를 읽지 못했습니다. 암호·배포용 문서를 해제한 편집용 HWP를 사용하세요.")
    return text


def suggestions(text):
    result={key:token if token in text else "" for key,token in TOKENS.items()}
    if result["number"]:
        match=re.search(r"서울\s*센터\s*(?:20\d{2}\s*[-－]\s*)?\{\{발신번호\}\}",text)
        if match:result["number"]=match[0]
    if result["date"]:
        match=re.search(r"\(\s*\{\{발신일\}\}\s*\)",text)
        if match:result["date"]=match[0]
    if not result["number"]:
        match=re.search(r"서울\s*센터\s*20\d{2}\s*[-－]\s*\d{1,6}",text)
        if not match:match=re.search(r"(?<!\d)20\d{2}\s*[-－]\s*\d{1,6}(?!\d)",text)
        if match:result["number"]=match[0]
    if not result["date"]:
        match=re.search(r"\(\s*(?:20)?\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?\s*\)",text)
        if match:result["date"]=match[0]
    for key,label in (("title","제\\s*목"),("receiver","수\\s*신")):
        if not result[key]:
            match=re.search(r"(?m)^[ \t]*"+label+r"[ \t]*[:：]?[ \t]*(?:\r?\n[ \t]*)*([^\n]+)",text)
            if match:result[key]=match[1].strip()
    return result


def validate_anchors(text,anchors):
    compact=docs._compact(text)
    for key in ("number","date"):
        if not anchors.get(key,"").strip():raise ValueError(("시행번호" if key=="number" else "시행일")+" 위치의 현재 문구를 지정하세요.")
    chosen=[]
    for key,value in anchors.items():
        if not value.strip():continue
        if any(c in value for c in "\r\n"):raise ValueError("자동 입력 위치는 한 문단 안의 문구를 선택하세요.")
        token=docs._compact(value)
        if compact.count(token)!=1:raise ValueError("지정 문구는 문서에 한 번만 있어야 합니다: "+value)
        if any(token in other or other in token for other in chosen):raise ValueError("번호·날짜·제목의 지정 문구가 겹칩니다. 서로 다른 부분을 선택하세요.")
        chosen.append(token)


def add(source,settings,storage=None):
    """Validate before copying, then atomically publish the catalog. Never overwrite a form."""
    source=Path(source)
    text=inspect(source)
    raw=source.read_bytes()
    if len(raw)>100*1024*1024:raise ValueError("양식 파일이 변경되었습니다. 다시 선택하세요.")
    anchors={key:str(settings.get(key,"")).strip() for key in TOKENS}
    original_number=anchors['number'];original_hash=hashlib.sha256(raw).hexdigest()
    if settings.get('_source_sha256') and settings['_source_sha256']!=original_hash:raise ValueError('미리보기 이후 원본 HWP가 변경되었습니다. 파일을 다시 선택해 주세요.')
    if any(value and docs._compact(text).count(docs._compact(value))>1 for value in anchors.values()):
        from hwp_anchor_slots import prepare
        with tempfile.TemporaryDirectory(prefix='sdr-form-') as temporary:
            prepared=Path(temporary)/'selected-slots.hwp'
            anchors=prepare(source,prepared,anchors,settings.get('_occurrences',{}),text)
            raw=prepared.read_bytes();text=inspect(prepared)
    validate_anchors(text,anchors)
    label=str(settings.get("label","")).strip()
    family=settings.get("family","공문")
    if not label:raise ValueError("양식 이름을 입력하세요.")
    if family not in ("공문","업무연락","처리지침"):raise ValueError("공문 종류를 선택하세요.")
    folder=str(settings.get("folder","")).strip()
    if not Path(folder).is_absolute():raise ValueError("기본 저장 폴더를 절대 경로로 선택하세요.")
    prefix=str(settings.get("prefix","")).strip()
    suffix=str(settings.get("suffix","")).strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]',prefix+suffix):raise ValueError("파일명 앞·뒤 표시에는 파일명 금지 문자를 사용할 수 없습니다.")
    root=Path(storage) if storage is not None else Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"SeoulDocumentRegister"/"hwp-templates"
    root.mkdir(parents=True,exist_ok=True)
    cp=root/"catalog.json"
    catalog=json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else docs.catalog()
    if any(item["label"]==label for item in catalog):raise ValueError("같은 이름의 양식이 있습니다. 다른 이름을 입력하세요.")
    # An upgrade must not make previously bundled forms disappear when the private library is created.
    oldroot=docs.template_root()
    for existing in catalog:
        if Path(existing["file"]).name!=existing["file"]:raise ValueError("양식 파일 경로를 확인하세요.")
        original=oldroot/existing["file"];target=root/existing["file"]
        if original.is_file() and not target.exists():
            with target.open("xb") as dst:dst.write(original.read_bytes())
    key="user_"+uuid.uuid4().hex
    target=root/(key+".hwp")
    item={"id":key,"label":label,"file":target.name,"family":family,"folder":folder,"prefix":prefix,
          "spacing":" " if settings.get("spacing",True) else "","suffix":suffix,"receiver":anchors["receiver"],
          "default_receiver":str(settings.get("default_receiver","")).strip(),"old_sender":anchors["sender"],
          "old_number":anchors["number"],"old_date":anchors["date"],"title_fragments":[anchors["title"]] if anchors["title"] else [],
          "blank_body":True,"metadata_only":True,"body_start":"","signature":"",
          "date_pattern":"({year2:02d}. {month}. {day})","number_pattern":number_pattern(original_number),
          "original_sha256":original_hash,"source_sha256":hashlib.sha256(raw).hexdigest()}
    temporary=root/("catalog-"+uuid.uuid4().hex+".tmp")
    try:
        with target.open("xb") as dst:dst.write(raw)
        validate_anchors(inspect(target),anchors)
        temporary.write_text(json.dumps([*catalog,item],ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(temporary,cp)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:temporary.unlink(missing_ok=True)
    docs.template_body.cache_clear()
    return item
