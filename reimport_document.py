"""Start explicitly removed downloads again while preserving prior local records."""
from copy import deepcopy
import hashlib,uuid
from pathlib import Path


def removed(job):
    return bool(job.get('list_hidden') or job.get('history_hidden') or job.get('state')=='제외')


def create(path,store,content_hash,progress=None):
    from core import inspect_document
    path=Path(path)
    if path.suffix.lower()=='.zip':
        from zip_intake import ingest_zip
        job,_=ingest_zip(path,store,content_hash,progress,persist=False)
    else:
        text,metadata,info=inspect_document(path,progress)
        job={'hash':content_hash,'id':uuid.uuid4().hex,'files':[{'path':str(path),'hash':content_hash}],
             'text':text,'text_file_hash':content_hash,'ocr':metadata,'state':'확인 필요','number':'',
             'destinations':[],'receipt_type':'공문',**info}
    old=store.get(content_hash)
    archive_key=hashlib.sha256(('history:'+uuid.uuid4().hex).encode()).hexdigest()
    preserved=deepcopy(old);preserved.update(hash=archive_key,list_hidden=True,reimport_archived=True,original_content_hash=content_hash)
    updates=[preserved]
    if not old.get('parent'):
        for alias in store.all():
            if alias.get('parent')==content_hash:
                alias['parent']=archive_key;updates.append(alias)
    job.update(reimport_id=uuid.uuid4().hex,reimported_from=archive_key)
    store.put_many([*updates,job])
    return job,True
