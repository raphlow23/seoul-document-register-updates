"""Plan and reserve outgoing case folders independently of Google ledger numbers."""
from pathlib import Path
import json
import string
from folder_naming import folder_name,child_folders,next_sequence

PLAN_FIELDS=("base","template","folder_title","agency","children","child_order","save_in","date")


def receipt_case_folders(job,config):
    if not job.get('receipt_done'):return []
    candidates=[Path(r['path']) for r in job.get('storage_roots',[]) if r.get('scope')=='receipt']
    for target in job.get('routing',{}).get('targets',[]):
        if target.get('folder_name') and config.get('root'):candidates.append(Path(config['root'])/target['relative']/target['folder_name'])
    for value in job.get('destinations',[]):
        for parent in Path(value).parents:
            if (parent/'.수신등록.json').is_file():candidates.append(parent);break
    result=[]
    for folder in candidates:
        try:
            folder=folder.resolve();marker=json.loads((folder/'.수신등록.json').read_text(encoding='utf-8'))
            if marker.get('id')==job.get('id') and folder not in result:result.append(folder)
        except (OSError,ValueError):continue
    return result


def receipt_send_folders(job,config):
    result=[]
    for folder in receipt_case_folders(job,config):
        try:children=sorted(folder.iterdir(),key=lambda p:p.name)
        except OSError:continue
        for child in children:
            if child.name.split('.',1)[-1]=='센터발송' and child.is_dir() and child.resolve().parent==folder:
                result.append(str(child))
    return result


def validate(plan,out):
    base=Path(plan.get("base",""))
    if not base.is_absolute() or not base.is_dir():raise ValueError("발신 공문폴더를 만들 상위 저장 위치에 접근할 수 없습니다.")
    children=child_folders(plan["agency"],plan.get("children",[]),plan.get('child_order'))
    selected=plan.get("save_in","outgoing")
    if selected!="root" and selected not in children:raise ValueError("HWP를 넣을 내부폴더를 선택하세요.")
    title=plan.get("folder_title") or out["title"]
    folder_name(plan["template"],title,plan["date"],plan.get("folder_number"))
    return base.resolve(),children,title


def prepare(job,config,store):
    out=job["outgoing"];plan=out.get("folder_plan")
    if not plan or out.get('folder_mode')=='direct':return Path(out["save_folder"])
    base,children,title=validate(plan,out)
    from core import registration_lock
    root=Path(config.get("root",str(base))).resolve()
    lock=root if root.is_dir() and base.is_relative_to(root) else base
    with registration_lock(lock):
        if not plan.get("folder_name"):
            has_number=any(f=="number" for _,f,_,_ in string.Formatter().parse(plan["template"]))
            plan["folder_number"]=next_sequence(base,plan["template"]) if has_number else None
            plan["folder_name"]=folder_name(plan["template"],title,plan["date"],plan["folder_number"])
            store.put(job)
        expected=folder_name(plan["template"],title,plan["date"],plan.get("folder_number"))
        if plan["folder_name"]!=expected:raise ValueError("저장 중인 발신 폴더 이름을 변경할 수 없습니다.")
        folder=(base/expected).resolve()
        if folder.parent!=base:raise ValueError("발신 폴더 경로를 확인하세요.")
        marker=folder/".수발신폴더.json"
        if folder.exists():
            if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("id")!=out["id"]:
                raise ValueError("동일한 이름의 다른 폴더가 있습니다. 기존 폴더는 변경하지 않았습니다.")
        else:
            folder.mkdir()
            with marker.open("x",encoding="utf-8") as stream:json.dump({"id":out["id"],"title":out["title"],"folder_title":title},stream,ensure_ascii=False)
            import os
            if os.name=="nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(marker),2)
        for name in children.values():
            child=(folder/name).resolve()
            if child.parent!=folder:raise ValueError("내부폴더 경로가 공문폴더 밖을 가리킵니다.")
            child.mkdir(exist_ok=True)
        save=folder/children[plan["save_in"]] if plan["save_in"]!="root" else folder
        plan.update(created=True,path=str(folder),save_path=str(save))
        from file_rollback import track_root
        track_root(job,folder,'outgoing')
        out["save_folder"]=str(save);store.put(job)
        return save
