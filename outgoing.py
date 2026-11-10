"""Independent outgoing ledger transactions; never send email or repeat receipt writes."""
from datetime import date
import re
import uuid
from urllib.parse import quote
from core import Sheets, RejectedWrite, value

OUTGOING_TAB="2026년 문서발행대장"
OUTGOING_TABS={"공문":OUTGOING_TAB,"업무연락":"2026년(업무연락)발행","처리지침":"2026년(처리지침)발행"}
KINDS=("발송문서","내부결재","처리지침")
METHODS=("Email","문서24","우편","FAX","인편")


def require_registered_number(out):
    number=str(out.get('number','')).strip();document=str(out.get('document_number','')).strip()
    match=re.search(r'(\d+)$',document)
    if not out.get('complete') or not re.fullmatch(r'\d{1,6}',number) or int(number)<1 or not match or int(match[1])!=int(number):
        raise ValueError('구글시트 발신대장에 먼저 등록하고 실제 발신번호를 확인한 뒤 HWP를 저장하세요.')
    return number


def defaults(job,config):
    out={"enabled":False,"id":uuid.uuid4().hex,"title":job.get("title",""),"title_same":True,"ledger_type":"공문",
            "date":date.today().isoformat(),"receiver":"","sender":config.get("recipient",job.get("recipient","")),
            "kind":"발송문서","method":"Email","body":"","attachments":"","reference":"","summary":"",
            "purpose":"전달·안내","template_path":config.get("document_template",""),**job.get("outgoing",{})}
    out["stamp"]={"department":"편의증진부","applicant":out["sender"],"person":"","date":out["date"],**out.get("stamp",{})}
    out['file_number']=str(out.get('number','')) if out.get('complete') else ''
    if job.get('standalone_outgoing'):out['title_same']=False
    if out.get("title_same",True) and not out.get("sheet_attempted"):out["title"]=job.get("title",out["title"])
    return out


def validate(job,config=None):
    out=defaults(job,config or {})
    if out.get("title_same",True) and not out.get("sheet_attempted"): out["title"]=job["title"]
    if out.get("enabled"):
        for key,label in (("title","발신 제목"),("sender","발송자")):
            if not out.get(key,"").strip(): raise ValueError(label+"을 입력해 주세요.")
        if out.get("kind") not in KINDS: raise ValueError("발신 문서 분류를 선택해 주세요.")
        if out.get("ledger_type") not in OUTGOING_TABS: raise ValueError("발신 등록 유형을 선택해 주세요.")
        if out["ledger_type"]!="공문" and out.get("method") not in ("Email","FAX","우편","인편"):
            raise ValueError("업무연락·처리지침 발신 방법은 Email/FAX/우편/인편 중 골라 주세요.")
        if out["kind"]!="내부결재" and not out.get("receiver","").strip(): raise ValueError("발신 공문의 수신 기관을 입력해 주세요.")
        date.fromisoformat(out["date"])
        date.fromisoformat(out["stamp"]["date"])
        if not out["stamp"]["department"].strip() or not out["stamp"]["applicant"].strip():
            raise ValueError("직인날인대장의 신청부서와 신청자를 입력해 주세요.")
    job["outgoing"]=out
    return out


def merge_edit(job,edited):
    old=job.get("outgoing",{})
    previous=old.get("folder_plan")
    if previous and (previous.get("folder_name") or previous.get("created")):
        from outgoing_folders import PLAN_FIELDS
        current=edited.get("folder_plan") or {}
        if any(previous.get(key)!=current.get(key) for key in PLAN_FIELDS):raise ValueError("발신 폴더 저장을 시작한 공문입니다. 기존 폴더 구성으로 이어서 저장하세요.")
        edited={**edited,"folder_plan":{**previous}}
        if previous.get("created"):edited["save_folder"]=previous["save_path"]
    if old.get("sheet_attempted"):
        keys=("enabled","ledger_type","title","title_same","date","receiver","sender","kind","method")
        if any(edited.get(k)!=old.get(k) for k in keys):
            raise ValueError("발신 등록을 시도한 문서의 등록 정보는 바꿀 수 없습니다. 초안 본문과 양식만 수정할 수 있습니다.")
    if old.get("stamp",{}).get("sheet_attempted"):
        if any(edited.get("stamp",{}).get(k)!=old["stamp"].get(k) for k in ("date","department","applicant","person")):
            raise ValueError("직인날인대장 등록을 시도한 내용은 바꿀 수 없습니다.")
    edited={**edited}
    for key in ("id","row","number","document_number","tab","sheet_attempted","complete"):
        if key in old:edited[key]=old[key]
    edited['file_number']=str(edited.get('number','')) if edited.get('complete') else ''
    if old.get("complete"):
        for key in ("hwp_path","hwp_saved_number","hwp_created_sha256"):
            if key in old:edited[key]=old[key]
    if 'files_rolled_back' in old:
        edited['files_rolled_back']=old['files_rolled_back']
        if old['files_rolled_back']:
            for key in ('hwp_path','hwp_saved_number','hwp_created_sha256'):edited.pop(key,None)
            plan=edited.get('folder_plan')
            if plan and not old.get('folder_plan',{}).get('created'):
                plan=dict(plan)
                for key in ('folder_number','folder_name','created','path','save_path'):plan.pop(key,None)
                edited['folder_plan']=plan
                if plan.get('base'):edited['save_folder']=plan['base']
    edited["stamp"]={**edited.get("stamp",{})}
    for key in ("row","number","sheet_attempted","complete"):
        if key in old.get("stamp",{}):edited["stamp"][key]=old["stamp"][key]
    job["outgoing"]=edited
    if job.get('standalone_outgoing'):
        job.update(title=edited.get('title',''),date=edited.get('date',job.get('date','')))
        if not (edited.get('complete') and edited.get('stamp',{}).get('complete')):job['state']='발신 등록 대기'
        return job
    if edited.get("enabled") and not (edited.get("complete") and edited.get("stamp",{}).get("complete")) and job["state"]=="완료":
        job["receipt_done"]=True
        job["state"]="수신 완료·발신 등록 대기"
    return job


def requests(sheet_id,row,out,previous,marker,candidate=None,compact=False):
    candidate=candidate or []
    cells=[{"userEnteredValue":{"formulaValue":f"=A{row-1}+1"}},
           {"userEnteredValue":{"numberValue":(date.fromisoformat(out["date"])-date(1899,12,30)).days}},
           value("○" if out["kind"]=="발송문서" else ""),value("○" if out["kind"]=="내부결재" else ""),value("○" if out["kind"]=="처리지침" else ""),
           value(previous[5]),{"userEnteredValue":{"formulaValue":f"=G{row-1}+1"}},
           value(out["title"]),value(out["receiver"]),value(out["sender"]),value(out["method"])]
    if compact:
        cells=[cells[0],cells[1],value(previous[2]),{"userEnteredValue":{"formulaValue":f"=D{row-1}+1"}},
               value(out["title"]),value(out["receiver"]),value(out["sender"]),value(out["method"])]
    title_col=4 if compact else 7
    preserve=(0,1,2,3) if compact else (0,1,5,6)
    result=[]
    if not any(str(x).strip() for x in candidate):
        src={"sheetId":sheet_id,"startRowIndex":row-2,"endRowIndex":row-1,"startColumnIndex":0,"endColumnIndex":len(cells)}
        dst={**src,"startRowIndex":row-1,"endRowIndex":row}
        result.extend({"copyPaste":{"source":src,"destination":dst,"pasteType":kind}} for kind in ("PASTE_FORMAT","PASTE_DATA_VALIDATION"))
    for col,cell in enumerate(cells):
        if col in preserve and col<len(candidate) and str(candidate[col]).strip(): continue
        result.append({"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":row-1,"columnIndex":col},"rows":[{"values":[cell]}],"fields":"userEnteredValue"}})
    result.append({"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":row-1,"columnIndex":title_col},"rows":[{"values":[{"note":marker}]}],"fields":"note"}})
    return result


class OutgoingSheets(Sheets):
    last_column="K"
    title_column=7
    note_column="H"
    header_word="분"
    marker_prefix="SDO:"

    def validate_header(self,rows):
        compact=lambda value:re.sub(r'[\s\u200b-\u200d\ufeff]+','',str(value or ''))
        matches=[];width=8 if self.compact else 11
        for index,row in enumerate(rows[:50]):
            cells=[compact(v) for v in row]+['']*width
            if not re.fullmatch(r'(?:문서|발신|공문)?제목|건명|문서명',cells[self.title_column]):continue
            columns=[''.join(compact(r[c]) if len(r)>c else '' for r in rows[max(0,index-2):index+2]) for c in range(width)]
            if '연번' not in columns[0] or not any(w in columns[1] for w in ('일자','날짜','발신일','발송일')):continue
            if '번호' not in columns[self.prefix_col]+columns[self.number_col]:continue
            receiver,sender,method=(5,6,7) if self.compact else (8,9,10)
            if not any(w in columns[receiver] for w in ('수신처','수신기관','받는곳')):continue
            if not any(w in columns[sender] for w in ('발송자','발신자','작성자','담당자')):continue
            if '방법' not in columns[method]:continue
            if not self.compact:
                if not any(w in columns[2] for w in ('발송문서','공문')) or not any(w in columns[3] for w in ('내부결재','결재')) or '처리지침' not in columns[4]:continue
            matches.append(index)
        if len(matches)!=1:
            letter='E' if self.compact else 'H'
            raise ValueError(f'{self.tab}의 발신대장 머리글을 확인하지 못했습니다. 연번·일자·분류번호·{letter}열 제목·수신처·발송자·방법 열을 확인하세요. 등록하지 않았습니다.')
        self.header_row=matches[0];self.notes_start=self.header_row+2

    def __init__(self,config):
        family=config.get("_outgoing_family","공문")
        self.compact=family!="공문"
        if self.compact:
            self.last_column="H"
            self.title_column=4
            self.note_column="E"
        self.prefix_col=2 if self.compact else 5
        self.number_col=3 if self.compact else 6
        super().__init__({**config,"tab":OUTGOING_TABS[family]})

    def register(self,job,store):
        out=validate(job)
        if getattr(self,"browser",None):
            self.browser.snapshot()
            self.browser.recover(["SDO:"+out["id"]])
        prop,rows,found=self.snapshot()
        marker="SDO:"+out["id"]
        if marker in found:
            row=found[marker]
        else:
            if out.get("sheet_attempted"): raise ValueError("발신 등록 결과가 불확실합니다. 발신대장에서 등록 여부를 먼저 확인해 주세요.")
            populated=[i+1 for i,r in enumerate(rows) if i>self.header_row and len(r)>self.title_column and str(r[self.title_column]).strip()]
            if not populated: raise ValueError("발신대장의 기존 번호 기준 행을 찾지 못했습니다.")
            row=max(populated)+1
            if row>prop["gridProperties"]["rowCount"]: raise ValueError("발신대장에 빈 행을 추가해 주세요.")
            previous=rows[row-2]
            candidate=rows[row-1] if row<=len(rows) else []
            if len(previous)<=self.number_col or not str(previous[0]).isdigit() or not str(previous[self.number_col]).isdigit(): raise ValueError("바로 앞 발신 행의 연번·발신번호가 숫자가 아닙니다.")
            if any(str(x).strip() for x in candidate[self.title_column:]): raise ValueError("다음 발신 행에 기존 내용이 있습니다.")
            expected={0:str(int(previous[0])+1),self.prefix_col:previous[self.prefix_col],self.number_col:str(int(previous[self.number_col])+1)}
            for col,expected_value in expected.items():
                if col<len(candidate) and str(candidate[col]).strip():
                    actual=str(candidate[col])
                    if (col in (0,self.number_col) and (not actual.isdigit() or int(actual)!=int(expected_value))) or (col==self.prefix_col and actual!=expected_value): raise ValueError("발신대장에 미리 입력된 번호가 앞 행과 맞지 않습니다.")
            if len(candidate)>1 and str(candidate[1]).strip() and candidate[1]!=out["date"]: raise ValueError("발신대장 다음 행의 날짜가 선택한 발신일과 다릅니다.")
            if not self.compact:
                for col,kind in enumerate(KINDS,2):
                    if col<len(candidate) and str(candidate[col]).strip() and candidate[col]!=("○" if out["kind"]==kind else ""): raise ValueError("다음 발신 행에 다른 문서 분류가 입력되어 있습니다.")
            note=getattr(self,"notes",{}).get(row,"")
            out["sheet_attempted"]=True
            store.put(job)
            try:
                self.request("POST",":batchUpdate",json={"requests":requests(prop["sheetId"],row,out,previous,(note+"\n" if note else "")+marker,candidate,self.compact)})
            except RejectedWrite:
                out["sheet_attempted"]=False
                store.put(job)
                raise
        tab="'"+self.tab.replace("'","''")+"'"
        start="D" if self.compact else "G"
        end="E" if self.compact else "H"
        values=self.request("GET","/values/"+quote(f"{tab}!{start}{row}:{end}{row}",safe=""),params={"valueRenderOption":"FORMATTED_VALUE"}).get("values",[])
        if not values or len(values[0])<2 or not str(values[0][0]).isdigit() or values[0][1]!=out["title"]: raise ValueError("발신대장 G열 번호와 제목을 확인하지 못했습니다. 재시도 시 기존 등록부터 확인합니다.")
        prefix=rows[row-1][self.prefix_col] if row<=len(rows) and len(rows[row-1])>self.prefix_col and rows[row-1][self.prefix_col] else rows[row-2][self.prefix_col]
        out.update(row=row,number=str(values[0][0]),file_number=str(values[0][0]),document_number=prefix+str(values[0][0]),tab=self.tab,complete=True)
        store.put(job)
        return row,out["number"]


def finish(job,config,store,factory=None,stamp_factory=None):
    out=validate(job,config)
    if not out["enabled"]: return job
    if factory is None: factory=OutgoingSheets
    if not out.get("complete"):
        row,number=factory({**config,"_outgoing_family":out["ledger_type"]}).register(job,store)
        out=job["outgoing"]
        out.update(row=row,number=number,complete=True)
    if not out["stamp"].get("complete"):
        if stamp_factory is None:
            from stamp_ledger import StampSheets
            stamp_factory=StampSheets
        job.update(state="발신 완료·직인 등록 대기",reason="발신 등록 완료 · 직인날인대장 등록 중")
        store.put(job)
        row,number=stamp_factory(config).register(job,store)
        job["outgoing"]["stamp"].update(row=row,number=number,complete=True)
    store.put(job)
    return job
