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
            html="""<input id="t-name-box"><div id="tabs"></div><script>
setTimeout(()=>{document.getElementById('tabs').innerHTML=('<div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">수신 시험</span></div><div class="docs-sheet-tab" style="display:inline-block;padding:15px"><span class="docs-sheet-tab-name">발신 시험</span></div>').normalize('NFD');
document.querySelectorAll('.docs-sheet-tab').forEach(n=>n.addEventListener('mousedown',e=>{if(!e.isTrusted)return;document.querySelectorAll('.docs-sheet-tab').forEach(t=>t.classList.remove('docs-sheet-active-tab'));n.classList.add('docs-sheet-active-tab')}));},500);
</script>"""
            ui.call("Page.setDocumentContent",{"frameId":frame,"html":html})
            ui.tab("수신 시험");ui.tab("발신 시험")
            try:ui.call("Browser.close")
            except websocket.WebSocketConnectionClosedException:pass
            return {"browser_started":True,"loopback_connection":True,"delayed_tabs":True,"trusted_tab_switch":True,"external_navigation":False}
        finally:
            if socket:socket.close()
            try:process.wait(timeout=10)
            except Exception:
                process.terminate();process.wait(timeout=10)
