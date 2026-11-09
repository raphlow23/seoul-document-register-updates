"""Create a local outgoing draft without inventing a receipt or attachment."""
from datetime import date
import uuid
from outgoing import defaults


def create(config,store):
    key='outgoing-'+uuid.uuid4().hex
    job={'id':key,'hash':key,'standalone_outgoing':True,'title':'','date':date.today().isoformat(),
         'state':'발신 작성 중','reason':'수신 없이 새 발신 작성','files':[],'text':'',
         'category':'','recipient':config.get('recipient','')}
    job['outgoing']=defaults(job,config)
    job['outgoing'].update(enabled=True,title_same=False)
    store.put(job)
    return job
