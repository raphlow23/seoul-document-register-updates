"""Signed shared-folder updates. No mailbox, document or Google key changes."""
from __future__ import annotations
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit,urljoin

from release_info import PRODUCT, UPDATE_PUBLIC_KEY, VERSION

MAX_EXE = 150 * 1024 * 1024


def fresh_environment():
    env=os.environ.copy()
    # A restarted one-file EXE must extract its own runtime, independent of the
    # helper's temporary directory and Tcl/Tk libraries which disappear on exit.
    for name in list(env):
        if name.startswith('_PYI_') or name in ('_MEIPASS2','TCL_LIBRARY','TK_LIBRARY'):
            env.pop(name,None)
    env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return env


def restart_target(target,cache,version,test=False,timeout=120):
    receipt=Path(cache)/('restart-'+uuid.uuid4().hex+'.json')
    arguments=[str(target),'--after-update',str(receipt)]
    if test:arguments.append('--test-restart')
    for attempt in range(3):
        receipt.unlink(missing_ok=True)
        process=subprocess.Popen(arguments,cwd=str(Path(target).parent),env=fresh_environment(),
                                 creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                ready=json.loads(receipt.read_text(encoding='utf-8'))
                if ready.get('status')=='ready' and ready.get('version')==version:
                    return {'status':'ready','receipt':str(receipt),'attempts':attempt+1}
                if ready.get('status')=='error':raise RuntimeError(ready.get('message','새 프로그램 시작 실패'))
            except (FileNotFoundError,json.JSONDecodeError):pass
            if process.poll() is not None:break
            time.sleep(.2)
        if process.poll() is None:
            raise TimeoutError('새 프로그램이 실행 중이지만 창 준비 응답이 없습니다. 업데이트 폴더의 시작 기록을 확인해 주세요.')
        time.sleep(1)
    raise RuntimeError('업데이트 후 프로그램이 시작 직후 종료되었습니다. 재실행 3회를 시도했습니다.')


def remote(source):
    value=str(source)
    if "://" in value and not value.startswith("https://"):raise ValueError("온라인 업데이트는 HTTPS 주소만 사용할 수 있습니다.")
    return value.startswith("https://")


def trusted_url(url):
    value=urlsplit(url)
    host=(value.hostname or "").lower()
    if value.scheme!="https" or value.username or value.password or value.port not in (None,443) or not (host=="github.com" or host.endswith(".githubusercontent.com")):
        raise ValueError("GitHub HTTPS 업데이트 주소를 확인해 주세요.")
    return url


def read_online(url,limit,target=None):
    import requests
    trusted_url(url)
    for hop in range(6):
        response=requests.get(trusted_url(url),stream=True,allow_redirects=False,timeout=(10,30),headers={"Cache-Control":"no-cache","User-Agent":PRODUCT+"/"+VERSION})
        if response.status_code not in (301,302,303,307,308):break
        try:url=trusted_url(urljoin(url,response.headers.get("Location","")))
        finally:response.close()
    else:raise ValueError("온라인 업데이트 주소의 이동 횟수가 너무 많습니다.")
    try:
        for item in [*response.history,response]:trusted_url(item.url)
        if response.status_code!=200:raise ValueError(f"온라인 업데이트 파일을 받지 못했습니다(HTTP {response.status_code}). 인터넷 연결과 GitHub 배포 상태를 확인해 주세요.")
        length=response.headers.get("Content-Length")
        if length and int(length)>limit:raise ValueError("온라인 배포 파일이 허용 크기를 넘습니다.")
        collected=bytearray();count=0
        for chunk in response.iter_content(chunk_size=1024*1024):
            count+=len(chunk)
            if count>limit:raise ValueError("온라인 배포 파일 크기가 안내와 다릅니다.")
            if target is not None:target.write(chunk)
            else:collected.extend(chunk)
        return bytes(collected)
    finally:response.close()


def read_manifest(source,public_key=UPDATE_PUBLIC_KEY):
    if remote(source):
        base=str(source).rstrip("/")
        if base.endswith("/latest.json"):base=base[:-len("/latest.json")]
        raw=read_online(base+"/latest.json",32768)
    else:
        path=Path(source)/"latest.json"
        try:
            if path.stat().st_size>32768:raise ValueError("업데이트 안내 파일이 너무 큽니다.")
            raw=path.read_bytes()
        except FileNotFoundError:return None
    return verify_manifest(json.loads(raw.decode("utf-8-sig")),public_key)


def download_to(source,manifest,filename,size,output):
    if remote(source):
        base=manifest.get("download_base") or str(source).rstrip("/").removesuffix("/latest.json")
        with Path(output).open("xb") as dst:read_online(trusted_url(base.rstrip("/")+"/"+filename),size,dst)
    else:
        with (Path(source)/filename).open("rb") as src,Path(output).open("xb") as dst:
            count=0
            while chunk:=src.read(1024*1024):
                count+=len(chunk)
                if count>size:raise ValueError("배포 파일 크기가 변경되었습니다.")
                dst.write(chunk)


def stage_component(source,data,name):
    manifest=read_manifest(source)
    if manifest is None:raise ValueError("업데이트 구성 파일을 찾지 못했습니다.")
    component=manifest.get("components",{}).get(name,{})
    filename=component.get("filename","");size=component.get("size",0);sha=component.get("sha256","")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.zip",filename) or not isinstance(size,int) or not 1024<size<100*1024*1024 or not re.fullmatch(r"[0-9a-f]{64}",sha):
        raise ValueError("업데이트 구성 파일의 안내 정보가 올바르지 않습니다.")
    cache=Path(data)/"updates"/"components";cache.mkdir(parents=True,exist_ok=True)
    result=cache/filename
    if result.is_file() and result.stat().st_size==size and file_hash(result)==sha:return result
    temporary=cache/(filename+"."+uuid.uuid4().hex+".part")
    try:
        download_to(source,manifest,filename,size,temporary)
        if temporary.stat().st_size!=size or file_hash(temporary)!=sha:raise ValueError("구성 파일의 서명된 해시를 확인하지 못했습니다.")
        os.replace(temporary,result)
    finally:temporary.unlink(missing_ok=True)
    return result


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", version):
        raise ValueError("업데이트 버전 형식이 올바르지 않습니다.")
    return tuple(map(int, version.split(".")))


def canonical(manifest):
    return json.dumps({k: v for k, v in manifest.items() if k != "signature"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_manifest(manifest, public_key=UPDATE_PUBLIC_KEY):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
            base64.b64decode(manifest["signature"], validate=True), canonical(manifest))
    except (InvalidSignature, ValueError, KeyError, TypeError):
        raise ValueError("업데이트 배포 서명을 확인하지 못했습니다.") from None
    version_tuple(manifest.get("version"))
    if manifest.get("product") != PRODUCT:
        raise ValueError("다른 프로그램의 업데이트입니다.")
    if manifest.get("filename") != f"{PRODUCT}-{manifest['version']}.exe":
        raise ValueError("업데이트 파일명이 올바르지 않습니다.")
    if not isinstance(manifest.get("size"), int) or not 1024 <= manifest["size"] <= MAX_EXE:
        raise ValueError("업데이트 파일 크기가 올바르지 않습니다.")
    if not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
        raise ValueError("업데이트 검증 정보가 올바르지 않습니다.")
    return manifest


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_exe(path, manifest):
    path = Path(path)
    if path.stat().st_size != manifest["size"] or file_hash(path) != manifest["sha256"]:
        raise ValueError("업데이트 파일이 미완성 또는 손상되어 적용하지 않습니다.")
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Windows 실행파일이 아닙니다.")


def check_and_stage(folder, data, current=VERSION, public_key=UPDATE_PUBLIC_KEY, progress=None):
    data=Path(data)
    manifest=read_manifest(folder,public_key)
    if manifest is None:return None
    if version_tuple(manifest["version"]) <= version_tuple(current):
        return None
    if progress:
        progress()
    cache = data / "updates"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / manifest["filename"]
    if target.exists():
        try:
            validate_exe(target, manifest)
            return {"manifest": manifest, "staged": str(target)}
        except ValueError:
            pass
    if not remote(folder) and (Path(folder)/manifest["filename"]).stat().st_size != manifest["size"]:
        raise ValueError("업데이트 파일이 아직 배포 중입니다. 다음 확인 때 다시 시도합니다.")
    temporary = cache / (manifest["filename"] + "." + uuid.uuid4().hex + ".part")
    try:
        download_to(folder,manifest,manifest["filename"],manifest["size"],temporary)
        validate_exe(temporary, manifest)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"manifest": manifest, "staged": str(target)}


def launch_helper(update, data):
    if not getattr(sys, "frozen", False):
        raise ValueError("자동 설치는 배포된 EXE에서 사용할 수 있습니다.")
    data = Path(data).resolve()
    cache = data / "updates"
    cache.mkdir(parents=True, exist_ok=True)
    validate_exe(update["staged"], verify_manifest(update["manifest"]))
    token = uuid.uuid4().hex
    # A separate copy can replace the installed EXE after its bootloader exits.
    helper = cache / f"helper-{token}.exe"
    shutil.copy2(sys.executable, helper)
    plan = {"parent_pid": os.getpid(), "target": str(Path(sys.executable).resolve()),
            "staged": update["staged"], "manifest": update["manifest"]}
    planpath = cache / f"plan-{token}.json"
    planpath.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    env = fresh_environment()
    subprocess.Popen([str(helper), "--apply-update", str(planpath)], cwd=str(cache), env=env,
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def wait_for_pid(pid, seconds=120):
    if os.name != "nt":
        return
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if kernel.GetLastError() == 87:  # process already exited
            return
        raise OSError("기존 프로그램 종료 여부를 확인할 수 없습니다.")
    try:
        if kernel.WaitForSingleObject(ctypes.c_void_p(handle), seconds * 1000) != 0:
            raise TimeoutError("기존 프로그램이 종료되지 않아 업데이트를 보류했습니다.")
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def apply_plan(planpath, public_key=UPDATE_PUBLIC_KEY, wait=wait_for_pid, launch=True, test_restart=False):
    planpath = Path(planpath).resolve()
    plan = json.loads(planpath.read_text(encoding="utf-8"))
    manifest = verify_manifest(plan["manifest"], public_key)
    cache = planpath.parent
    staged = Path(plan["staged"]).resolve()
    target = Path(plan["target"]).resolve()
    if staged.parent != cache or staged.name != manifest["filename"] or target.name != f"{PRODUCT}.exe":
        raise ValueError("업데이트 설치 경로가 올바르지 않습니다.")
    if target.parent == cache:
        raise ValueError("업데이트 캐시 안에는 설치할 수 없습니다.")
    validate_exe(staged, manifest)
    wait(int(plan["parent_pid"]))
    backup = target.with_name(target.name + ".previous")
    pending = target.with_name(target.name + ".updating")
    shutil.copyfile(staged, pending)
    validate_exe(pending, manifest)
    # The PyInstaller parent may release its EXE shortly after the Python child exits.
    for attempt in range(30):
        try:
            os.replace(target, backup)
            break
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(1)
    try:
        os.replace(pending, target)
        if launch:
            # No GUI and no updates during this health check. Restore on failure.
            env = fresh_environment()
            subprocess.run([str(target), "--self-test"], env=env, check=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        os.replace(backup, target)
        if launch:
            env = fresh_environment()
            subprocess.Popen([str(target)], cwd=str(target.parent), env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        raise
    result = {"version": manifest["version"], "status": "updated"}
    if launch:
        result['restart']=restart_target(target,cache,manifest['version'],test=test_restart)
    (cache / "last-result.json").write_text(json.dumps(result), encoding="utf-8")
    planpath.unlink(missing_ok=True)
    return result


def helper_main(planpath, launch=True):
    try:
        apply_plan(planpath, launch=launch)
    except Exception as error:
        cache = Path(planpath).resolve().parent
        (cache / "last-result.json").write_text(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), encoding="utf-8")
        if os.name == "nt" and launch:
            ctypes.windll.user32.MessageBoxW(None, f"업데이트를 완료하지 못했습니다. 기존 프로그램을 다시 실행해 주세요.\n{error}", "공문 수신등록 업데이트", 0)
        return 1
    return 0
