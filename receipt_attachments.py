"""Append source attachments without replacing receipt identity or calling a ledger."""
from copy import deepcopy
from pathlib import Path
from core import digest
from file_rollback import has_files,execute


def append(job,additions,config,store):
    additions=[source for source in additions if source['hash']!=job['hash']]
    known={f['hash'] for f in job['files']};extra=[]
    for source in additions:
        if all(file['hash'] in known for file in source['files']):continue
        if source.get('receipt_done') or source.get('sheet_attempted') or source.get('files_started') or source.get('state') in ('완료','묶음 처리 완료'):
            raise ValueError('이미 별도로 저장·접수한 공문은 첨부파일로 합칠 수 없습니다.')
        if source.get('parent') and source['parent']!=job['hash']:raise ValueError('다른 공문에 포함된 파일은 먼저 기존 묶음을 확인하세요.')
        for file in source['files']:
            if file['hash'] in known:continue
            if not Path(file['path']).is_file() or digest(file['path'])!=file['hash']:raise ValueError('추가할 원본 첨부파일이 없거나 변경되었습니다.')
            old=store.get(file['hash'])
            if old and old.get('parent') not in (None,job['hash'],source['hash']):raise ValueError('다른 묶음의 첨부파일은 보호합니다.')
            known.add(file['hash']);extra.append(deepcopy(file))
    if not extra:return job
    # Keep the original sheet transaction's marker set stable, including uncertain writes.
    if job.get('sheet_attempted') or job.get('receipt_done'):job.setdefault('receipt_marker_files',deepcopy(job['files']))
    if has_files(job,'receipt'):execute(job,config,store,'receipt')
    job['files'].extend(extra);job['ready']=False
    job['reason']=f"첨부파일 {len(extra)}개 추가 · 총 {len(job['files'])}개 · 기존 시트 등록·번호 유지"
    updates={job['hash']:job}
    for source in additions:
        updates[source['hash']]={**source,'parent':job['hash'],'state':'묶음에 포함','archive_alias':bool(source.get('archive_hash'))}
    for file in extra:
        old=updates.get(file['hash']) or store.get(file['hash']) or {}
        updates[file['hash']]={**old,'hash':file['hash'],'files':[file],'parent':job['hash'],'state':'묶음에 포함'}
    store.put_many(list(updates.values()))
    return job
