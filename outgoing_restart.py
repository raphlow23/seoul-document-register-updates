"""Cancel only local outgoing progress; never remove ledger rows or saved documents."""
from copy import deepcopy
from datetime import datetime,timezone
from outgoing import defaults


def restart(job,config,store,hide=False,expected_id=None):
    if job.get('registration_cancel_pending'):raise ValueError('시작한 등록 취소를 이전 단계 버튼으로 먼저 완료하세요.')
    if not (job.get('standalone_outgoing') or job.get('receipt_done') or job.get('state')=='완료'):
        raise ValueError('수신 접수가 끝난 문서에서 발신을 다시 시작할 수 있습니다.')
    previous=job.get('outgoing',{})
    if expected_id is not None and previous.get('id')!=expected_id:
        raise ValueError('선택한 발신 진행이 변경되었습니다. 목록에서 다시 선택해 주세요.')
    result=deepcopy(job)
    if previous:
        result.setdefault('outgoing_attempts',[]).append({'cancelled_at':datetime.now(timezone.utc).isoformat(),'action':'hide' if hide else 'restart','outgoing':deepcopy(previous)})
    result.pop('outgoing',None)
    # A new transaction ID prevents recovery from adopting the cancelled outgoing/stamp rows.
    result['outgoing']=defaults(result,config)
    if result.get('standalone_outgoing'):
        result.update(state='발신 작성 중',reason='발신 목록에서 제외' if hide else '발신 초기화 · 새 발신번호로 다시 작성')
    else:result.update(receipt_done=True,state='완료',reason='발신 목록에서 제외 · 수신 접수 유지' if hide else '발신 초기화 · 새 발신번호로 다시 작성')
    if hide:result['outgoing_list_hidden']=True
    else:result.pop('outgoing_list_hidden',None)
    store.put(result)
    return result
