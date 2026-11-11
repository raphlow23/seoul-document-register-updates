"""Keep only observed header labels locally; never include ledger data rows."""
import uuid,re
from sheet_connections import header_text


def record(config,owner,rows):
    labels={'연번','제목','건명','문서명','분류번호','수신처','발송자','방법','접수일자','일자'}
    observed=[]
    for index,row in enumerate(rows[:50]):
        if row and str(row[0]).strip().isdigit():continue
        if len(row)>1 and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',str(row[1]).strip()):continue
        if any(header_text(value) in labels for value in row):
            observed.append({'row':index+1,'cells':{chr(65+i):str(v) for i,v in enumerate(row) if str(v).strip()}})
    summary=' / '.join(f"{entry['row']}행 "+', '.join(f'{c}={header_text(v)}' for c,v in entry['cells'].items()) for entry in observed[:3])
    message='\n읽어 온 머리글: '+(summary or '머리글 문구를 읽지 못했습니다.')
    try:
        from program_storage import temporary_root,save_json
        path=temporary_root(config,'시트머리글')/(uuid.uuid4().hex+'.json')
        save_json(path,{'tab':owner.tab,'last_column':owner.last_column,'read_row_count':len(rows),'headers':observed})
        message+='\n확인 기록: '+str(path)
    except OSError:pass
    return message
