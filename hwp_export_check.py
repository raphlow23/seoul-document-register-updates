"""Same-version HWP validation; private diagnostics never enter business folders."""
from pathlib import Path
import hashlib,json,shutil,uuid


def check_baseline(source,baseline):
    from core import extract
    from hwp_fidelity import snapshot
    original=snapshot(source);saved=snapshot(baseline)
    # Ctrl style slots must retain their names and order. The unedited round trip
    # may rewrite version-dependent DocInfo bytes and reference table IDs.
    if original['names']!=saved['names'][:len(original['names'])]:
        raise ValueError('원본 양식의 스타일 이름·순서가 기준본 저장 중 달라졌습니다.')
    if [p['style'] for p in original['paragraphs']]!=[p['style'] for p in saved['paragraphs']]:
        raise ValueError('원본 양식의 문단 스타일 구성이 기준본 저장 중 달라졌습니다.')
    compact=lambda text:''.join(text.split())
    if compact(extract(source))!=compact(extract(baseline)):
        raise ValueError('원본 양식의 내용이 기준본 저장 중 달라졌습니다.')


def diagnostics(source,baseline,result,error):
    from program_storage import temporary_root,save_json
    folder=temporary_root({},'HWP-서식확인')/uuid.uuid4().hex
    folder.mkdir(parents=True,exist_ok=False)
    report={'error':str(error),'files':{}}
    from hwp_fidelity import snapshot
    for name,path in (('original',source),('unchanged',baseline),('result',result)):
        path=Path(path);saved=folder/(name+'.hwp');shutil.copyfile(path,saved)
        entry={'sha256':hashlib.sha256(saved.read_bytes()).hexdigest()}
        try:
            state=snapshot(saved)
            entry['tables']={str(tag):[hashlib.sha256(p).hexdigest() for p in entries] for tag,entries in state['tables'].items()}
            entry['style_count']=len(state['names']);entry['paragraph_count']=len(state['paragraphs'])
        except Exception as issue:entry['read_error']=str(issue)
        report['files'][name]=entry
    save_json(folder/'report.json',report)
    return folder


def verify_export(source,baseline,result,verifier):
    try:
        check_baseline(source,baseline)
        return verifier(baseline,result)
    except (ValueError,RuntimeError) as error:
        try:folder=diagnostics(source,baseline,result,error)
        except OSError:raise error
        raise ValueError(str(error)+'\n공유폴더에는 저장하지 않았습니다. 확인 자료: '+str(folder)) from error
