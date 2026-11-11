"""Resolve ledger destinations and pin them before writes so retries never change files."""
import re
import unicodedata
from urllib.parse import urlsplit

FAMILIES=('공문','업무연락','처리지침')
SCOPES={'receipt':'수신대장','outgoing':'발신대장','stamp':'직인날인대장'}


def header_text(value):
    return re.sub(r'[\s\u200b-\u200f\u2060\ufeff\u00ad]+','',unicodedata.normalize('NFKC',str(value or '')))


def spreadsheet_id(value):
    value=str(value).strip()
    if '://' in value:
        url=urlsplit(value)
        if url.hostname!='docs.google.com' or '/spreadsheets/d/' not in url.path:raise ValueError('Google 스프레드시트 주소 또는 ID를 입력하세요.')
        value=url.path.split('/spreadsheets/d/',1)[1].split('/',1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError('구글 시트 주소 또는 ID를 확인하세요.')
    return value


def legacy_tab(config,scope,family):
    if scope=='receipt':
        from core import RECEIPT_TABS
        return config.get('tab') or RECEIPT_TABS[family] if family=='공문' else RECEIPT_TABS[family]
    if scope=='outgoing':
        from outgoing import OUTGOING_TABS
        return OUTGOING_TABS[family]
    from stamp_ledger import STAMP_TAB
    return STAMP_TAB


def normalized(target):
    tab=str(target.get('tab','')).strip()
    if not tab or any(c in tab for c in '\t\r\n'):raise ValueError('연결할 하단 시트 탭 이름을 입력하세요.')
    result={'spreadsheet_id':spreadsheet_id(target.get('spreadsheet_id','')),'tab':tab}
    if target.get('layout'):result['layout']=target['layout'].copy()
    return result


def default_connection(config,scope,family):
    entry=config.get('ledger_connections',{}).get(scope+':'+family,{})
    result={'spreadsheet_id':entry.get('spreadsheet_id') or config.get('spreadsheet_id',''),'tab':entry.get('tab') or legacy_tab(config,scope,family)}
    if entry.get('layout'):
        from ledger_layout import normalize
        result['layout']=normalize(entry['layout'],scope,family)
    return result


def browser_config(config):
    target=default_connection(config,'receipt','공문')
    if not target['spreadsheet_id']:
        for family in FAMILIES:
            for scope in SCOPES:
                candidate=default_connection(config,scope,family)
                if candidate['spreadsheet_id']:target=candidate;break
            if target['spreadsheet_id']:break
    return {**config,**normalized(target)}


def preflight(config,job):
    if not config.get('spreadsheet_id') and not config.get('ledger_connections'):return
    if not job.get('standalone_outgoing') and not job.get('receipt_done'):
        normalized(chosen(config,job,'receipt',job.get('receipt_type','공문')))
    out=job.get('outgoing',{})
    if out.get('enabled'):
        for scope in ('outgoing','stamp'):
            if not record(job,scope,config).get('complete'):normalized(chosen(config,job,scope,out.get('ledger_type','공문')))


def record(job,scope,config=None):
    if scope=='receipt':return job
    if 'outgoing' not in job:
        from outgoing import defaults
        job['outgoing']=defaults(job,config or {})
    return job['outgoing'] if scope=='outgoing' else job['outgoing'].setdefault('stamp',{})


def locked(job,scope):
    current=record(job,scope)
    return bool(current.get('sheet_attempted') or current.get('complete') or (scope=='receipt' and current.get('receipt_done')))


def chosen(config,job,scope,family):
    current=record(job,scope,config)
    keep=locked(job,scope) or (scope=='stamp' and locked(job,'outgoing'))
    if keep and current.get('sheet_connection'):return current['sheet_connection'].copy()
    return (current.get('sheet_connection_override') or default_connection(config,scope,family)).copy()


def bind(config,job,scope,family):
    target=chosen(config,job,scope,family)
    if not target['spreadsheet_id']:return {**config,'tab':target['tab'],'_target_tab':target['tab']}
    target=normalized(target)
    record(job,scope,config)['sheet_connection']=target
    return {**config,**target,'_target_tab':target['tab']}


def set_override(job,config,scope,target):
    if job.get('registration_cancel_pending'):raise ValueError('시작한 등록 취소를 이전 단계 버튼으로 먼저 완료하세요.')
    if scope not in SCOPES:raise ValueError('연결할 대장 종류를 확인하세요.')
    if locked(job,scope):raise ValueError('이 대장의 등록을 이미 시작했습니다. 당시 연결을 유지합니다.')
    current=record(job,scope,config)
    if target is None:
        current.pop('sheet_connection_override',None);current.pop('sheet_connection',None);return
    current['sheet_connection_override']=normalized(target)
    current['sheet_connection']=current['sheet_connection_override'].copy()


def pin_existing(store,config):
    # Preserve old jobs' destinations BEFORE changing global defaults.
    updated=[]
    for job in store.all():
        changed=False
        for scope in SCOPES:
            if scope!='receipt' and not job.get('outgoing'):continue
            if scope=='receipt' and job.get('standalone_outgoing'):continue
            current=record(job,scope,config)
            if locked(job,scope) and not current.get('sheet_connection'):
                family=job.get('receipt_type','공문') if scope=='receipt' else job['outgoing'].get('ledger_type','공문')
                target=default_connection(config,scope,family)
                old_tab=job.get('receipt_tab') if scope=='receipt' else current.get('tab')
                if old_tab:target['tab']=old_tab
                if target['spreadsheet_id']:current['sheet_connection']=normalized(target);changed=True
        if changed:store.put(job);updated.append(job)
    return updated
