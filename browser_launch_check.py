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
            # It accepts real mouse input only, matching Sheets' tab controls.
            html="""<input id="t-name-box"><div id="waffle-rich-text-editor" contenteditable="true">owned test grid</div><div id="tabs"></div><script>
const box=document.getElementById('t-name-box'),grid=document.getElementById('waffle-rich-text-editor');
window.fixtureClipboard='';window.rangeTrusted=false;window.copyTrusted=false;
box.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.isTrusted){window.rangeTrusted=true;box.value=box.value.replace(/^A1:H/,'A2:H');grid.focus();e.preventDefault()}});
grid.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='End'&&e.isTrusted){box.value='K8';e.preventDefault()}});
document.addEventListener('copy',e=>{if(!e.isTrusted)return;window.copyTrusted=true;const match=box.value.match(/A(\\d+):H(\\d+)/);if(!match)return;let rows=[];for(let i=+match[1];i<=+match[2];i++){let row=Array(8).fill('');if(i===3)row=['연번','일자','문서번호','','발신처','제목','수신자','방법'];if(i===6)row=['1','2026-11-03','센터','581','시험기관','기존 제목','담당자','Email'];rows.push(row.join('\\t'))}window.fixtureClipboard=rows.join('\\r\\n');e.preventDefault()});
setTimeout(()=>{document.getElementById('tabs').innerHTML=('<div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">수신 시험</span></div><div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">발신 시험</span></div>').normalize('NFD');
document.querySelectorAll('.docs-sheet-tab').forEach(n=>n.addEventListener('mousedown',e=>{if(!e.isTrusted)return;document.querySelectorAll('.docs-sheet-tab').forEach(t=>t.classList.remove('docs-sheet-active-tab'));n.classList.add('docs-sheet-active-tab')}));},500);
</script>"""
            ui.call("Page.setDocumentContent",{"frameId":frame,"html":html})
            ui.tab("수신 시험");ui.tab("발신 시험")
            # Use only this fixture's clipboard; no business data or OS clipboard.
            ui.clipboard=lambda value=None: ui.evaluate("window.fixtureClipboard") if value is None else ui.evaluate("window.fixtureClipboard="+json.dumps(value))
            rows=ui.read("A:H")
            assert len(rows)==8 and rows[0]==[""]*8 and rows[2][5]=="제목" and rows[5][5]=="기존 제목",rows
            assert ui.read("A6:H6")[0][3]=="581"
            assert ui.evaluate("window.rangeTrusted&&window.copyTrusted")
            try:ui.call("Browser.close")
            except websocket.WebSocketConnectionClosedException:pass
            return {"browser_started":True,"loopback_connection":True,"delayed_tabs":True,"trusted_tab_switch":True,"hidden_first_row_alignment":True,"trusted_range_copy":True,"external_navigation":False}
        finally:
            if socket:socket.close()
            try:process.wait(timeout=10)
            except Exception:
                process.terminate();process.wait(timeout=10)
