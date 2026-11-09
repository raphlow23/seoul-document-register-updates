"""Temporary files beside the installed EXE, with verified cross-volume transfers."""
import hashlib,json,os,re,shutil,sys,uuid
from pathlib import Path
from fs_paths import io_path,logical_path


def program_root(config):
    if config.get('_program_dir'):return Path(config['_program_dir']).resolve()
    if getattr(sys,'frozen',False):return Path(sys.executable).resolve().parent
    if config.get('_auth_data_dir'):return (Path(config['_auth_data_dir'])/'Program').resolve()
    return Path(__file__).resolve().parent


def temporary_root(config,kind):return program_root(config)/'임시보관'/kind


def save_json(path,value):
    path=Path(path);io_path(path.parent).mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with io_path(temporary).open('w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(io_path(temporary),io_path(path))
    finally:io_path(temporary).unlink(missing_ok=True)


def subset(actual,expected):
    return all(expected['files'].get(k)==v for k,v in actual['files'].items()) and set(actual['dirs']).issubset(expected['dirs'])


def validate_inventory(expected):
    for relative in [*expected['dirs'],*expected['files']]:
        value=Path(relative)
        if value.is_absolute() or '..' in value.parts:raise ValueError('보관할 파일의 상대 경로가 올바르지 않습니다.')
    if any(not re.fullmatch('[0-9a-f]{64}',v) for v in expected['files'].values()):raise ValueError('보관 파일 해시가 올바르지 않습니다.')


def copy_verified(source,target,expected):
    from file_rollback import checked,inventory
    from core import digest
    validate_inventory(expected);source,target=checked(source),checked(target)
    if io_path(target).exists() and not subset(inventory(target),expected):raise ValueError('임시 보관 파일이 변경되어 원본을 보호합니다: '+str(target))
    if io_path(source).exists() and not subset(inventory(source),expected):raise ValueError('원본 파일이 변경되어 이동을 중단합니다: '+str(source))
    if expected['dirs']:
        for relative in expected['dirs']:io_path(checked(target/relative)).mkdir(parents=True,exist_ok=True)
    else:io_path(target.parent).mkdir(parents=True,exist_ok=True)
    for relative,hash_ in expected['files'].items():
        src=source/relative if expected['dirs'] else source
        dst=target/relative if expected['dirs'] else target
        if io_path(dst).is_file() and digest(dst)==hash_:continue
        if not io_path(src).is_file() or digest(src)!=hash_:raise ValueError('복구할 원본을 확인하지 못했습니다: '+str(src))
        part=target.parent/('.copy-'+uuid.uuid4().hex+'.part')
        try:
            shutil.copy2(io_path(src),io_path(part))
            if digest(part)!=hash_ or digest(src)!=hash_:raise ValueError('파일 복사 검증에 실패했습니다. 원본은 유지합니다.')
            os.replace(io_path(part),io_path(dst))
        finally:io_path(part).unlink(missing_ok=True)
    if inventory(target)!=expected:raise ValueError('보관된 파일 구성이 원본과 다릅니다. 원본은 유지합니다.')


def remove_verified(path,expected):
    from file_rollback import checked,inventory
    from core import digest
    validate_inventory(expected);path=checked(path)
    if not io_path(path).exists():return
    current=inventory(path)
    if not subset(current,expected):raise ValueError('추가·수정된 파일을 보호하여 자동 정리를 중단합니다: '+str(path))
    for relative,hash_ in current['files'].items():
        file=checked(path/relative if current['dirs'] else path)
        if digest(file)!=hash_:raise ValueError('정리 중 변경된 파일을 보호합니다: '+str(file))
        io_path(file).unlink()
    for relative in sorted(current['dirs'],key=lambda x:len(Path(x).parts),reverse=True):io_path(checked(path/relative)).rmdir()


def prune_empty(path,stop):
    path,stop=Path(path).resolve(),Path(stop).resolve()
    while path!=stop and path.is_relative_to(stop):
        try:io_path(path).rmdir()
        except OSError:break
        path=path.parent


def migrate_rollbacks(config,store):
    from file_rollback import checked,inventory
    jobs=store.all();legacy=set()
    if config.get('root'):legacy.add(checked(config['root'])/'.수발신-되돌리기')
    for job in jobs:
        for tx in [*job.get('rollback_history',[]),*([job['rollback_pending']] if job.get('rollback_pending') else [])]:
            for move in tx['moves']:
                for parent in Path(move['backup']).parents:
                    if parent.name=='.수발신-되돌리기':legacy.add(checked(parent));break
    migrations=temporary_root(config,'이전폴더이동');records=[]
    if migrations.is_dir():records=[(p,json.loads(p.read_text(encoding='utf-8'))) for p in migrations.glob('*.json')]
    tracked={entry['source'] for _,entry in records}
    for source in legacy:
        if not io_path(source).is_dir() or str(source) in tracked:continue
        record={'source':str(source),'target':str(temporary_root(config,'수발신-되돌리기')/('legacy-'+uuid.uuid4().hex)),'inventory':inventory(source)}
        path=migrations/(hashlib.sha256(str(source).encode()).hexdigest()+'.json');save_json(path,record);records.append((path,record))
    for path,entry in records:
        source,target=checked(entry['source']),checked(entry['target']);base=temporary_root(config,'수발신-되돌리기').resolve()
        if source.name!='.수발신-되돌리기' or not target.is_relative_to(base) or target.parent!=base or not target.name.startswith('legacy-'):raise ValueError('이전 보관 폴더 이동 경로가 다릅니다.')
        copy_verified(source,target,entry['inventory'])
        def replace(value):
            if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
            if isinstance(value,list):return [replace(v) for v in value]
            if isinstance(value,str):
                candidate=Path(value)
                if candidate.is_absolute() and candidate.is_relative_to(source):return str(target/candidate.relative_to(source))
            return value
        jobs=[replace(job) for job in store.all()]
        for job in jobs:
            for tx in [*job.get('rollback_history',[]),*([job['rollback_pending']] if job.get('rollback_pending') else [])]:
                for move in tx['moves']:
                    if Path(move['backup']).is_relative_to(target):move.update(local=True,local_base=str(target))
        store.put_many(jobs)  # Publish replacement references before removing any shared backup.
        remove_verified(source,entry['inventory']);path.unlink();prune_empty(path.parent,program_root(config))


def cleanup_rollbacks(job,config,store):
    from file_rollback import checked,inventory
    if job.get('rollback_pending'):return
    roots=[temporary_root(config,'수발신-되돌리기').resolve()]
    jobs=[job,*[other for other in store.all() if other['hash']!=job['hash']]]
    references=[Path(f['path']).resolve() for current in jobs for f in [*current.get('files',[]),*current.get('archive_files',[])] if f.get('path')]
    changed=False
    for tx in job.get('rollback_history',[]):
        waiting=job.get('files_rolled_back') if tx['scope']=='receipt' else job.get('outgoing',{}).get('files_rolled_back')
        if waiting:continue
        for move in tx['moves']:
            if move.get('cleaned') or not move.get('local'):continue
            backup=checked(move['backup'])
            if not any(backup.is_relative_to(root) for root in roots):continue
            if any(p==backup or p.is_relative_to(backup) for p in references):continue
            if io_path(backup).exists():
                if inventory(backup)!=move['inventory']:continue
                remove_verified(backup,move['inventory']);prune_empty(backup.parent,program_root(config))
            move['cleaned']=True;changed=True
    if changed:store.put(job)


def maintain(config,store):
    if not config.get('root'):return '공유 실적 폴더를 설정한 뒤 임시자료 정리를 실행하세요.'
    from core import registration_lock
    from browser_journal import Journal
    with registration_lock(config['root']):
        Journal(config).migrate();migrate_rollbacks(config,store)
        for job in store.all():cleanup_rollbacks(job,config,store)
        clean_copy_parts(config)
    return '이전 공유폴더 자료를 설치 폴더로 옮기고, 정상 완료가 확인된 임시자료를 정리했습니다.'


def clean_copy_parts(config):
    from file_rollback import checked
    root=(program_root(config)/'임시보관').resolve()
    if not root.is_dir():return
    for disk in io_path(root).rglob('.copy-*.part'):
        path=logical_path(disk)
        if re.fullmatch(r'\.copy-[0-9a-f]{32}\.part',path.name):
            io_path(checked(path)).unlink();prune_empty(path.parent,program_root(config))
