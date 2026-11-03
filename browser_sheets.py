"""Google Sheets through the visible browser UI, without Google Cloud/API credentials.

Uses only sheet tabs, the name box, copy/paste and a shared write-ahead journal.
No Google private network endpoints or browser password/cookie extraction.
"""
import csv
from datetime import date,timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.parse import unquote
from urllib.request import build_opener,ProxyHandler
from urllib.parse import urlsplit
from browser_process import launch as launch_browser


class BrowserUI:
    def __init__(self,config):
        self.config=config
        self.data=Path(config.get("_auth_data_dir") or Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"SeoulDocumentRegister")
        self.profile=self.data/"sheet-browser"
        self.profile.mkdir(parents=True,exist_ok=True)
        self.sid=config["spreadsheet_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+",self.sid):raise ValueError("구글 시트 ID를 확인해 주세요.")
        self.ws=None;self.seq=0

    def executable(self):
        bins=[Path(os.environ.get(key,"C:/"))/rel for key in ("PROGRAMFILES(X86)","PROGRAMFILES","LOCALAPPDATA") for rel in ("Microsoft/Edge/Application/msedge.exe","Google/Chrome/Application/chrome.exe")]
        exe=next((p for p in bins if p.is_file()),None)
        if not exe:raise ValueError("브라우저 연결에는 Microsoft Edge 또는 Google Chrome이 필요합니다.")
        return exe

    def available(self):
        try:
            port=int((self.profile/"DevToolsActivePort").read_text().splitlines()[0])
            if not 1<=port<=65535:return None,[]
            with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/json/list",timeout=1) as response:
                targets=json.load(response)
            if not isinstance(targets,list):return None,[]
            return port,targets
        except (OSError,ValueError,IndexError):return None,[]

    def open_login(self):
        if self.available()[0] is not None:
            raise ValueError("열린 ‘전용 시트 브라우저’ 창을 먼저 모두 닫은 뒤 ‘시트 로그인’을 다시 눌러 주세요. 로그인은 일반 브라우저 모드에서 직접 진행합니다.")
        return launch_browser([str(self.executable()),f"--user-data-dir={self.profile}","--no-first-run","--no-default-browser-check",f"https://docs.google.com/spreadsheets/d/{self.sid}/edit"],self.data/"browser-launch.log")

    def connect(self):
        import websocket
        port,targets=self.available()
        if port is None:
            process=launch_browser([str(self.executable()),"--remote-debugging-port=0","--remote-debugging-address=127.0.0.1",f"--user-data-dir={self.profile}","--no-first-run","--no-default-browser-check",f"https://docs.google.com/spreadsheets/d/{self.sid}/edit"],self.data/"browser-launch.log")
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                port,targets=self.available()
                if port:break
                if process.poll() not in (None,0):break
                time.sleep(.2)
            if port is None:raise ValueError("시트 브라우저 연결을 확인하지 못했습니다. 로그인한 전용 브라우저 창을 모두 닫고 ‘연결 확인’을 다시 눌러 주세요. 계속 실패하면 PC의 Edge/Chrome 실행 정책을 확인해야 합니다. 실행 기록: "+str(self.data/"browser-launch.log"))
        target=next((t for t in targets if t.get("type")=="page" and f"/spreadsheets/d/{self.sid}/" in t.get("url","")),None)
        if target is None:
            # Inspect the existing page before attempting any navigation.
            target=next((t for t in targets if t.get("type")=="page"),None)
            if target is None:raise ValueError("열린 시트 브라우저 탭을 찾지 못했습니다.")
        endpoint=target["webSocketDebuggerUrl"]
        address=urlsplit(endpoint)
        if address.scheme!="ws" or address.hostname not in ("127.0.0.1","localhost") or address.port!=port:
            raise ValueError("이 PC의 시트 브라우저 연결 주소가 올바르지 않습니다.")
        if urlsplit(target.get("url","")).hostname=="accounts.google.com":
            raise ValueError("Google 로그인이 필요합니다. 전용 시트 브라우저 창을 모두 닫고 ‘시트 로그인’을 누르세요. 일반 브라우저에서 로그인과 시트 표시를 확인한 뒤 그 창을 닫고 ‘연결 확인’을 눌러 주세요.")
        self.ws=websocket.create_connection(endpoint,timeout=15,suppress_origin=True,http_no_proxy=["127.0.0.1","localhost"])
        self.call("Page.bringToFront")
        if f"/spreadsheets/d/{self.sid}/" not in target.get("url",""):
            self.call("Page.navigate",{"url":f"https://docs.google.com/spreadsheets/d/{self.sid}/edit"})
        return self

    def close(self):
        if self.ws:self.ws.close();self.ws=None

    def call(self,method,params=None):
        self.seq+=1;seq=self.seq
        self.ws.send(json.dumps({"id":seq,"method":method,"params":params or {}}))
        while True:
            result=json.loads(self.ws.recv())
            if result.get("id")!=seq:continue
            if "error" in result:raise RuntimeError("브라우저 조작 실패: "+result["error"].get("message",""))
            return result.get("result",{})

    def evaluate(self,expression):
        r=self.call("Runtime.evaluate",{"expression":expression,"returnByValue":True})
        if r.get("exceptionDetails"):raise RuntimeError("시트 화면 조작에 실패했습니다.")
        return r.get("result",{}).get("value")

    def key(self,key,ctrl=False):
        vk={"Enter":13,"Escape":27,"Delete":46,"End":35,"Home":36,"Tab":9}.get(key,ord(key.upper()) if len(key)==1 else 0)
        mod=2 if ctrl else 0
        code="Key"+key.upper() if len(key)==1 and key.isalpha() else key
        self.call("Input.dispatchKeyEvent",{"type":"keyDown","key":key,"code":code,"windowsVirtualKeyCode":vk,"modifiers":mod})
        self.call("Input.dispatchKeyEvent",{"type":"keyUp","key":key,"code":code,"windowsVirtualKeyCode":vk,"modifiers":mod})

    def ready(self):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if self.evaluate("!!document.querySelector('#t-name-box')"):return
            time.sleep(.25)
        raise ValueError("시트 편집 화면을 확인하지 못했습니다. 로그인이 필요하면 이 전용 브라우저 창을 모두 닫고 ‘시트 로그인’을 누르세요. 일반 브라우저에서 직접 로그인하고 시트 표시 확인 후 창을 닫은 다음 ‘연결 확인’을 누르세요.")

    def tab(self,name):
        from browser_tabs import probe
        self.ready()
        deadline=time.monotonic()+25
        clicked=False;names=[]
        while time.monotonic()<deadline:
            state=self.evaluate(probe(name)) or {}
            names=state.get("names",names)
            if state.get("matches",0)>1:
                raise ValueError("같은 이름으로 보이는 시트 탭이 여러 개입니다. 시트에서 탭 이름을 확인해 주세요: "+name)
            if state.get("active"):return
            if state.get("matches")==1 and not clicked:
                state=self.evaluate(probe(name,scroll=True)) or {}
                if state.get("visible"):
                    point={"x":state["x"],"y":state["y"]}
                    self.call("Input.dispatchMouseEvent",{"type":"mouseMoved",**point})
                    self.call("Input.dispatchMouseEvent",{"type":"mousePressed","button":"left","buttons":1,"clickCount":1,**point})
                    self.call("Input.dispatchMouseEvent",{"type":"mouseReleased","button":"left","buttons":0,"clickCount":1,**point})
                    clicked=True
            time.sleep(.2)
        actual=" / ".join(names) if names else "탭 목록이 아직 표시되지 않았습니다"
        raise ValueError("시트 탭을 확인하지 못했습니다: "+name+"\n현재 화면의 탭: "+actual+"\n시트 로딩이 끝난 뒤 ‘연결 확인’을 다시 눌러 주세요. 설정의 구글 시트 ID도 확인해 주세요.")

    def select(self,range_):
        self.call("Page.bringToFront")
        point=self.evaluate("""(()=>{const e=document.querySelector('#t-name-box');if(!e)return null;const r=e.getBoundingClientRect();return r.width&&r.height?{x:r.x+r.width/2,y:r.y+r.height/2}:null})()""")
        if not point:raise ValueError("시트의 범위 입력란을 찾지 못했습니다.")
        self.call("Input.dispatchMouseEvent",{"type":"mousePressed","button":"left","buttons":1,"clickCount":1,**point})
        self.call("Input.dispatchMouseEvent",{"type":"mouseReleased","button":"left","buttons":0,"clickCount":1,**point})
        if not self.evaluate("document.activeElement===document.querySelector('#t-name-box')"):
            raise ValueError("범위 입력란에 포커스를 맞추지 못했습니다. 시트 창을 확인해 주세요.")
        self.key("a",ctrl=True);self.call("Input.insertText",{"text":range_});self.key("Enter")
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            state=self.selection()
            if state.get("range") and state.get("active")!="t-name-box":return state["range"]
            time.sleep(.1)
        raise ValueError("셀 범위 선택이 끝나지 않았습니다. 시트 창의 열려 있는 메뉴나 안내창을 닫아 주세요.")

    def selection(self):
        return self.evaluate("({range:document.querySelector('#t-name-box')?.value||'',active:document.activeElement?.id||''})") or {}

    def grid_rows(self):
        from browser_ranges import bounds
        self.select("A1");self.key("End",ctrl=True);time.sleep(.25)
        selected=self.selection().get("range","")
        row,_,last,_=bounds(selected)
        self._grid_row_count=max(row,last)
        return self._grid_row_count

    @staticmethod
    def clipboard(value=None):
        import win32clipboard as cb
        for _ in range(30):
            try:cb.OpenClipboard();break
            except Exception:time.sleep(.04)
        else:raise ValueError("클립보드를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.")
        try:
            if value is not None:cb.EmptyClipboard();cb.SetClipboardText(value,cb.CF_UNICODETEXT);return value
            return cb.GetClipboardData(cb.CF_UNICODETEXT) if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT) else ""
        finally:cb.CloseClipboard()

    def read(self,range_):
        from browser_ranges import align,bounds
        columns=re.fullmatch(r"([A-Z]+):([A-Z]+)",range_)
        if columns:
            range_=columns[1]+"1:"+columns[2]+str(self.grid_rows())
        r1,c1,r2,c2=bounds(range_)
        if (r2-r1+1)*(c2-c1+1)>50000:raise ValueError("읽을 대장 범위가 너무 큽니다. 범위를 줄여 확인해 주세요.")
        detail=""
        for _ in range(3):
            selected=self.select(range_)
            sentinel="SDR-copy-"+uuid.uuid4().hex
            self.clipboard(sentinel);self.key("c",ctrl=True)
            deadline=time.monotonic()+3
            previous=None
            while time.monotonic()<deadline:
                text=self.clipboard()
                if text and text!=sentinel:
                    if len(text)>5_000_000:raise ValueError("시트 복사 범위가 너무 큽니다.")
                    if text==previous:
                        try:return align(text,range_,selected)
                        except ValueError as error:detail=str(error);break
                    previous=text
                time.sleep(.1)
        raise ValueError(detail or "시트 내용을 복사하지 못했습니다. 전용 시트 창이 편집 화면인지 확인하고 열린 안내창을 닫은 뒤 다시 연결해 주세요.")

    def write(self,cell,value):
        from browser_ranges import bounds
        selected=self.select(cell)
        if bounds(selected)!=bounds(cell):raise ValueError("입력할 셀과 실제 선택 셀이 다릅니다. 숨김·필터 상태를 확인해 주세요. 입력하지 않았습니다.")
        if value:self.clipboard(value);self.key("v",ctrl=True)
        else:self.key("Delete")
        time.sleep(.2)


def normalized(row,owner):
    row=[str(v) for v in row]+[""]*11
    numeric=(0,) if owner.marker_prefix=="SDS:" else (0,getattr(owner,"number_col",3))
    for col in numeric:
        if row[col].isdigit():row[col]=str(int(row[col]))
    text=row[1].strip()
    m=re.fullmatch(r"(20\d\d)[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})(?:일|\.)?",text)
    if m:
        try:row[1]=date(*map(int,m.groups())).isoformat()
        except ValueError:pass
    return row[:ord(owner.last_column)-64]


class BrowserSession:
    def __init__(self,config,owner,ui=None):
        self.config,self.owner=config,owner
        self.ui=ui or BrowserUI(config).connect()
        self.journal=Path(config["root"])/".수발신-브라우저이력"
        self.sid=config["spreadsheet_id"]
        self.rows=[]

    def close(self):self.ui.close()

    def getrows(self,range_):return [normalized(r,self.owner) for r in self.ui.read(range_)]

    def _check_entry(self,entry):
        row=entry["row"]
        current=self.getrows(f"A{row}:{self.owner.last_column}{row}")[0]
        before,after=entry["before"],entry["after"]
        if any(v not in (before[i],after[i]) for i,v in enumerate(current)):
            raise ValueError(f"브라우저 등록 {row}행이 다른 내용으로 바뀌었습니다. 자동 복구를 중단했습니다.")
        if not entry.get("complete"):
            for col,value in entry["writes"].items():
                i=int(col)
                if current[i]!=after[i]:self.ui.write(f"{chr(65+i)}{row}",value)
            current=self.getrows(f"A{row}:{self.owner.last_column}{row}")[0]
        if current!=after:raise ValueError(f"브라우저 등록 {row}행의 저장 결과가 다릅니다. 시트에서 확인해 주세요.")
        return current

    @staticmethod
    def save(path,entry):
        temp=path.with_suffix("."+uuid.uuid4().hex+".tmp")
        temp.write_text(json.dumps(entry,ensure_ascii=False),encoding="utf-8")
        os.replace(temp,path)

    def recover(self,markers):
        # A connection test/snapshot is read-only. Resume only a document explicitly being registered.
        self.ui.tab(self.owner.tab)
        if not self.journal.is_dir():return
        for path in self.journal.glob("*.json"):
            entry=json.loads(path.read_text(encoding="utf-8"))
            if entry["sid"]!=self.sid or entry["tab"]!=self.owner.tab or entry.get("complete"):continue
            if not set(markers).intersection(entry["markers"]):continue
            self._check_entry(entry);entry["complete"]=True;self.save(path,entry)

    def snapshot(self):
        self.ui.tab(self.owner.tab)
        self.journal.mkdir(exist_ok=True)
        found={};entries=[]
        for path in self.journal.glob("*.json"):
            entry=json.loads(path.read_text(encoding="utf-8"))
            if entry["sid"]!=self.sid or entry["tab"]!=self.owner.tab:continue
            entries.append(entry)
        rows=self.getrows(f"A:{self.owner.last_column}")
        header=rows[self.owner.header_row] if len(rows)>self.owner.header_row else []
        if len(header)<=self.owner.title_column or "제" not in header[self.owner.title_column] or self.owner.header_word not in header[2]:
            raise ValueError(f"{self.owner.tab}의 {self.owner.header_row+1}행에서 제목·문서분류 열을 확인하지 못했습니다. 선택 범위와 숨김·필터 상태를 확인해 주세요. 등록하지 않았습니다.")
        # Identity fields prove matches; handwritten stamp-person and other later edits stay editable.
        identity={0,self.owner.title_column,5 if self.owner.marker_prefix=="SDS:" else getattr(self.owner,"number_col",3)}
        for entry in entries:
            if entry.get("complete") and entry["row"]<=len(rows) and all(rows[entry["row"]-1][c]==entry["after"][c] for c in identity):
                for marker in entry["markers"]:found[marker]=entry["row"]
        self.rows=rows;self.owner.notes={}
        return {"sheetId":0,"gridProperties":{"rowCount":getattr(self.ui,"_grid_row_count",max(len(rows),1000))}},rows,found

    def request(self,method,suffix="",**kwargs):
        self.intent_written=False
        try:return self._request(method,suffix,**kwargs)
        except Exception as error:
            if method=="POST" and not self.intent_written:
                from core import RejectedWrite
                raise RejectedWrite(str(error)) from error
            raise

    def _request(self,method,suffix="",**kwargs):
        if method=="GET" and suffix.startswith("/values/"):
            text=unquote(suffix[len("/values/"):]);range_=text.rsplit("!",1)[-1]
            return {"values":self.getrows(range_) if range_.startswith("A") else self.ui.read(range_)}
        if method!="POST":raise ValueError("지원하지 않는 브라우저 시트 작업입니다.")
        updates={};markers=[];row=None
        for req in kwargs["json"]["requests"]:
            cell=req.get("updateCells")
            if not cell:continue
            r=cell["start"]["rowIndex"]+1
            if row is not None and row!=r:raise ValueError("한 번에 한 대장 행만 등록할 수 있습니다.")
            row=r;col=cell["start"]["columnIndex"];v=cell["rows"][0]["values"][0]
            if "note" in v:markers.extend(x for x in v["note"].splitlines() if x.startswith(("SDR:","SDO:","SDS:")))
            if "userEnteredValue" in v:updates[col]=v["userEnteredValue"]
        if not markers or not row:raise ValueError("브라우저 중복 방지 표식이 없습니다.")
        before=self.getrows(f"A{row}:{self.owner.last_column}{row}")[0]
        expected=self.rows[row-1] if row<=len(self.rows) else [""]*len(before)
        if before!=expected:raise ValueError("등록 직전에 시트 행이 바뀌었습니다. 다시 확인해 주세요.")
        after=before.copy();writes={}
        for col,v in updates.items():
            if "formulaValue" in v:
                formula=v["formulaValue"];match=re.fullmatch(r"=([A-K])(\d+)\+1",formula)
                if not match or int(match[2])!=row-1:raise ValueError("지원하지 않는 번호 수식입니다.")
                value=str(int(self.rows[row-2][ord(match[1])-65])+1);write=formula
            elif "numberValue" in v:
                value=(date(1899,12,30)+timedelta(days=v["numberValue"])).isoformat() if col==1 else str(v["numberValue"])
                write=value
            else:
                value=str(v.get("stringValue",""));write="'"+value if value.startswith(("=","+","-","@")) else value
            if any(c in value for c in "\t\r\n"):raise ValueError("대장 입력 항목의 탭·줄바꿈을 공백으로 바꿔 주세요.")
            after[col]=value;writes[str(col)]=write
        entry={"sid":self.sid,"tab":self.owner.tab,"row":row,"before":before,"after":after,"writes":writes,"markers":markers,"complete":False}
        path=self.journal/(hashlib.sha256((self.sid+self.owner.tab+markers[0]).encode()).hexdigest()+".json")
        self.save(path,entry)  # Durable intent BEFORE any sheet edit; partial writes recover conditionally.
        self.intent_written=True
        self._check_entry(entry)
        entry["complete"]=True;self.save(path,entry)
        return {}
