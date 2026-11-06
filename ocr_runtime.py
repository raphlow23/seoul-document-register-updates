"""Install authenticated native OCR files once, outside the one-file application's startup path."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
import zipfile

_config={}
_lock=threading.RLock()


def configure(config):
    global _config
    _config=dict(config)


def data_path(config=None):
    return Path((config if config is not None else _config).get('_auth_data_dir') or Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SeoulDocumentRegister').resolve()


@lru_cache(maxsize=8)
def valid(folder):
    from ocr_runtime_info import FILES
    from updater import file_hash
    try:return all((folder/name).is_file() and not (folder/name).is_symlink() and file_hash(folder/name)==sha for name,sha in FILES.items())
    except OSError:return False


def installed(config=None):
    from ocr_runtime_info import RUNTIME_ID,FILES
    if not RUNTIME_ID or not FILES:return None
    root=data_path(config)/'ocr-runtime'
    try:
        name=json.loads((root/(RUNTIME_ID+'.json')).read_text(encoding='utf-8'))['folder']
        if re.fullmatch(re.escape(RUNTIME_ID)+r'-[0-9a-f]{32}',name):
            folder=root/name
            if folder.resolve().parent==root.resolve() and valid(folder):return folder
    except (OSError,ValueError,KeyError,TypeError):pass
    return None


def install_archive(source,config=None):
    from ocr_runtime_info import FILES,RUNTIME_ID
    if not RUNTIME_ID or not FILES:raise ValueError('OCR 빌드 정보를 준비하지 못했습니다.')
    root=data_path(config)/'ocr-runtime';root.mkdir(parents=True,exist_ok=True);root=root.resolve()
    folder=(root/(RUNTIME_ID+'-'+uuid.uuid4().hex)).resolve()
    if folder.parent!=root:raise ValueError('OCR 설치 경로가 올바르지 않습니다.')
    folder.mkdir()
    try:
        with zipfile.ZipFile(source) as archive:
            entries=archive.infolist()
            if len(entries)!=len(FILES) or set(archive.namelist())!=set(FILES) or sum(i.file_size for i in entries)>300*1024*1024:raise ValueError('OCR 구성 파일 목록이 올바르지 않습니다.')
            for name in FILES:
                target=(folder/name).resolve()
                if not target.is_relative_to(folder):raise ValueError('OCR 파일 경로가 올바르지 않습니다.')
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(name) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
        if not valid(folder):raise ValueError('OCR 구성 파일의 해시를 확인하지 못했습니다.')
        temporary=root/('active-'+uuid.uuid4().hex+'.json')
        temporary.write_text(json.dumps({'folder':folder.name}),encoding='utf-8')
        os.replace(temporary,root/(RUNTIME_ID+'.json'))
        return folder
    except Exception:
        if folder.parent==root and folder.is_dir():shutil.rmtree(folder,ignore_errors=True)
        valid.cache_clear()
        raise


def ensure(config=None,archive=None):
    config=dict(_config if config is None else config)
    with _lock:
        ready=installed(config)
        if ready:return ready
        if archive is None:
            from updater import stage_component
            from release_info import UPDATE_BASE_URL
            source=config.get('ocr_source') or config.get('update_url') or config.get('update_folder') or UPDATE_BASE_URL
            archive=stage_component(source,data_path(config),'ocr_runtime')
        return install_archive(archive,config)
