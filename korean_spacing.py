"""Offline, whitespace-only Korean correction; signed model data stays outside the update EXE."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import zipfile

MODEL_VERSION="0.20.0"
COMPONENT_NAME=f"SpacingModel-{MODEL_VERSION}.zip"
MODEL_FILES={"combiningRule.txt","default.dict","extract.mdl","multi.dict","sj.knlm","sj.morph","skipbigram.mdl","typo.dict"}
TERMS=("보건복지부","장애인권익지원과","편의시설","중앙센터","기초센터","광역센터","서울센터","한국지체장애인협회","장애인편의증진기술지원센터","현장조사표","사용승인","웹하드")


@lru_cache(maxsize=8)
def valid_model(folder):
    try:
        info=json.loads((folder/"BUILDINFO.json").read_text(encoding="utf-8"))
        if info["version"]!=MODEL_VERSION or set(info["files"])!=MODEL_FILES:return False
        from updater import file_hash
        return all((folder/name).is_file() and file_hash(folder/name)==sha for name,sha in info["files"].items())
    except (OSError,ValueError,KeyError,TypeError):return False


def model_path(config):
    data=Path(config.get("_auth_data_dir") or Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"SeoulDocumentRegister")
    root=(data/"spacing-model").resolve();root.mkdir(parents=True,exist_ok=True)
    try:
        active=json.loads((root/"active.json").read_text(encoding="utf-8"))["folder"]
        if re.fullmatch(re.escape(MODEL_VERSION)+r"-[0-9a-f]{32}",active) and valid_model(root/active):return root/active
    except (OSError,ValueError,KeyError,TypeError):pass
    from release_info import UPDATE_BASE_URL,VERSION
    from updater import stage_component
    feed=config.get("model_source") or config.get("update_url") or config.get("update_folder") or UPDATE_BASE_URL
    if str(feed).startswith("https://"):feed=str(feed).replace("/releases/latest/download","/releases/download/v"+VERSION)
    try:
        source=stage_component(feed,data,"spacing_model")
        if source.name!=COMPONENT_NAME:raise ValueError("한국어 모델 버전이 올바르지 않습니다.")
    except (OSError,KeyError) as error:
        raise ValueError("띄어쓰기 모델을 준비하지 못했습니다. 새 설치본의 install.cmd를 실행하거나 공유 업데이트 폴더 연결을 확인해 주세요.") from error
    final=(root/(MODEL_VERSION+'-'+uuid.uuid4().hex)).resolve()
    if not final.is_relative_to(root) or final==root:raise ValueError('모델 설치 위치가 올바르지 않습니다.')
    final.mkdir()
    try:
        with zipfile.ZipFile(source) as z:
            names=z.namelist()
            if len(names)!=len(MODEL_FILES)+1 or set(names)!=MODEL_FILES|{"BUILDINFO.json"} or sum(i.file_size for i in z.infolist())>100*1024*1024:
                raise ValueError("한국어 모델 압축 구성이 올바르지 않습니다.")
            for name in names:
                with z.open(name) as src,(final/name).open("xb") as dst:
                    import shutil
                    shutil.copyfileobj(src,dst)
        if not valid_model(final):raise ValueError("한국어 모델 압축 해제 검증에 실패했습니다.")
    except Exception:
        import shutil
        if final.resolve().is_relative_to(root) and final!=root:shutil.rmtree(final,ignore_errors=True)
        raise
    pointer=root/("active-"+uuid.uuid4().hex+".json")
    pointer.write_text(json.dumps({"folder":final.name}),encoding="utf-8")
    os.replace(pointer,root/"active.json")
    return final


@lru_cache(maxsize=2)
def engine(path):
    from kiwipiepy import Kiwi
    kiwi=Kiwi(num_workers=1,model_path=str(path),model_type="knlm")
    for term in TERMS:kiwi.add_user_word(term,"NNP",10)
    return kiwi


def correct(text,config):
    kiwi=engine(str(model_path(config)))
    result=kiwi.space(text,reset_whitespace=True)
    # This feature can alter spaces only. Never rewrite OCR letters, numbers or punctuation.
    if re.sub(r"\s","",text)!=re.sub(r"\s","",result):
        return text
    # Keep date/number runs contiguous without changing any digit or punctuation.
    result=re.sub(r"(?<=\d)\s*([.,:/-])\s*(?=\d)",r"\1",result)
    result=re.sub(r"(?<=[가-힣])\s*(-)\s*(?=\d)",r"\1",result)
    return result
