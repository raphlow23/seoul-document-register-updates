"""Append source attachments without replacing receipt identity or calling a ledger."""
from copy import deepcopy
from pathlib import Path
from core import digest
from file_rollback import has_files,execute


def load_sources(paths,job,store,progress=None):
    from core import ingest
    from zip_intake import unpack,discard_extraction,NoDocuments
    from attachment_files import ZIP_DOCUMENTS
    sources=[]
    for path in paths:
        path=Path(path)
        if path.suffix.lower()!='.zip':
            source,_=ingest(path,store,progress=progress);sources.append(source);continue
        hash_=digest(path);old=store.intake(hash_)
        if old and (old.get('receipt_done') or old.get('sheet_attempted') or old.get('files_started') or old.get('parent')) and (old.get('parent') or old['hash'])!=job['hash']:
            raise ValueError('다른 공문에 이미 포함된 ZIP입니다. 해당 공문의 이력을 먼저 확인하세요.')
        cache=Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent/'archive-cache'
        files=unpack(path,cache,progress)
        documents=[file for file in files if file.suffix.lower() in ZIP_DOCUMENTS]
        if not documents:
            discard_extraction(files,cache)
            raise NoDocuments('ZIP 안에 문서·도면·오피스 첨부파일이 없습니다.')
        sources.append({'hash':hash_,'archive_hash':hash_,'source_archive':str(path),'archive_files':[{'path':str(path),'hash':hash_}],'files':[{'path':str(file),'hash':digest(file)} for file in documents],'state':'확인 필요','_zip_cache':str(cache),'_zip_paths':[str(file) for file in files]})
    return sources


def discard_sources(sources,store):
    from zip_intake import discard_extraction
    # Only discard this import's new, otherwise unreferenced cache directories.
    used={file['path'] for entry in store.all() for file in entry.get('files',[])}
    for source in sources:
        paths=source.get('_zip_paths',[])
        if paths and not any(path in used for path in paths):
            try:discard_extraction(paths,source['_zip_cache'])
            except OSError:pass


def append(job,additions,config,store):
    additions=[source for source in additions if source['hash']!=job['hash']]
    known={f['hash'] for f in job['files']};extra=[]
    archive_known={f['hash'] for f in job.get('archive_files',[])};archive_extra=[]
    foreign_aliases=set()
    for source in additions:
        new_archives=[f for f in source.get('archive_files',[]) if f['hash'] not in archive_known]
        if all(file['hash'] in known for file in source['files']) and not new_archives:continue
        if source.get('receipt_done') or source.get('sheet_attempted') or source.get('files_started') or source.get('state') in ('완료','묶음 처리 완료'):
            raise ValueError('이미 별도로 저장·접수한 공문은 첨부파일로 합칠 수 없습니다.')
        if source.get('parent') and source['parent']!=job['hash']:raise ValueError('다른 공문에 포함된 파일은 먼저 기존 묶음을 확인하세요.')
        for file in new_archives:
            if not Path(file['path']).is_file() or digest(file['path'])!=file['hash']:raise ValueError('추가할 원본 ZIP이 없거나 변경되었습니다.')
            archive_known.add(file['hash']);archive_extra.append(deepcopy(file))
        for file in source['files']:
            if file['hash'] in known:continue
            if not Path(file['path']).is_file() or digest(file['path'])!=file['hash']:raise ValueError('추가할 원본 첨부파일이 없거나 변경되었습니다.')
            old=store.get(file['hash'])
            if source.get('archive_hash') and old and old.get('parent') not in (job['hash'],source['hash']) and old.get('id')!=job.get('id') and (old.get('parent') or any(old.get(k) for k in ('receipt_done','sheet_attempted','files_started')) or old.get('state') in ('완료','묶음 처리 완료')):
                foreign_aliases.add(file['hash'])
            if not source.get('archive_hash'):
                if old and old.get('parent')!=job['hash'] and (old.get('receipt_done') or old.get('sheet_attempted') or old.get('files_started') or old.get('state') in ('완료','묶음 처리 완료')):raise ValueError('다른 공문에서 이미 처리한 첨부파일입니다.')
                if old and old.get('parent') not in (None,job['hash'],source['hash']):raise ValueError('다른 묶음의 첨부파일은 보호합니다.')
            known.add(file['hash']);extra.append(deepcopy(file))
    if not extra and not archive_extra:return job
    # Keep the original sheet transaction's marker set stable, including uncertain writes.
    if job.get('sheet_attempted') or job.get('receipt_done'):job.setdefault('receipt_marker_files',deepcopy(job['files']))
    if has_files(job,'receipt'):execute(job,config,store,'receipt')
    job['files'].extend(extra);job['ready']=False
    if archive_extra:job.setdefault('archive_files',[]).extend(archive_extra)
    job['reason']=f"첨부파일 {len(extra)}개 추가 · 총 {len(job['files'])}개 · 기존 시트 등록·번호 유지"
    updates={job['hash']:job}
    for source in additions:
        updates[source['hash']]={**job,**{key:value for key,value in source.items() if not key.startswith('_zip_')},'parent':job['hash'],'state':'묶음에 포함','archive_alias':bool(source.get('archive_hash'))}
    for file in extra:
        if file['hash'] in foreign_aliases:continue
        old=updates.get(file['hash']) or store.get(file['hash']) or {}
        if old and old.get('parent') not in (None,job['hash']) and old.get('id')!=job.get('id'):continue
        if old.get('receipt_done') and old.get('parent')!=job['hash'] and old.get('id')!=job.get('id'):continue
        updates[file['hash']]={**job,**old,'hash':file['hash'],'files':[file],'parent':job['hash'],'state':'묶음에 포함'}
    store.put_many(list(updates.values()))
    return job
