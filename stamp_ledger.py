"""Stamp ledger entry linked to a verified outgoing number; no physical stamping."""
from datetime import date
from urllib.parse import quote
from core import Sheets, RejectedWrite, value

STAMP_TAB="2026년 직인날인대장_공문"
FAMILIES=("공문","업무연락","처리지침")


def requests(sheet_id,row,out,previous,marker,candidate=None):
    stamp=out["stamp"]
    cells=[{"userEnteredValue":{"formulaValue":f"=A{row-1}+1"}},
           {"userEnteredValue":{"numberValue":(date.fromisoformat(stamp["date"])-date(1899,12,30)).days},"userEnteredFormat":{"numberFormat":{"type":"DATE","pattern":"yyyy.MM.dd"}}},
           *[value("○" if out["ledger_type"]==kind else "") for kind in FAMILIES],
           value(out["document_number"]),value(out["title"]),value(out["receiver"]),
           value(stamp["department"]),value(stamp["applicant"]),value(stamp["person"])]
    source={"sheetId":sheet_id,"startRowIndex":row-2,"endRowIndex":row-1,"startColumnIndex":0,"endColumnIndex":11}
    target={**source,"startRowIndex":row-1,"endRowIndex":row}
    result=[{"copyPaste":{"source":source,"destination":target,"pasteType":kind}} for kind in ("PASTE_FORMAT","PASTE_DATA_VALIDATION")]
    candidate=candidate or []
    if any(str(x).strip() for x in candidate): result=[]
    for col,cell in enumerate(cells):
        if col==0 and candidate and str(candidate[0]).strip(): continue
        result.append({"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":row-1,"columnIndex":col},
                                      "rows":[{"values":[cell]}],"fields":"userEnteredValue,userEnteredFormat.numberFormat" if col==1 else "userEnteredValue"}})
    result.append({"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":row-1,"columnIndex":6},"rows":[{"values":[{"note":marker}]}],"fields":"note"}})
    return result


class StampSheets(Sheets):
    last_column="K"
    title_column=6
    note_column="G"
    header_word="분"
    header_row=1
    notes_start=4
    marker_prefix="SDS:"

    def __init__(self,config):
        super().__init__({**config,"tab":STAMP_TAB})

    def register(self,job,store):
        out=job["outgoing"]
        stamp=out["stamp"]
        if not out.get("complete") or not out.get("document_number"):
            raise ValueError("발신번호를 확인한 뒤 직인날인대장을 등록해야 합니다.")
        if getattr(self,"browser",None):self.browser.recover(["SDS:"+out["id"]])
        prop,rows,found=self.snapshot()
        marker="SDS:"+out["id"]
        if marker in found:
            row=found[marker]
        else:
            if stamp.get("sheet_attempted"):
                raise ValueError("직인날인대장 등록 결과가 불확실합니다. 대장에서 먼저 확인해 주세요.")
            occupied=[i+1 for i,r in enumerate(rows) if i>=3 and len(r)>6 and str(r[6]).strip()]
            if not occupied: raise ValueError("직인날인대장 기존 기준 행을 찾지 못했습니다.")
            row=max(occupied)+1
            if row>prop["gridProperties"]["rowCount"]: raise ValueError("직인날인대장에 빈 행을 추가해 주세요.")
            previous=rows[row-2]
            candidate=rows[row-1] if row<=len(rows) else []
            if not previous or not str(previous[0]).isdigit(): raise ValueError("직인날인대장 앞 행의 연번을 확인해 주세요.")
            if any(str(x).strip() for x in candidate[2:]): raise ValueError("직인날인대장 다음 행에 기존 내용이 있습니다.")
            if candidate and str(candidate[0]).strip() and (not str(candidate[0]).isdigit() or int(candidate[0])!=int(previous[0])+1):
                raise ValueError("직인날인대장에 미리 입력된 연번이 앞 행과 맞지 않습니다.")
            if len(candidate)>1 and str(candidate[1]).strip() and candidate[1].rstrip(".").replace(".","-")!=stamp["date"]:
                raise ValueError("직인날인대장 다음 행의 날짜를 확인해 주세요.")
            old=getattr(self,"notes",{}).get(row,"")
            stamp["sheet_attempted"]=True
            store.put(job)
            try:
                self.request("POST",":batchUpdate",json={"requests":requests(prop["sheetId"],row,out,previous,(old+"\n" if old else "")+marker,candidate)})
            except RejectedWrite:
                stamp["sheet_attempted"]=False
                store.put(job)
                raise
        tab="'"+self.tab.replace("'","''")+"'"
        values=self.request("GET","/values/"+quote(f"{tab}!F{row}:G{row}",safe=""),params={"valueRenderOption":"FORMATTED_VALUE"}).get("values",[])
        if not values or len(values[0])<2 or values[0][0]!=out["document_number"] or values[0][1]!=out["title"]:
            raise ValueError("직인날인대장의 문서번호·제목을 확인하지 못했습니다.")
        serial=rows[row-1][0] if row<=len(rows) and rows[row-1] else str(int(rows[row-2][0])+1)
        stamp.update(row=row,number=str(serial),complete=True)
        store.put(job)
        return row,str(serial)
