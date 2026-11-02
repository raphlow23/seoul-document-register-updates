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
            socket.send(json.dumps({"id":1,"method":"Browser.close"}))
            try:socket.recv()
            except websocket.WebSocketConnectionClosedException:pass
            return {"browser_started":True,"loopback_connection":True,"external_navigation":False}
        finally:
            if socket:socket.close()
            try:process.wait(timeout=10)
            except Exception:
                process.terminate();process.wait(timeout=10)
