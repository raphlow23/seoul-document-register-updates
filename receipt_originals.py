"""Keep watched originals recoverable until a confirmed receipt is undone."""
from pathlib import Path
import os
import uuid

from attachment_files import file_identity,storage_files
from core import digest
from file_rollback import checked
from fs_paths import io_path
from program_storage import temporary_root


def signature(path):
    identity = file_identity(path)
    if not identity[1]:
        raise ValueError(f'파일 식별 번호를 확인할 수 없습니다: {path}')
    return identity


def prepare(job, config, store):
    """Capture identity before the first copy, while all inputs are still available."""
    if 'receipt_originals' in job or not config.get('downloads'):
        return
    watched = Path(config['downloads']).absolute()
    entries = []
    token = uuid.uuid4().hex
    for item in storage_files(job):
        path = Path(item['path']).absolute()
        if path.parent != watched:
            continue
        path = checked(path)
        identity = signature(path)
        if (item.get('intake_identity',identity)!=identity or digest(path) != item['hash']):
            raise ValueError(f'접수할 원본이 변경되었습니다: {path}')
        base = checked(temporary_root(config, '수신-원본보관').absolute())
        existing = next(parent for parent in (base, *base.parents) if parent.exists())
        # A move on the original volume preserves file identity even across a crash.
        if existing.stat().st_dev != identity[0]:
            base = checked(watched/'.수발신-원본보관')
        backup = base/token/str(len(entries))/path.name
        entries.append(dict(original=str(path), backup=str(backup), sha256=item['hash'], identity=identity))
    if entries:
        job['receipt_originals'] = dict(token=token, watched=str(watched), entries=entries,
                                       state='ready', copies={})
        store.put(job)


def record_copy(job, target, checksum, owned):
    journal = job.get('receipt_originals')
    if journal:
        path = str(checked(target))
        record = journal['copies'].get(path)
        current = dict(sha256=checksum, identity=signature(target), owned=owned)
        if record:
            if record['identity'] != current['identity'] or record['sha256'] != checksum:
                raise ValueError(f'이미 저장한 사본이 변경되었습니다: {target}')
        else:
            journal['copies'][path] = current


def staged_primary(job,source,target,checksum,checkpoint):
    from additional_storage import copy_staged,_same_file
    from file_rollback import track_file
    journal=job['receipt_originals']
    pending=journal.get('pending_copy')
    if pending and (pending['source'],pending['target'],pending['sha256'])!=(str(source),str(target),checksum):
        raise ValueError('중단된 접수 사본을 같은 위치에서 재시도하거나 되돌려 주세요: '+pending['target'])
    if not pending:
        pending=dict(source=str(source),target=str(target),sha256=checksum,
                     stage=str(target.with_name('.copy-'+uuid.uuid4().hex+'.part')))
        journal['pending_copy']=pending
        checkpoint()
    pending_artifacts(pending)
    try:
        copy_staged(pending,checkpoint)
    except Exception:
        stage=checked(pending['stage'])
        if _same_file(stage,pending.get('identity')):
            pending['partial_sha256']=digest(stage)
            pending['partial_identity']=signature(stage)
            checkpoint()
        raise
    track_file(job,target,checksum,'receipt')
    record_copy(job,target,checksum,True)
    del journal['pending_copy']
    checkpoint()


def pending_artifacts(item):
    from additional_storage import _same_file
    if not item:return {}
    result={}
    for key in ('target','stage'):
        path=checked(item[key])
        if not io_path(path).exists():continue
        value=digest(path)
        if (not _same_file(path,item.get('identity')) or
                (value!=item['sha256'] and not (key=='stage' and value==item.get('partial_sha256')
                                                 and signature(path)==item.get('partial_identity')))):
            raise ValueError(f'중단된 사본의 소유권이나 내용이 달라 보존합니다: {path}')
        result[path]=value
    return result


def verify_copies(job):
    journal = job['receipt_originals']
    if journal.get('pending_copy'):
        raise ValueError('중단된 접수 사본을 먼저 확인하세요.')
    paths = {str(checked(p)) for p in job.get('destinations', [])}
    if not paths or paths != set(journal['copies']):
        raise ValueError('접수 사본 기록이 완전하지 않습니다. 모든 저장 위치를 먼저 확인하세요.')
    if paths.intersection(e['original'] for e in journal['entries']):
        raise ValueError('원본과 사본의 저장 위치가 같습니다. 서로 다른 위치를 선택하세요.')
    extra = {p for group in job.get('additional_saves', []) if group['scope']=='receipt' for p in group['files']}
    hashes = {a['path']:a['sha256'] for a in job.get('file_artifacts', []) if a['scope']=='receipt'}
    from additional_storage import rollback_entries
    rollback_entries(job, {'receipt'})
    for path in paths | extra:
        record = journal['copies'].get(path)
        expected = record['sha256'] if record else hashes.get(path)
        if (not io_path(checked(path)).is_file() or digest(path) != expected
                or (record and signature(path) != record['identity'])):
            raise ValueError(f'저장 사본이 누락되거나 변경되었습니다. 원본을 보존합니다: {path}')


def locations(journal, config):
    token = journal['token']
    if uuid.UUID(hex=token).hex != token:
        raise ValueError('원본 보관 작업 번호를 확인하세요.')
    watched = checked(journal['watched'])
    for index, entry in enumerate(journal['entries']):
        source, backup = checked(entry['original']), checked(entry['backup'])
        bases = (checked(temporary_root(config,'수신-원본보관').absolute()), watched/'.수발신-원본보관')
        if source.parent != watched or backup not in {base/token/str(index)/source.name for base in bases}:
            raise ValueError(f'원본 보관 경로가 변경되었습니다: {source}')
        yield entry, source, backup


def check_rollback_copies(job,store):
    owned = {checked(f['path']) for f in job.get('file_artifacts',[]) if f['scope']=='receipt'}
    for item in job.get('file_artifacts',[]):
        if item['scope']=='receipt' and item.get('identity') and io_path(item['path']).exists():
            if signature(checked(item['path']))!=item['identity']:
                raise ValueError(f"교체되거나 수정된 접수 사본을 보존합니다: {item['path']}")
    for other in store.all():
        if other.get('id')==job.get('id') or other.get('parent') or other.get('files_rolled_back'):
            continue
        paths = list(other.get('destinations',[]))
        paths.extend(f['path'] for f in storage_files(other))
        paths.extend(a['path'] for a in other.get('file_artifacts',[]))
        for raw in paths:
            if Path(raw) in owned:
                raise ValueError(f'다른 접수에서도 사용하는 사본을 보존합니다: {raw}')


def move_original(entry,source,target):
    """Move a recognized download without replacing another file."""
    if target is None:raise ValueError('원본 보관 위치를 확인하세요.')
    change_verified_file(entry,source,target)


def change_verified_file(entry,source,target):
    """Hold the verified Windows handle through rename, or removal of a backed-up copy."""
    if os.name!='nt':
        from additional_storage import _publish
        if signature(source)!=entry['identity'] or digest(source)!=entry['sha256']:
            raise ValueError(f'변경된 파일을 보존합니다: {source}')
        if target is None:io_path(source).unlink()
        else:_publish(source,target)
        return
    import ctypes
    from ctypes import wintypes
    import hashlib
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    open_file=kernel.CreateFileW
    open_file.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                        wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    open_file.restype=wintypes.HANDLE
    handle=open_file(str(io_path(source)),0x80000000|0x10000,1,None,3,0,None)  # GENERIC_READ | DELETE
    if handle==ctypes.c_void_p(-1).value:
        raise OSError(f'원본을 이동할 수 없습니다. 사용 중인 파일과 권한을 확인하세요: {source} ({ctypes.get_last_error()})')
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except OSError:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    with os.fdopen(fd,'rb') as stream:
        if signature(source)!=entry['identity'] or hashlib.file_digest(stream,'sha256').hexdigest()!=entry['sha256']:
            raise ValueError(f'변경된 파일을 보존합니다: {source}')
        set_info=kernel.SetFileInformationByHandle
        set_info.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        set_info.restype=wintypes.BOOL
        if target is None:
            disposition=ctypes.c_ubyte(1)
            if not set_info(handle,4,ctypes.byref(disposition),ctypes.sizeof(disposition)):
                raise OSError(f'보관을 마친 사본을 되돌리지 못했습니다: {source} ({ctypes.get_last_error()})')
            return
        class RenameInfo(ctypes.Structure):
            _fields_=[('replace',wintypes.DWORD),('root',wintypes.HANDLE),
                      ('length',wintypes.DWORD),('name',wintypes.WCHAR*1)]
        name=str(io_path(target)).encode('utf-16-le')
        buffer=ctypes.create_string_buffer(ctypes.sizeof(RenameInfo)+len(name))
        RenameInfo.from_buffer(buffer).length=len(name)  # replace=0; refuse any existing destination.
        ctypes.memmove(ctypes.addressof(buffer)+RenameInfo.name.offset,name,len(name))
        if not set_info(handle,3,buffer,len(buffer)):  # FileRenameInfo
            raise OSError(f'파일을 이동하지 못했습니다: {source} → {target} ({ctypes.get_last_error()})')


def transfer(job, config, store, *, restore=False):
    from core import attachment_aliases
    journal = job.get('receipt_originals')
    if not journal:
        return
    if not restore:
        if (not job.get('receipt_done') or job.get('files_rolled_back') or job.get('rollback_pending')
                or job.get('registration_cancel_pending') or job.get('additional_save_pending')
                or journal['state']=='restoring'):
            raise ValueError('저장 또는 되돌리기가 진행 중입니다. 완료 후 원본을 보관하세요.')
        verify_copies(job)
        sources = {Path(entry['original']) for entry in journal['entries']}
        for other in store.all():
            if other.get('id')==job.get('id') or other.get('parent') or other.get('files_rolled_back'):
                continue
            for file in storage_files(other):
                path=Path(file['path'])
                if path in sources and (not file.get('intake_identity') or
                                        (path.is_file() and signature(path)==file['intake_identity'])):
                    raise ValueError(f"다른 접수에서도 사용하는 원본을 보존합니다: {file['path']}")
    moves = []
    for entry, original, backup in locations(journal,config):
        source, target = (backup, original) if restore else (original, backup)
        existing = [p for p in (source,target) if io_path(p).exists()]
        if (len(existing)!=1 or signature(existing[0])!=entry['identity']
                or digest(existing[0])!=entry['sha256']):
            raise ValueError(f'원본 복구 위치 또는 보관 파일이 충돌·변경되었습니다. 두 위치를 확인하세요: {original} / {backup}')
        if existing[0]==source:
            moves.append((entry,source,target))
    if restore and journal['state']=='ready' and not moves:
        return
    journal['state']='restoring' if restore else 'moving'
    store.put(job)
    for entry, source, target in moves:
        io_path(target.parent).mkdir(parents=True,exist_ok=True)
        if signature(source)!=entry['identity'] or io_path(target).exists():
            raise ValueError(f'파일이 변경되어 이동을 중단했습니다: {source} / {target}')
        move_original(entry,source,target)
        if signature(target)!=entry['identity'] or digest(target)!=entry['sha256']:
            raise ValueError(f'이동 결과를 확인할 수 없습니다. 파일을 보존했습니다: {target}')
    replacements = {e['backup' if restore else 'original']:e['original' if restore else 'backup']
                    for e in journal['entries']}
    for item in job.get('files', []) + job.get('archive_files', []):
        item['path'] = replacements.get(item['path'],item['path'])
    if job.get('source_archive') in replacements:
        job['source_archive']=replacements[job['source_archive']]
    journal['state']='ready' if restore else 'archived'
    store.put_many([job,*attachment_aliases(job,store,'묶음 처리 완료' if job.get('receipt_done') else '묶음에 포함')])
    if restore:
        from program_storage import prune_empty
        for _,_,backup in locations(journal,config):
            prune_empty(backup.parent,backup.parents[2])
