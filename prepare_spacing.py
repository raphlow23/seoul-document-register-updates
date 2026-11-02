"""Prepare the independently signed model payload (build machine only)."""
from importlib.metadata import version
from pathlib import Path
import json
import zipfile
import kiwipiepy_model
from korean_spacing import MODEL_VERSION,MODEL_FILES
from updater import file_hash


def corresponding_source(base):
    import hashlib
    import requests
    target=Path(base)/"build-tools"/"kiwipiepy-0.20.4.tar.gz"
    if not target.is_file():
        response=requests.get("https://pypi.org/pypi/kiwipiepy/0.20.4/json",timeout=30);response.raise_for_status()
        source=next(f for f in response.json()["urls"] if f["packagetype"]=="sdist")
        response=requests.get(source["url"],timeout=60);response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest()!=source["digests"]["sha256"]:raise ValueError("Kiwi source download hash mismatch")
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(response.content)
    return target


def component(output):
    if version("kiwipiepy-model")!=MODEL_VERSION:raise ValueError("Install kiwipiepy-model=="+MODEL_VERSION)
    root=Path(kiwipiepy_model.__file__).parent
    info={"version":MODEL_VERSION,"files":{name:file_hash(root/name) for name in sorted(MODEL_FILES)}}
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        values={name:(root/name).read_bytes() for name in sorted(MODEL_FILES)}
        values["BUILDINFO.json"]=json.dumps(info,ensure_ascii=False,sort_keys=True).encode("utf-8")
        for name,data in values.items():
            entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(entry,data,compresslevel=6)
    return info
