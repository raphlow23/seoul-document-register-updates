"""One-time private installation data; these documents/settings are never GitHub release assets."""
import json
import os
from pathlib import Path
import shutil
import uuid
from release_info import UPDATE_BASE_URL


def prepare(package,data):
    package,data=Path(package),Path(data)
    data.mkdir(parents=True,exist_ok=True)
    path=data/"config.json"
    config=json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    seed=package/"initial-config.json"
    if seed.is_file():
        values=json.loads(seed.read_text(encoding="utf-8-sig"))
        for key in ("root","spreadsheet_id"):
            if not config.get(key) and values.get(key):config[key]=values[key]
    config.update(update_url=UPDATE_BASE_URL,auto_update=True)
    source=package/"local-templates"
    if source.is_dir():
        target=data/"hwp-templates";target.mkdir(exist_ok=True)
        for name in ("catalog.json",*[f"template{i}.hwp" for i in range(1,7)]):
            src,dst=source/name,target/name
            if not src.is_file():raise ValueError("기본 양식 설치 파일이 없습니다: "+name)
            if not dst.exists():
                try:
                    with src.open("rb") as incoming,dst.open("xb") as output:shutil.copyfileobj(incoming,output)
                except FileExistsError:pass
    temporary=data/("config-"+uuid.uuid4().hex+".tmp")
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temporary,path)
    return config
