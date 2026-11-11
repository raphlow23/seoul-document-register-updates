"""Undo only verified owned ledger cells and generated files, retaining rows and numbering formulas."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import re,uuid
from browser_journal import Journal
from sheet_connections import record,locked,chosen


def columns(scope,family):
    if scope=='receipt':return [1,4,5,6,7]
    if scope=='outgoing':return [1,4,5,6,7] if family!='공문' else [1,2,3,4,7,8,9,10]
    return list(range(1,11))


def markers(job,scope):
    if scope=='receipt':
        from core import receipt_markers
        return receipt_markers(job)
    return [('SDO:' if scope=='outgoing' else 'SDS:')+job['outgoing']['id']]


def owner(config,target):
    config={**config,'spreadsheet_id':target['sid'],'tab':target['tab'],'_target_tab':target['tab'],'_outgoing_family':target['family'],'layout':target.get('layout')}
    if config.get('auth_mode','browser')!='browser':raise ValueError('등록 취소는 브라우저 연결 방식에서 실행하세요.')
    if target['scope']=='receipt':
        from core import Sheets
        return Sheets(config)
    if target['scope']=='outgoing':
        from outgoing import OutgoingSheets
        return OutgoingSheets(config)
    from stamp_ledger import StampSheets
    return StampSheets(config)


def compatible(current,target):
    after=target['after'];before=target.get('before') or ['']*len(after)
    for col,value in enumerate(current[:len(after)]):
        allowed={str(after[col]),str(before[col]) if col<len(before) else ''}
        if col in target['columns']:allowed.add(str(target['restore'].get(str(col),'')))
        if value in allowed:continue
        if col in (2,3,4) and after[col]=='○' and re.fullmatch(r'서울센터20\d{2}-○',value):continue
        return False
    return True


def locate(ledger,target):
    _,rows,_=ledger.snapshot()
    row=target['row']
    def match(values):
        anchored=values and values[0]==target['after'][0]
        untouched=not target.get('complete') and values==target.get('before')
        return (anchored or untouched) and compatible(values,target)
    if row<=len(rows) and match(rows[row-1]):return row,rows[row-1]
    # Physical row insertion can move a document; never guess between multiple matches.
    matches=[(i+1,r) for i,r in enumerate(rows) if i>ledger.header_row and match(r)]
    if len(matches)!=1:raise ValueError('취소할 등록 행의 내용이 바뀌었거나 동일한 행을 확정할 수 없습니다. 다른 행은 지우지 않았습니다.')
    return matches[0]


def evidence(job,config,scope,family):
    wanted=set(markers(job,scope));entries=Journal(config).entries()
    found=[e for e in entries if not e.get('cancelled') and wanted.intersection(e['markers'])]
    bound=record(job,scope,config).get('sheet_connection')
    if bound:found=[e for e in found if e['sid']==bound['spreadsheet_id'] and e['tab']==bound['tab']]
    unique={(e['sid'],e['tab'],e['row']):e for e in found}
    if len(unique)!=1:raise ValueError('이 프로그램의 등록 확인 이력을 확정할 수 없어 시트 자동 취소를 중단했습니다. 기존 행은 유지했습니다.')
    entry=deepcopy(next(iter(unique.values())));entry.update(scope=scope,family=family,columns=columns(scope,family))
    before=entry.get('before') or []
    entry['restore']={str(c):str(before[c]) if c<len(before) else '' for c in entry['columns']}
    return entry


def reset(job,scope):
    audit={'at':datetime.now(timezone.utc).isoformat(),'scope':scope,'outgoing':deepcopy(job.get('outgoing',{}))}
    if scope=='receipt':audit['receipt']={k:deepcopy(job.get(k)) for k in ('row','number','receipt_tab','sheet_connection','receipt_registration_markers')}
    job.setdefault('registration_cancellations',[]).append(audit)
    out=job.get('outgoing',{})
    for current in (out,out.get('stamp',{})):
        for key in ('row','number','document_number','tab','sheet_attempted','complete','sheet_connection','hwp_path','hwp_saved_number','hwp_created_sha256','file_number'):current.pop(key,None)
    if out:out['id']=uuid.uuid4().hex
    if scope=='receipt':
        for key in ('row','number','sheet_attempted','receipt_done','receipt_tab','sheet_connection','receipt_registration_markers'):job.pop(key,None)
        job['registration_epoch']=uuid.uuid4().hex
        job['state']='확인 필요'
    else:job['state']='발신 작성 중' if job.get('standalone_outgoing') else '완료'
    job['reason']='등록 내용과 생성 파일·폴더를 취소했습니다. 다음 등록은 시트의 최신 번호를 확인합니다.'
    edits=job.get('last_rollback_edited_files',[])
    if edits:
        job.setdefault('edited_hwp_backups',[]).extend(deepcopy(edits))
        job['reason']+=' 수정한 HWP 보관 위치: '+', '.join(e['backup'] for e in edits)
    job.pop('registration_cancel_pending',None)


def execute(job,config,store,scope,factory=None):
    factory=factory or owner
    if scope not in ('receipt','outgoing'):raise ValueError('취소할 공문 단계를 확인하세요.')
    pending=job.get('registration_cancel_pending')
    if pending and pending['scope']!=scope:raise ValueError('이미 시작한 등록 취소를 먼저 완료하세요.')
    if not pending:
        job.pop('last_rollback_edited_files',None)
        from file_rollback import has_files,plan
        if has_files(job,scope):plan(job,config,scope,allow_edited_hwp=True)  # Preserve owned edited HWP; protect unrelated files before touching ledgers.
        targets=[]
        scopes=['stamp','outgoing']+(['receipt'] if scope=='receipt' and not job.get('standalone_outgoing') else [])
        for kind in scopes:
            if kind!='receipt' and not job.get('outgoing'):continue
            if not locked(job,kind):continue
            family=job.get('receipt_type','공문') if kind=='receipt' else job['outgoing'].get('ledger_type','공문')
            target=evidence(job,config,kind,family);ledger=factory(config,target)
            try:locate(ledger,target)
            finally:ledger.browser.close()
            targets.append(target)
        pending={'scope':scope,'targets':targets,'files_done':False};job['registration_cancel_pending']=pending;store.put(job)
    if not pending['files_done']:
        from file_rollback import execute as rollback,has_files
        if has_files(job,scope):rollback(job,config,store,scope,allow_edited_hwp=True)
        pending=job['registration_cancel_pending'];pending['files_done']=True;store.put(job)
    from core import registration_lock
    with registration_lock(config['root']):
        for target in pending['targets']:
            if target.get('cancelled'):continue
            ledger=factory(config,target)
            try:
                row,current=locate(ledger,target)
                for col in target['columns']:
                    value=target['restore'][str(col)]
                    current=ledger.browser.getrows(f'A{row}:{ledger.last_column}{row}')[0]
                    anchored=current[0]==target['after'][0] or (not target.get('complete') and current[0]==target.get('before',[''])[0])
                    if not anchored or not compatible(current,target):raise ValueError('취소 중 시트 행의 내용이 변경되어 중단했습니다. 다른 내용은 지우지 않습니다.')
                    if current[col]!=value:ledger.browser.ui.write(f'{chr(65+col)}{row}',"'"+value if value.startswith(('=','+','-','@')) else value)
                actual=ledger.browser.getrows(f'A{row}:{ledger.last_column}{row}')[0]
                if not compatible(actual,target) or any(actual[c]!=target['restore'][str(c)] for c in target['columns']):raise ValueError('시트 등록 취소 결과를 확인하지 못했습니다. 같은 공문에서 취소를 다시 진행하세요.')
                target['cancelled']=True
                Journal(config).mark_cancelled(target)
                store.put(job)
            finally:ledger.browser.close()
    reset(job,scope)
    from core import attachment_aliases
    store.put_many([job,*attachment_aliases(job,store,'묶음에 포함')]);return job
