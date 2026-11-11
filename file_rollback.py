"""Reversible removal of proven app-created files; never touches a Google ledger."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import json
import os
import re
from attachment_files import storage_files
from pathlib import Path
import uuid
from fs_paths import io_path,logical_path


def linked(path):
    path=io_path(path)
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def checked(path):
    path=logical_path(path)
    if not path.is_absolute():raise ValueError('되돌릴 경로가 절대 경로가 아닙니다.')
    if any(linked(p) for p in (path,*path.parents)):raise ValueError('연결·바로가기 경로는 자동으로 되돌리지 않습니다: '+str(path))
    return logical_path(path.resolve())


def track_root(job,path,scope):
    value={'path':str(Path(path).resolve()),'scope':scope}
    if value not in job.setdefault('storage_roots',[]):job['storage_roots'].append(value)


def track_file(job,path,hash_,scope):
    value={'path':str(Path(path).resolve()),'sha256':hash_,'scope':scope}
    files=job.setdefault('file_artifacts',[])
    old=next((f for f in files if f['path']==value['path']),None)
    if old and old!=value:raise ValueError('저장 파일 생성 기록이 다릅니다.')
    if not old:files.append(value)


def has_files(job,scope):
    scopes={'receipt','outgoing'} if scope=='receipt' else {'outgoing'}
    return bool(job.get('rollback_pending') or any(r['scope'] in scopes for r in job.get('storage_roots',[])) or
                any(f['scope'] in scopes for f in job.get('file_artifacts',[])) or
                (scope=='receipt' and (job.get('files_started') or job.get('destinations'))) or
                job.get('outgoing',{}).get('hwp_path') or (job.get('outgoing',{}).get('folder_plan') or {}).get('created'))


def _roots(job,config,scope):
    scopes={'receipt','outgoing'} if scope=='receipt' else {'outgoing'}
    candidates=[Path(r['path']) for r in job.get('storage_roots',[]) if r['scope'] in scopes]
    if scope=='receipt':
        for target in job.get('routing',{}).get('targets',[]):
            if target.get('storage_mode')=='year':continue
            if target.get('folder_name'):candidates.append(Path(config['root'])/target['relative']/target['folder_name'])
        # Older versions persisted destination files rather than absolute case-folder roots.
        for file in job.get('destinations',[]):
            path=checked(file)
            for parent in path.parents:
                if (parent/'.수신등록.json').is_file():candidates.append(parent);break
    out=job.get('outgoing',{});plan=out.get('folder_plan') or {}
    if plan.get('created') and plan.get('path'):candidates.append(Path(plan['path']))
    roots=[];allowed_dirs=set();markers={}
    from folder_naming import child_folders
    for candidate in candidates:
        path=checked(candidate)
        if not path.exists():continue
        if not path.is_dir():raise ValueError('공문폴더가 다른 파일로 바뀌었습니다: '+str(path))
        matched=False
        for name,owner,kind in (('.수신등록.json',job.get('id'),'receipt'),('.수발신폴더.json',out.get('id'),'outgoing')):
            marker=path/name
            if kind not in scopes or not marker.is_file():continue
            if linked(marker) or json.loads(marker.read_text(encoding='utf-8')).get('id')!=owner or not owner:raise ValueError('다른 문서의 폴더이므로 보호합니다: '+str(path))
            matched=True;markers[marker]=owner;allowed_dirs.add(path)
            setting=job.get('routing',{}) if kind=='receipt' else plan
            if kind=='receipt':
                target=next((t for t in setting.get('targets',[]) if t.get('folder_name') and checked(Path(config['root'])/t['relative']/t['folder_name'])==path),{})
                setting={**setting,'children':target.get('children',setting.get('children',[])),'child_order':target.get('child_order')}
            if setting.get('agency'):
                allowed_dirs.update(path/name for name in child_folders(setting['agency'],setting.get('children',[]),setting.get('child_order')).values())
        if not matched:raise ValueError('프로그램이 만든 공문폴더임을 확인하지 못했습니다: '+str(path))
        if path not in roots:roots.append(path)
    # Moving an outer owned folder also includes a nested outgoing folder.
    roots=[p for p in roots if not any(p!=q and p.is_relative_to(q) for q in roots)]
    return roots,allowed_dirs,markers


def inventory(path):
    from core import digest
    path=checked(path)
    if io_path(path).is_file():return {'files':{'':digest(path)},'dirs':[]}
    files={};dirs=['']
    def unreadable(error):raise error
    for base,children,names in os.walk(io_path(path),followlinks=False,onerror=unreadable):
        base=logical_path(base)
        for name in children:
            child=checked(base/name);dirs.append(str(child.relative_to(path)))
        for name in names:
            child=checked(base/name);files[str(child.relative_to(path))]=digest(child)
    return {'files':files,'dirs':sorted(dirs)}


def hwp_available(path):
    from hwp_documents import _probe_native_file
    try:_probe_native_file(io_path(path))
    except OSError as error:
        if getattr(error,'winerror',None) in (32,33):raise ValueError('HWP가 한글에서 열려 있습니다. Ctrl+S로 저장하고 한글 창을 닫은 뒤 이전 단계를 다시 눌러 주세요.') from error
        raise


def plan(job,config,scope,allow_edited_hwp=False):
    if scope not in ('receipt','outgoing'):raise ValueError('되돌릴 업무 종류가 올바르지 않습니다.')
    roots,dirs,markers=_roots(job,config,scope)
    scopes={'receipt','outgoing'} if scope=='receipt' else {'outgoing'}
    allowed={checked(f['path']):f['sha256'] for f in job.get('file_artifacts',[]) if f['scope'] in scopes}
    # Legacy receipt copies are provable only inside a case folder carrying this job's marker.
    from core import digest
    sources={Path(f['path']).name:f['hash'] for f in storage_files(job)}
    for path in job.get('destinations',[]) if scope=='receipt' else []:
        file=checked(path)
        if file in {checked(p) for p in job.get('receipt_shared_files',[])} and file not in allowed:continue
        if any(file.is_relative_to(root) for root in roots):
            matches=[f['hash'] for f in storage_files(job) if file.name in (Path(f['path']).name,Path(f['path']).stem+'_'+f['hash'][:12]+Path(f['path']).suffix)]
            if len(set(matches))==1:allowed[file]=matches[0]
        elif file.exists() and file not in allowed:raise ValueError('생성 기록이 없는 기존 공용 파일은 보호합니다: '+str(file))
    # Recover an interrupted copy in an owned folder even before destinations were persisted.
    for root in roots:
        for base,_,names in os.walk(root,followlinks=False):
            for name in names:
                file=checked(Path(base)/name)
                if scope=='receipt' and name in sources and file not in markers:allowed.setdefault(file,sources[name])
    for marker in markers:allowed[marker]=digest(marker)
    out=job.get('outgoing',{});edited_files=[]
    if out.get('hwp_path'):
        hwp=checked(out['hwp_path'])
        if hwp.exists():
            if not out.get('hwp_created_sha256'):raise ValueError('이전 버전에서 만든 HWP는 변경 여부를 확인할 수 없어 보호합니다: '+str(hwp))
            created=out['hwp_created_sha256']
            if not re.fullmatch('[0-9a-f]{64}',created) or (hwp in allowed and allowed[hwp]!=created):raise ValueError('HWP 생성 기록을 확인하지 못해 보호합니다: '+str(hwp))
            hwp_available(hwp)
            current=digest(hwp)
            allowed[hwp]=created
            if allow_edited_hwp and current!=created and hwp.suffix.lower()=='.hwp':
                # This exact path is the current generated output, not a received
                # attachment or an untracked file. Preserve its latest saved bytes.
                allowed[hwp]=current
                edited_files.append({'path':str(hwp),'created_sha256':created,'sha256':current})
    protected={checked(f['path']) for f in storage_files(job)}
    if any(p==root or p.is_relative_to(root) for p in protected for root in roots):raise ValueError('원본 첨부파일이 들어 있는 폴더는 되돌릴 수 없습니다.')
    targets=list(roots)
    for path in allowed:
        if path in markers or any(path.is_relative_to(root) for root in roots):continue
        if path.exists():targets.append(path)
    moves=[];token=uuid.uuid4().hex
    for path in targets:
        if path in protected:raise ValueError('다운로드 원본은 되돌리기 대상이 아닙니다.')
        before=inventory(path)
        for rel,hash_ in before['files'].items():
            file=path/rel if path.is_dir() else path
            if file not in allowed or allowed[file]!=hash_:raise ValueError('직접 추가·수정한 파일은 보호합니다. 해당 파일을 확인하세요: '+str(file))
        if path.is_dir():
            for rel in before['dirs']:
                if path/rel not in dirs:raise ValueError('직접 추가한 하위폴더는 보호합니다: '+str(path/rel))
        anchor=checked(config['root']) if path.is_relative_to(checked(config['root'])) else path.parent
        for owner in job.get('storage_roots',[]):
            owned=checked(owner['path'])
            if path.is_relative_to(owned) and owned.parent in anchor.parents:anchor=owned.parent
        from program_storage import temporary_root
        local_base=temporary_root(config,'수발신-되돌리기').resolve()
        slot=str(len(moves));backup=local_base/str(job['id'])/token/slot/path.name
        if not backup.resolve().is_relative_to(local_base):raise ValueError('복구 보관 경로가 올바르지 않습니다.')
        moves.append({'source':str(path),'backup':str(backup),'backup_root':str(anchor),'slot':slot,'inventory':before,'done':False,'local':True,'local_base':str(local_base)})
        for edited in edited_files:
            original=Path(edited['path'])
            if original==path:edited['backup']=str(backup)
            elif original.is_relative_to(path):edited['backup']=str(backup/original.relative_to(path))
    return {'scope':scope,'id':token,'moves':moves,'edited_files':edited_files,'started':datetime.now().isoformat(timespec='seconds')}


def execute(job,config,store,scope,allow_edited_hwp=False):
    from core import registration_lock
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',str(job.get('id',''))):raise ValueError('공문 파일 이력 ID가 올바르지 않습니다.')
    locks={checked(config['root'])}
    base=(job.get('outgoing',{}).get('folder_plan') or {}).get('base')
    if base:
        base=checked(base)
        if not base.is_relative_to(checked(config['root'])):locks.add(base)
    with ExitStack() as stack:
        for root in sorted(locks,key=str):stack.enter_context(registration_lock(root))
        from program_storage import temporary_root,copy_verified,remove_verified,subset
        store.put(job)
        refreshed=store.get(job['hash']);job.clear();job.update(refreshed)
        transaction=job.get('rollback_pending')
        if transaction and transaction['scope']!=scope:raise ValueError('시작한 파일 되돌리기를 먼저 마쳐 주세요.')
        if transaction:
            for move in transaction['moves']:
                if not move.get('local') and not Path(move['backup']).exists():
                    local_base=temporary_root(config,'수발신-되돌리기').resolve()
                    move.update(backup=str(local_base/str(job['id'])/transaction['id']/move['slot']/Path(move['source']).name),local=True,local_base=str(local_base))
            store.put(job)
        if not transaction:
            transaction=plan(job,config,scope,allow_edited_hwp=allow_edited_hwp);job['rollback_pending']=transaction;store.put(job)
        if not re.fullmatch(r'[0-9a-f]{32}',transaction['id']):raise ValueError('되돌리기 작업 ID가 올바르지 않습니다.')
        for edited in transaction.get('edited_files',[]):
            path=checked(edited['path'])
            if io_path(path).exists():hwp_available(path)
        # Preflight every remaining move before changing any source path.
        for index,move in enumerate(transaction['moves']):
            source,backup=checked(move['source']),checked(move['backup'])
            anchor=checked(move['backup_root'])
            if anchor not in source.parents or move['slot']!=str(index):raise ValueError('복구 보관 기준 경로가 올바르지 않습니다.')
            local_base=checked(move.get('local_base',str(temporary_root(config,'수발신-되돌리기'))))
            root=temporary_root(config,'수발신-되돌리기').resolve()
            if not move.get('local') or not (local_base==root or (local_base.parent==root and re.fullmatch(r'legacy-[0-9a-f]{32}',local_base.name))):raise ValueError('설치 폴더 내 보관 경로를 확인하지 못했습니다.')
            expected=local_base/str(job['id'])/transaction['id']/move['slot']/source.name
            if backup!=expected.resolve():raise ValueError('파일 되돌리기 보관 경로가 달라 중단합니다.')
            if not io_path(source).exists() and not io_path(backup).exists():raise ValueError('되돌릴 파일이 없습니다: '+str(source))
            if io_path(source).exists() and not subset(inventory(source),move['inventory']):raise ValueError('원본 파일이 변경되어 중단합니다: '+str(source))
            if io_path(backup).exists() and not subset(inventory(backup),move['inventory']):raise ValueError('보관 파일이 변경되어 중단합니다: '+str(backup))
        for move in transaction['moves']:
            source,backup=checked(move['source']),checked(move['backup'])
            copy_verified(source,backup,move['inventory'])
            move['copied']=True;store.put(job)
            remove_verified(source,move['inventory'])
            move['done']=True;store.put(job)
        scopes={'receipt','outgoing'} if scope=='receipt' else {'outgoing'}
        job['storage_roots']=[r for r in job.get('storage_roots',[]) if r['scope'] not in scopes]
        job['file_artifacts']=[f for f in job.get('file_artifacts',[]) if f['scope'] not in scopes]
        if scope=='receipt':
            from core import digest
            for file in storage_files(job):
                original=Path(file['path'])
                if io_path(original).is_file() and digest(original)==file['hash']:continue
                copies=[Path(m['backup'])/rel if m['inventory']['dirs'] else Path(m['backup']) for m in transaction['moves'] for rel,hash_ in m['inventory']['files'].items() if hash_==file['hash']]
                if copies:
                    file.setdefault('original_source_path',file['path']);file['path']=str(copies[0])
            job.update(files_started=False,files_rolled_back=True,destinations=[],receipt_shared_files=[])
            job['state']='접수 유지·파일 다시 저장' if job.get('receipt_done') else '저장 취소·등록 결과 확인' if job.get('sheet_attempted') else '확인 필요'
            for target in job.get('routing',{}).get('targets',[]):
                for key in ('folder_number','folder_name'):target.pop(key,None)
        out=job.get('outgoing',{})
        if out:
            out['files_rolled_back']=True
            for key in ('hwp_path','hwp_saved_number','hwp_created_sha256'):out.pop(key,None)
            folder=out.get('folder_plan') or {}
            if folder.get('base'):out['save_folder']=folder['base']
            for key in ('folder_number','folder_name','created','path','save_path'):folder.pop(key,None)
        transaction['finished']=datetime.now().isoformat(timespec='seconds')
        job['last_rollback_edited_files']=deepcopy(transaction.get('edited_files',[]))
        job.setdefault('rollback_history',[]).append(deepcopy(transaction));job['rollback_history']=job['rollback_history'][-20:]
        job.pop('rollback_pending',None)
        job['reason']='파일·폴더 저장 취소 · 구글시트 등록과 번호 유지'
        from core import attachment_aliases
        store.put_many([job,*attachment_aliases(job,store,'묶음 처리 완료' if job.get('receipt_done') else '묶음에 포함')])
        from program_storage import clean_copy_parts
        clean_copy_parts(config)
    return job
