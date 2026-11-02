"""User OAuth through Google's browser page; tokens protected with Windows DPAPI."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import uuid

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def auth_dir(config):
    return Path(config.get("_auth_data_dir") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SeoulDocumentRegister")


def protect(raw, decrypt=False):
    if os.name != "nt":
        raise OSError("Windows 사용자 보안 저장소가 필요합니다.")
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    source = ctypes.create_string_buffer(raw)
    entropy = ctypes.create_string_buffer(b"SeoulDocumentRegister.GoogleOAuth.v1")
    inp = Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    extra = Blob(len(entropy.raw)-1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(extra), None, None, 1, ctypes.byref(out))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(inp), "Google OAuth", ctypes.byref(extra), None, None, 1, ctypes.byref(out))
    if not ok:
        raise OSError("Google 로그인 정보를 Windows 보안 저장소에서 읽거나 저장하지 못했습니다. 다시 로그인해 주세요.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def client_config(config):
    candidates = [Path(config["oauth_client_path"])] if config.get("oauth_client_path") else [
        auth_dir(config) / "google-login.json",
        Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "google-login.json"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ValueError("Google 로그인 설정이 아직 없습니다. 설정에서 ‘데스크톱 앱 OAuth 클라이언트’ JSON을 한 번 지정해 주세요. 서비스 계정 키는 필요하지 않습니다.")
    if path.stat().st_size > 32768:
        raise ValueError("Google 로그인 설정 파일이 올바르지 않습니다.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    app = data.get("installed", {})
    if (app.get("token_uri") != "https://oauth2.googleapis.com/token"
            or app.get("auth_uri") not in ("https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth")
            or not str(app.get("client_id", "")).endswith(".apps.googleusercontent.com")
            or not app.get("client_secret")):
        raise ValueError("Google의 ‘데스크톱 앱 OAuth 클라이언트’ JSON을 선택해 주세요.")
    return data


def save_token(config, credentials):
    directory = auth_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ("google-token." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(protect(credentials.to_json().encode("utf-8")))
        temporary.replace(directory / "google-token.dpapi")
    finally:
        temporary.unlink(missing_ok=True)


def login(config):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(client_config(config), SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                       authorization_prompt_message=None,
                                       success_message="Google 연결을 마쳤습니다. 창을 닫고 공문 수신등록 프로그램으로 돌아가세요.",
                                       timeout_seconds=180, prompt="consent select_account", access_type="offline")
    if not credentials.refresh_token:
        raise ValueError("자동 연결 유지 권한을 받지 못했습니다. Google 로그인에서 접근을 다시 허용해 주세요.")
    save_token(config, credentials)
    return "본인 Google 계정으로 연결했습니다. ‘연결 확인’으로 접수대장 접근을 확인해 주세요."


def credentials(config):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError
    token = auth_dir(config) / "google-token.dpapi"
    if not token.is_file():
        raise ValueError("먼저 프로그램의 ‘Google 로그인’을 눌러 시트 편집 권한이 있는 계정으로 로그인해 주세요.")
    info = json.loads(protect(token.read_bytes(), decrypt=True).decode("utf-8"))
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        class BoundedRequest(Request):
            def __call__(self, *args, **kwargs):
                kwargs["timeout"] = 30
                return super().__call__(*args, **kwargs)
        try:
            creds.refresh(BoundedRequest())
        except RefreshError:
            raise ValueError("Google 연결이 만료 또는 해제되었습니다. ‘Google 로그인’으로 다시 연결해 주세요.") from None
        save_token(config, creds)
    return creds


def logout(config):
    (auth_dir(config) / "google-token.dpapi").unlink(missing_ok=True)


def has_login(config):
    return (auth_dir(config) / "google-token.dpapi").is_file()
