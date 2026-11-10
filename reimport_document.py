"""Start explicitly removed downloads again while preserving prior local records."""
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
    old=store.intake(content_hash)
    # The previous key and all its references remain immutable. A separate index
    # points subsequent downloads to this new case until it is removed again.
    key=hashlib.sha256(('intake:'+uuid.uuid4().hex).encode()).hexdigest()
    job.update(hash=key,content_hash=content_hash,reimport_id=uuid.uuid4().hex,reimported_from=old['hash'])
    store.put_intake(job,content_hash)
    return job,True
