"""Isolated, credential-free browser startup check for the frozen Windows build."""
import json
from pathlib import Path
import tempfile
import time

from browser_process import launch
from browser_sheets import BrowserUI


def check():
    import websocket
    with tempfile.TemporaryDirectory(prefix="sdr-browser-check-",ignore_cleanup_errors=True) as temporary:
        ui=BrowserUI({"_auth_data_dir":temporary,"spreadsheet_id":"startup-test"})
        ui._owned_fixture=True
        process=launch([str(ui.executable()),"--headless=new","--no-first-run","--no-default-browser-check","--remote-debugging-port=0","--remote-debugging-address=127.0.0.1",f"--user-data-dir={ui.profile}","about:blank"],Path(temporary)/"launch.log")
        socket=None
        try:
            deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                port,targets=ui.available()
                if port and any(t.get("url")=="about:blank" for t in targets):break
                if process.poll() not in (None,0):break
                time.sleep(.2)
            else:raise ValueError("Isolated browser did not expose its loopback endpoint")
            target=next((t for t in targets if t.get("url")=="about:blank"),None)
            if not target:raise ValueError("Isolated browser startup failed")
            socket=websocket.create_connection(target["webSocketDebuggerUrl"],timeout=10,suppress_origin=True,http_no_proxy=["127.0.0.1","localhost"])
            ui.ws=socket
            frame=ui.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
            # The owned about:blank fixture deliberately renders tabs after the name box.
            # Tab navigation and format-only paste require trusted keyboard input.
            html="""<input id="t-name-box"><div id="waffle-rich-text-editor" contenteditable="true">owned test grid</div><div id="tabs"></div><script>
const box=document.getElementById('t-name-box'),grid=document.getElementById('waffle-rich-text-editor');
window.fixtureClipboard='';window.rangeTrusted=false;window.copyTrusted=false;window.tabPointer=false;window.tabSwitches=0;window.formatCopies=[];
box.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.isTrusted){window.rangeTrusted=true;box.value=box.value.replace(/^A1:H/,'A2:H');grid.focus();e.preventDefault()}});
grid.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='End'&&e.isTrusted){box.value='K8';e.preventDefault()}});
document.addEventListener('copy',e=>{if(!e.isTrusted)return;e.preventDefault();window.copyTrusted=true;window.copiedRange=box.value;const match=box.value.match(/A(\\d+):H(\\d+)/);if(!match)return;let rows=[];for(let i=+match[1];i<=+match[2];i++){let row=Array(8).fill('');if(i===3)row=['연번','일자','문서번호','','발신처','제목','수신자','방법'];if(i===6)row=['1','2026-11-03','센터','581','시험기관','기존 제목','담당자','Email'];rows.push(row.join('\\t'))}window.fixtureClipboard=rows.join('\\r\\n')});
document.addEventListener('keydown',e=>{if(!e.isTrusted)return;if(e.ctrlKey&&e.altKey&&e.key.toLowerCase()==='v'){window.formatCopies.push([window.copiedRange,box.value]);e.preventDefault();return}if(!e.altKey||!['ArrowUp','ArrowDown'].includes(e.key))return;const tabs=[...document.querySelectorAll('.docs-sheet-tab')],current=tabs.findIndex(t=>t.classList.contains('docs-sheet-active-tab')),next=Math.max(0,Math.min(tabs.length-1,current+(e.key==='ArrowDown'?1:-1)));if(current<0)return;tabs.forEach((t,i)=>t.classList.toggle('docs-sheet-active-tab',i===next));window.tabSwitches++;e.preventDefault()});
setTimeout(()=>{document.getElementById('tabs').innerHTML=('<div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">수신 시험</span></div><div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">발신 시험</span></div>').normalize('NFD');
const tabs=[...document.querySelectorAll('.docs-sheet-tab')];tabs[0].classList.add('docs-sheet-active-tab');window.originalTabOrder=tabs.map(t=>t.textContent);tabs.forEach(n=>n.addEventListener('mousedown',()=>{window.tabPointer=true}));},500);
</script>"""
            ui.call("Page.setDocumentContent",{"frameId":frame,"html":html})
            ui.tab("수신 시험");ui.tab("발신 시험");ui.tab("수신 시험");ui.tab("발신 시험")
            # Use only this fixture's clipboard; no business data or OS clipboard.
            ui.clipboard=lambda value=None: ui.evaluate("window.fixtureClipboard") if value is None else ui.evaluate("window.fixtureClipboard="+json.dumps(value))
            rows=ui.read("A:H")
            assert len(rows)==8 and rows[0]==[""]*8 and rows[2][5]=="제목" and rows[5][5]=="기존 제목",rows
            assert ui.read("A6:H6")[0][3]=="581"
            assert ui.evaluate("window.rangeTrusted&&window.copyTrusted")
            ui.copy_format('C5','C6:E6')
            assert ui.evaluate("window.tabSwitches===3&&!window.tabPointer&&JSON.stringify(window.originalTabOrder)===JSON.stringify([...document.querySelectorAll('.docs-sheet-tab')].map(t=>t.textContent))")
            assert ui.evaluate("JSON.stringify(window.formatCopies)===JSON.stringify([['C5','C6:E6']])")
            # Reordered columns use trusted range selection / keyboard paste on this owned grid.
            from ledger_layout import normalize,schema,column
            from browser_sheets import BrowserSession
            import core
            physical=[27,15,24,18,22,20,13,25]
            layout=normalize({'columns':{k:column(c) for k,c in zip(schema('receipt','공문'),physical)},'header_range':'N3:AB4','data_start':5,'date_format':'YYYY.MM.DD'},'receipt','공문')
            mapped=[['']*28 for _ in range(8)]
            headers=['연번','일자','문서번호','','발신처','제목','담당자','방법']
            previous=['1','2026-11-03','센터','581','시험기관','기존 제목','담당자','Email']
            for row,values in ((2,headers),(4,previous)):
                for col,value in zip(physical,values):mapped[row][col]=value
            for row in mapped:row[0]='unmapped keep'
            ui.evaluate('window.mappedRows='+json.dumps(mapped))
            ui.evaluate(r"""(()=>{
const col=t=>[...t].reduce((a,c)=>a*26+c.charCodeAt(0)-64,0)-1;
document.addEventListener('copy',e=>{if(!e.isTrusted)return;const m=box.value.match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/);if(!m)return;e.preventDefault();window.fixtureClipboard=window.mappedRows.slice(+m[2]-1,+m[4]).map(r=>r.slice(col(m[1]),col(m[3])+1).join('\t')).join('\r\n')});
document.addEventListener('keydown',e=>{if(!e.isTrusted)return;const m=box.value.match(/^([A-Z]+)(\d+)$/);if(!m)return;const r=+m[2]-1,c=col(m[1]);if(e.ctrlKey&&!e.altKey&&e.key.toLowerCase()==='v'){e.preventDefault();let v=window.fixtureClipboard;const f=v.match(/^=([A-Z]+)(\d+)\+1$/);if(f)v=String(+window.mappedRows[+f[2]-1][col(f[1])]+1);if(v.startsWith("'"))v=v.slice(1);window.mappedRows[r][c]=v;}else if(e.key==='Delete'){e.preventDefault();window.mappedRows[r][c]=''}});
})()""")
            owner=core.Sheets.__new__(core.Sheets);owner.tab='발신 시험'
            config={'root':temporary,'_program_dir':str(Path(temporary)/'Program'),'spreadsheet_id':'startup-test','layout':layout}
            owner.browser=BrowserSession(config,owner,ui=ui)
            job={'hash':'owned-reordered','files':[{'hash':'owned-body','path':'fixture.pdf'}],'date':'2026-11-11','title':'열 변경 시험','issuer':'시험기관','recipient':'시험 담당','state':'확인 필요'}
            store=core.Store(Path(temporary)/'owned-history.sqlite')
            try:
                assert owner.register(job,store)==(6,'582')
                assert ui.evaluate('window.mappedRows[5][20]')=='열 변경 시험'
                assert ui.evaluate('window.mappedRows[5][15]')=='2026.11.11'
                assert ui.evaluate('window.mappedRows.every(r=>r[0]==="unmapped keep")')
                assert owner.register(job,store)==(6,'582')
            finally:store.db.close()
            try:ui.call("Browser.close")
            except websocket.WebSocketConnectionClosedException:pass
            return {"browser_started":True,"loopback_connection":True,"delayed_tabs":True,"trusted_tab_switch":True,"sheet_tab_order_preserved":True,"no_tab_pointer_drag":True,"trusted_format_only_paste":True,"hidden_first_row_alignment":True,"trusted_range_copy":True,"external_navigation":False,"reordered_ledger_registration_verified":True,"unmapped_columns_preserved":True}
        finally:
            if socket:socket.close()
            try:process.wait(timeout=10)
            except Exception:
                process.terminate();process.wait(timeout=10)
