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


def edit_copy(key,config):
    """Edit a private copy so cancelling an edit cannot damage the installed form."""
    from program_storage import temporary_root
    item=docs.style(key)
    if Path(item['file']).name!=item['file']:raise ValueError('양식 파일 경로를 확인하세요.')
    source=docs.template_root()/item['file']
    if not source.is_file():raise ValueError('원본 HWP를 먼저 연결하세요.')
    return copy_for_edit(source,config)


def copy_for_edit(source,config):
    from program_storage import temporary_root
    source=Path(source)
    if source.stat().st_size>100*1024*1024:raise ValueError('양식 파일이 너무 큽니다.')
    target=temporary_root(config,'HWP-양식수정')/(uuid.uuid4().hex+'.hwp')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as dst:dst.write(source.read_bytes())
    return target


def add(source,settings,storage=None,replace_id=None):
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
    if replace_id=='8' and family!='공문':raise ValueError('완화공문은 공문 유형으로 등록하세요.')
    folder=str(settings.get("folder","")).strip()
    if not Path(folder).is_absolute():raise ValueError("기본 저장 폴더를 절대 경로로 선택하세요.")
    prefix=str(settings.get("prefix","")).strip()
    suffix=str(settings.get("suffix","")).strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]',prefix+suffix):raise ValueError("파일명 앞·뒤 표시에는 파일명 금지 문자를 사용할 수 없습니다.")
    root=Path(storage) if storage is not None else Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"SeoulDocumentRegister"/"hwp-templates"
    root.mkdir(parents=True,exist_ok=True)
    cp=root/"catalog.json"
    catalog=json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else docs.catalog()
    if replace_id=='8' and not any(x['id']=='8' for x in catalog):catalog.append(docs.relief_template())
    previous=next((x for x in catalog if x['id']==replace_id),None)
    if replace_id and previous is None:raise ValueError('변경할 양식을 찾지 못했습니다.')
    if any(item['label']==label and item['id']!=replace_id for item in catalog):raise ValueError("같은 이름의 양식이 있습니다. 다른 이름을 입력하세요.")
    # An upgrade must not make previously bundled forms disappear when the private library is created.
    oldroot=docs.template_root()
    for existing in catalog:
        if Path(existing["file"]).name!=existing["file"]:raise ValueError("양식 파일 경로를 확인하세요.")
        original=oldroot/existing["file"];target=root/existing["file"]
        if original.is_file() and not target.exists():
            with target.open("xb") as dst:dst.write(original.read_bytes())
    file_key='user_'+uuid.uuid4().hex;key=replace_id or file_key
    target=root/(file_key+".hwp")
    item={"id":key,"label":label,"file":target.name,"family":family,"folder":folder,"prefix":prefix,
          "spacing":" " if settings.get("spacing",True) else "","suffix":suffix,"receiver":anchors["receiver"],
          "default_receiver":str(settings.get("default_receiver","")).strip(),"old_sender":anchors["sender"],
          "old_number":anchors["number"],"old_date":anchors["date"],"title_fragments":[anchors["title"]] if anchors["title"] else [],
          "blank_body":True,"metadata_only":True,"body_start":"","signature":"",
          "date_pattern":"({year2:02d}. {month}. {day})","number_pattern":number_pattern(original_number),
          "original_sha256":original_hash,"source_sha256":hashlib.sha256(raw).hexdigest()}
    if previous and previous.get('role'):item['role']=previous['role']
    if previous and settings.get('_edit_existing'):
        fragments=previous.get('title_fragments',[])
        if len(fragments)>1 and anchors['title']==fragments[0] and all(docs._compact(text).count(docs._compact(fragment))==1 for fragment in fragments):item['title_fragments']=fragments.copy()
        if anchors['date']==previous.get('old_date'):
            item['date_pattern']=previous.get('date_pattern') or ('({year2:02d}. {month}. {day})' if previous.get('blank_body') else '({year}. {month:02d}. {day:02d}.)')
        if anchors['number']==previous.get('old_number') and previous.get('number_pattern'):item['number_pattern']=previous['number_pattern']
    temporary=root/("catalog-"+uuid.uuid4().hex+".tmp")
    try:
        with target.open("xb") as dst:dst.write(raw)
        validate_anchors(inspect(target),anchors)
        updated=[item if x['id']==key else x for x in catalog] if previous else [*catalog,item]
        temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(temporary,cp)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:temporary.unlink(missing_ok=True)
    docs.template_body.cache_clear()
    return item


def _publish(path,items):
    temporary=path.with_name('catalog-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def _private_catalog(storage):
    root=Path(storage);root.mkdir(parents=True,exist_ok=True)
    items=docs.catalog(root) if (root/'catalog.json').is_file() else docs.catalog()
    oldroot=docs.template_root()
    for item in items:
        if Path(item['file']).name!=item['file']:raise ValueError('양식 파일 경로를 확인하세요.')
        source=oldroot/item['file'];target=root/item['file']
        if source.is_file() and not target.exists():
            with target.open('xb') as dst:dst.write(source.read_bytes())
    return root,items


def rename(key,label,storage):
    label=label.strip()
    if not label:raise ValueError('양식 이름을 입력하세요.')
    root,items=_private_catalog(storage)
    if not any(x['id']==key for x in items):raise ValueError('변경할 양식을 찾지 못했습니다.')
    if any(x['label']==label and x['id']!=key for x in items):raise ValueError('같은 이름의 양식이 있습니다.')
    items=[{**x,'label':label} if x['id']==key else x for x in items]
    _publish(root/'catalog.json',items);docs.template_body.cache_clear()


def remove(key,storage):
    root,items=_private_catalog(storage);selected=next((x for x in items if x['id']==key),None)
    if selected is None:raise ValueError('삭제할 양식을 찾지 못했습니다.')
    path=root/'catalog-retired.json';retired=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
    # Preserve retired metadata/files for documents already using this template ID.
    _publish(path,[*[x for x in retired if x['id']!=key],selected])
    _publish(root/'catalog.json',[x for x in items if x['id']!=key]);docs.template_body.cache_clear()
