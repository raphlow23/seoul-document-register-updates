"""Explicit ledger layouts. Business logic uses stable logical columns; transports map them.

Only owned input cells are mapped. Unmapped physical columns are never written/copied.
The layout is pinned with the destination and journal, so later defaults cannot move a retry.
"""
from copy import deepcopy
from datetime import date
import re

LABELS={'serial':'연번','date':'일자','prefix':'번호 앞 문구','number':'접수·발신번호',
        'issuer':'발신처','title':'제목','recipient':'담당자·수신자','method':'방법',
        'receiver':'수신처','sender':'발송자·담당자','official':'공문·발송문서 ○',
        'internal':'내부결재 ○','guideline':'처리지침 ○','work':'업무연락 ○',
        'document_number':'문서번호','department':'신청부서','applicant':'신청자','person':'날인자'}
DATE_FORMATS=('YYYY-MM-DD','YYYY.MM.DD','YYYY. MM. DD.')


def schema(scope,family):
    if scope=='receipt':return ('serial','date','prefix','number','issuer','title','recipient','method')
    if scope=='stamp':return ('serial','date','official','work','guideline','document_number','title','receiver','department','applicant','person')
    if scope!='outgoing':raise ValueError('대장 종류를 확인하세요.')
    if family!='공문':return ('serial','date','prefix','number','title','receiver','sender','method')
    return ('serial','date','official','internal','guideline','prefix','number','title','receiver','sender','method')


def column(index):
    result='';index+=1
    while index:index,rem=divmod(index-1,26);result=chr(65+rem)+result
    return result


def index(text):
    text=str(text).strip().upper()
    if not re.fullmatch('[A-Z]{1,2}',text):raise ValueError('열은 A, B, …, AZ처럼 입력하세요.')
    result=0
    for c in text:result=result*26+ord(c)-64
    if result>52:raise ValueError('대장 연결 열은 A~AZ 범위에서 지정하세요.')
    return result-1


def normalize(raw,scope,family):
    raw=raw or {};keys=schema(scope,family)
    if not isinstance(raw,dict):raise ValueError('대장 열 설정을 확인하세요.')
    columns={k:column(i) for i,k in enumerate(keys)}
    if set(raw.get('columns',{}))-set(keys):raise ValueError('이 대장에 없는 입력 항목입니다.')
    columns.update(raw.get('columns',{}));columns={k:column(index(v)) for k,v in columns.items()}
    if len(set(columns.values()))!=len(keys):raise ValueError('서로 다른 항목을 같은 열에 연결할 수 없습니다.')
    header=str(raw.get('header_range','')).strip().upper().replace('$','')
    start=raw.get('data_start','')
    if header:
        from browser_ranges import bounds
        r1,c1,r2,c2=bounds(header)
        if r2-r1>9 or r2>500 or c2>52:raise ValueError('머리글 범위는 500행 이내, 최대 10행, A~AZ로 지정하세요.')
        if not all(c1-1<=index(v)<c2 for v in columns.values()):raise ValueError('모든 연결 열이 머리글 범위 안에 있어야 합니다.')
        header=f'{column(c1-1)}{r1}:{column(c2-1)}{r2}'
    if start not in ('',None):
        if not str(start).isdigit() or not 2<=int(start)<=20000:raise ValueError('데이터 시작 행은 2~20000 사이의 행 번호를 입력하세요.')
        start=int(start)
        if not header:raise ValueError('데이터 시작 행과 함께 머리글 범위를 지정하세요.')
        if start<=r2:raise ValueError('데이터 시작 행은 머리글 다음 행부터 지정하세요.')
    elif header:raise ValueError('머리글 범위와 함께 첫 데이터 행을 지정하세요.')
    fmt=raw.get('date_format',DATE_FORMATS[0])
    if fmt not in DATE_FORMATS:raise ValueError('지원하는 날짜 표시 형식을 선택하세요.')
    result={'columns':columns,'header_range':header,'data_start':start or '', 'date_format':fmt}
    return result


def context(owner,config):
    scope={'SDR:':'receipt','SDO:':'outgoing','SDS:':'stamp'}[owner.marker_prefix]
    family=config.get('_outgoing_family','공문')
    return scope,family


def install(owner,config):
    raw=config.get('layout')
    if not raw:return None
    scope,family=context(owner,config);layout=normalize(raw,scope,family)
    owner.layout=layout
    original=owner.validate_header
    def validate(rows):
        if not layout['header_range']:return original(rows)
        from browser_ranges import bounds
        r1,_,r2,_=bounds(layout['header_range'])
        if len(rows)<r2:raise ValueError('설정한 머리글 범위를 읽지 못했습니다. 등록하지 않았습니다.')
        original(rows[r1-1:r2])
        owner.layout_header_row=owner.header_row+r1-1
        owner.header_row=int(layout['data_start'])-2
        owner.notes_start=int(layout['data_start'])
    owner.validate_header=validate
    return layout


def default_values(config,scope,family):
    """Visible fallbacks; dynamic document fields (date/title/numbers) stay authoritative."""
    if scope=='receipt':values={'issuer':'','recipient':config.get('recipient',''),'method':'Email'}
    elif scope=='outgoing':values={'receiver':'','sender':config.get('recipient',''),'method':'Email'}
    else:values={'department':'편의증진부','applicant':'','person':''}
    entry=config.get('ledger_connections',{}).get(scope+':'+family,{})
    return {**values,**normalize_values(entry.get('defaults',{}),scope,family)}


def normalize_values(raw,scope,family):
    allowed={'receipt':('issuer','recipient','method'),'outgoing':('receiver','sender','method'),
             'stamp':('department','applicant','person')}[scope]
    if not isinstance(raw,dict) or set(raw)-set(allowed):raise ValueError('기본 입력값의 항목을 확인하세요.')
    result={}
    for key,value in raw.items():
        value=str(value).strip()
        if len(value)>300 or any(c in value for c in '\t\r\n'):raise ValueError('기본 입력값은 300자 이내 한 줄로 입력하세요.')
        if key=='method' and value not in (('Email','문서24','우편','FAX','인편') if family=='공문' else ('Email','우편','FAX','인편')):
            raise ValueError('발신 방법은 Email·우편·FAX·인편 중 선택하세요. 공문은 문서24도 가능합니다.')
        result[key]=value
    return result


def apply_outgoing(out,config,force=False):
    """Initialize each family once, never overwrite a user's edit or attempted write."""
    family=out.get('ledger_type','공문')
    if out.get('sheet_attempted'):return out
    if not force and out.get('defaults_family')==family:return out
    previous=out.get('defaults_snapshot',{})
    current=default_values(config,'outgoing',family)
    stamp=out.setdefault('stamp',{})
    for key,value in current.items():
        if force or key not in out or out[key]==previous.get(key,out[key] if 'defaults_family' not in out else None):out[key]=value
    stamp_values=default_values(config,'stamp',family)
    if not stamp_values['applicant']:stamp_values['applicant']=out.get('sender','')
    if not stamp.get('sheet_attempted'):
        for key,value in stamp_values.items():
            if force or key not in stamp or stamp[key]==previous.get('stamp',{}).get(key,stamp[key] if 'defaults_family' not in out else None):stamp[key]=value
    out['defaults_family']=family;out['defaults_snapshot']={**current,'stamp':stamp_values}
    return out


class MappedUI:
    """Map the UI's logical ranges without moving sheet tabs, rows or columns."""
    def __init__(self,raw,layout,scope,family):
        self.raw=raw;self.layout=normalize(layout,scope,family)
        self.keys=schema(scope,family);self.cols=[index(self.layout['columns'][k]) for k in self.keys]

    def __getattr__(self,name):return getattr(self.raw,name)

    def span(self,range_):
        m=re.fullmatch(r'([A-Z]+)(\d*):([A-Z]+)(\d*)',range_)
        if not m:
            m=re.fullmatch(r'([A-Z]+)(\d+)',range_)
            if not m:raise ValueError('열 연결에 사용할 셀 범위를 확인하세요.')
            return index(m[1]),index(m[1]),m[2],m[2]
        return index(m[1]),index(m[3]),m[2],m[4]

    def read(self,range_):
        first,last,r1,r2=self.span(range_);cols=self.cols[first:last+1]
        lo,hi=min(cols),max(cols)
        rows=self.raw.read(f'{column(lo)}{r1}:{column(hi)}{r2}')
        result=[]
        for row in rows:
            values=[row[c-lo] if c-lo<len(row) else '' for c in cols]
            # Partial verification reads need the same canonical date as full rows.
            if first<=1<=last:values[1-first]=canonical_date(values[1-first])
            result.append(values)
        return result

    def write(self,cell,value):
        col,_,r,_=self.span(cell)
        if value.startswith('='):
            match=re.fullmatch(r'=([A-K])(\d+)\+1',value)
            if not match:raise ValueError('지원하지 않는 대장 번호 수식입니다.')
            value=f'={column(self.cols[index(match[1])])}{match[2]}+1'
        elif col==1 and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            dt=date.fromisoformat(value);fmt=self.layout['date_format']
            value=dt.strftime({'YYYY-MM-DD':'%Y-%m-%d','YYYY.MM.DD':'%Y.%m.%d','YYYY. MM. DD.':'%Y. %m. %d.'}[fmt])
        return self.raw.write(f'{column(self.cols[col])}{r}',value)

    def copy_format(self,source,target):
        a,b,r1,r2=self.span(source);c,d,s1,s2=self.span(target)
        if a==b:
            for dest in range(c,d+1):self.raw.copy_format(f'{column(self.cols[a])}{r1}:{column(self.cols[a])}{r2}',f'{column(self.cols[dest])}{s1}:{column(self.cols[dest])}{s2}')
        else:
            if b-a!=d-c:raise ValueError('서식 복사 열 수가 다릅니다.')
            for src,dest in zip(range(a,b+1),range(c,d+1)):
                self.raw.copy_format(f'{column(self.cols[src])}{r1}:{column(self.cols[src])}{r2}',f'{column(self.cols[dest])}{s1}:{column(self.cols[dest])}{s2}')


def canonical_date(text):
    m=re.fullmatch(r'(20\d\d)[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})(?:일|\.)?',str(text).strip())
    if m:
        try:return date(*map(int,m.groups())).isoformat()
        except ValueError:pass
    return text


class APIMapper:
    """Apply identical coordinate mapping to Sheets API requests, including notes/formats."""
    def __init__(self,owner,config,request):
        self.owner=owner;self.request=request;scope,family=context(owner,config)
        self.cols=[index(owner.layout['columns'][k]) for k in schema(scope,family)]

    def __call__(self,method,suffix='',**kwargs):
        from urllib.parse import unquote,quote
        if method=='GET' and suffix.startswith('/values/'):
            name,range_=unquote(suffix[len('/values/'):]).rsplit('!',1)
            first,last,r1,r2=MappedUI.span(self,range_);cols=self.cols[first:last+1];lo,hi=min(cols),max(cols)
            response=self.request(method,'/values/'+quote(f'{name}!{column(lo)}{r1}:{column(hi)}{r2}',safe=''),**kwargs)
            rows=[]
            for row in response.get('values',[]):
                values=[row[c-lo] if c-lo<len(row) else '' for c in cols]
                if first<=1<=last:values[1-first]=canonical_date(values[1-first])
                rows.append(values)
            return {**response,'values':rows}
        if method=='GET' and kwargs.get('params',{}).get('ranges'):
            kwargs=deepcopy(kwargs);text=kwargs['params']['ranges']
            name,span=text.rsplit('!',1);a,b,r1,r2=MappedUI.span(self,span)
            kwargs['params']['ranges']=f'{name}!{column(self.cols[a])}{r1}:{column(self.cols[b])}{r2}'
        if method=='POST':
            kwargs=deepcopy(kwargs);requests=[]
            for req in kwargs['json']['requests']:
                if 'copyPaste' in req:
                    cp=req['copyPaste'];src,dst=cp['source'],cp['destination']
                    for c in range(src['startColumnIndex'],src['endColumnIndex']):
                        item=deepcopy(cp)
                        for part in ('source','destination'):item[part].update(startColumnIndex=self.cols[c],endColumnIndex=self.cols[c]+1)
                        requests.append({'copyPaste':item})
                elif 'updateCells' in req:
                    item=deepcopy(req);cell=item['updateCells'];logical=cell['start']['columnIndex'];cell['start']['columnIndex']=self.cols[logical]
                    v=cell['rows'][0]['values'][0];entered=v.get('userEnteredValue',{})
                    if 'formulaValue' in entered:
                        entered['formulaValue']=re.sub(r'([A-K])(\d+)',lambda m:column(self.cols[index(m[1])])+m[2],entered['formulaValue'])
                    if logical==1 and 'numberValue' in entered:
                        fmt={'YYYY-MM-DD':'yyyy-mm-dd','YYYY.MM.DD':'yyyy.mm.dd','YYYY. MM. DD.':'yyyy. mm. dd.'}[self.owner.layout['date_format']]
                        v.setdefault('userEnteredFormat',{})['numberFormat']={'type':'DATE','pattern':fmt}
                        if 'userEnteredFormat.numberFormat' not in cell['fields']:cell['fields']+=',userEnteredFormat.numberFormat'
                    requests.append(item)
                else:raise ValueError('지원하지 않는 대장 열 연결 요청입니다.')
            kwargs['json']['requests']=requests
        return self.request(method,suffix,**kwargs)
