"""Build and publish public runtime files on GitHub; signing stays on the maintainer PC."""
import base64
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import requests
from release_info import PRODUCT, VERSION, UPDATE_REPOSITORY, UPDATE_PUBLIC_KEY

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com/repos/" + UPDATE_REPOSITORY
RELEASE_TITLE='작업표시줄 아이콘 확대·하단 안내·발신 초기화'
RELEASE_BODY='기존 아이콘의 문서·화살표·색상과 투명 배경을 유지하면서 작은 Windows 아이콘 프레임의 그림 너비를 약 33% 키웠습니다. 메인 화면 하단 안내의 높이를 먼저 확보하고 창 너비에 맞춰 줄바꿈해 글자 잘림을 수정했습니다. 발신 작성 목록에 발신 초기화·다시 시작과 목록 삭제를 추가했습니다. 기존 발신·직인 등록 진행은 새 작성에 재사용하지 않고 양식 선택부터 시작하며 등록 시 최신 대장에서 새 번호를 받습니다. 수신 접수·기존 구글시트 행·파일은 보존합니다. 206개 검사와 실제 설치 파일의 화면·아이콘·새 발신 진행·자동 업데이트 및 재실행을 검증하며 최신·직전 릴리스만 보관합니다.'
_release_id = None


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def headers():
    return {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json", "Cache-Control":"no-cache"}


def info(create=False):
    global _release_id
    if _release_id is not None:
        response=requests.get(f"{API}/releases/{_release_id}",headers=headers(),timeout=30)
        response.raise_for_status();result=response.json()
    else:
        response=requests.get(f"{API}/releases",params={"per_page":100},headers=headers(),timeout=30)
        response.raise_for_status()
        result=next((r for r in response.json() if r["tag_name"]=="v"+VERSION),None)
    if result is None and create:
        response=requests.post(f"{API}/releases",headers=headers(),json={"tag_name":"v"+VERSION,"target_commitish":"main","name":"v"+VERSION+" — "+RELEASE_TITLE,"draft":True,"prerelease":False,"body":RELEASE_BODY},timeout=30)
        response.raise_for_status();result=response.json()
    elif result is not None and create:
        assert result['draft'], 'Published releases must not be rebuilt'
        response=requests.patch(f"{API}/releases/{result['id']}",headers=headers(),json={'name':'v'+VERSION+' — '+RELEASE_TITLE,'body':RELEASE_BODY},timeout=30)
        response.raise_for_status();result=response.json()
    if result is None:raise ValueError("No draft release for "+VERSION)
    assert result["tag_name"] == "v" + VERSION and result["draft"]
    _release_id=result["id"]
    return result


def upload(path):
    release=info();release_id=release["id"]
    for asset in release["assets"]:
        if asset["name"]==path.name:
            response=requests.delete(asset["url"],headers=headers(),timeout=30);response.raise_for_status()
    with path.open("rb") as stream:
        response=requests.post(f"https://uploads.github.com/repos/{UPDATE_REPOSITORY}/releases/{release_id}/assets",params={"name":path.name},headers={**headers(),"Content-Type":"application/octet-stream","Content-Length":str(path.stat().st_size)},data=stream,timeout=(30,300))
    response.raise_for_status(); asset=response.json()
    assert asset["state"]=="uploaded" and asset["size"]==path.stat().st_size
    assert asset["digest"]=="sha256:"+digest(path)


def build():
    from prepare_spacing import component, corresponding_source
    from public_assets import verify
    out=ROOT/"public-build";out.mkdir(exist_ok=True)
    exe=out/f"{PRODUCT}-{VERSION}.exe";shutil.copy2(ROOT/"dist"/f"{PRODUCT}.exe",exe)
    from ocr_runtime_info import COMPONENT_NAME,RUNTIME_ID
    native=out/COMPONENT_NAME
    assert native.is_file() and exe.stat().st_size<90*1024*1024
    from PyInstaller.archive.readers import CArchiveReader
    assert not any('ocr-runtime' in n.lower() for n in CArchiveReader(str(exe)).toc)
    ocr_data=out/'ocr-test-data'
    subprocess.run([str(exe),'--prepare-ocr-runtime',str(native),str(ocr_data)],check=True,timeout=90)
    seed=out/"empty-seed.json";seed.write_text('{"root":"","spreadsheet_id":""}')
    print("Public executable check:",verify(exe,seed));seed.unlink()
    subprocess.run([str(exe),'--ocr-data',str(ocr_data),"--self-test"],check=True,timeout=90)
    from PIL import Image,ImageDraw,ImageFont
    sample=out/'ocr-fixture.png';image=Image.new('RGB',(1600,300),'white')
    ImageDraw.Draw(image).text((70,80),'OCR TEST 2026',font=ImageFont.truetype('arial.ttf',72),fill='black');image.save(sample);image.close()
    result=out/'ocr-result.json'
    subprocess.run([str(exe),'--ocr-data',str(ocr_data),'--test-ocr',str(sample),str(result)],check=True,timeout=90)
    assert '2026' in json.loads(result.read_text(encoding='utf-8'))['text']
    subprocess.run([str(exe),"--test-workflow"],check=True,timeout=90)
    subprocess.run([sys.executable,"-m","unittest","-q","test_browser_startup","test_queue_visibility","test_browser_tabs","test_template_library","test_outgoing_folders","test_release_retention","test_browser_ranges","test_file_drop","test_workflow_navigation","test_template_defaults","test_hwp_anchor_slots","test_file_rollback","test_receipt_attachments","test_zip_intake","test_browser_sheets","test_attachment_files","test_ocr_review","test_folder_name_picker","test_updater","test_child_folder_editor","test_receipt_identity","test_stamp_layout","test_receipt_locations","test_relief_intake","test_ocr_runtime","test_modern_ui","test_native_dependencies","test_relief_title_step","test_taskbar_icon","test_settings_layout","test_outgoing_restart"],check=True,timeout=90)
    taskbar=out/'taskbar-check.json'
    taskbar_run=subprocess.run([str(exe),'--test-taskbar',str(taskbar)],timeout=90)
    if taskbar_run.returncode:
        error=Path(str(taskbar)+'.error.txt')
        if error.is_file():print(error.read_text(encoding='utf-8'),flush=True)
        taskbar_run.check_returncode()
    taskbar_result=json.loads(taskbar.read_text(encoding='utf-8'))
    assert taskbar_result['big_icon'] and taskbar_result['small_icon'] and taskbar_result['owned_shortcut_repaired']
    print('Native taskbar and shortcut verification:',taskbar.read_text(encoding='utf-8'))
    from updater import restart_target
    print("Frozen post-update GUI readiness:", restart_target(exe,out,VERSION,test=True))
    startup=out/"browser-startup.json"
    subprocess.run([str(exe),"--test-browser-startup",str(startup)],check=True,timeout=90)
    print("Frozen browser startup check:",startup.read_text())
    model=out/"SpacingModel-0.20.0.zip";component(model)
    licenses=out/"licenses";licenses.mkdir(exist_ok=True)
    for dist in metadata.distributions():
        name=dist.metadata.get("Name","package");target=licenses/name;target.mkdir(exist_ok=True)
        for file in dist.files or []:
            if any(any(word in p.lower() for word in ("license","copying","notice")) for p in file.parts) and dist.locate_file(file).is_file():
                shutil.copy2(dist.locate_file(file),target/"_".join(file.parts))
        (target/"PACKAGE.txt").write_text(f"{name} {dist.version}\n",encoding="utf-8")
    shutil.copy2(corresponding_source(ROOT),licenses/"kiwipiepy-0.20.4-source.tar.gz")
    shutil.copytree(ROOT/"ocr-runtime/licenses",licenses/"ocr-runtime",dirs_exist_ok=True)
    shutil.copy2(ROOT/"ocr-runtime/BUILDINFO.json",licenses/"ocr-runtime/BUILDINFO.json")
    for source,name in ((Path(sys.base_prefix)/"LICENSE.txt","PYTHON_LICENSE.txt"),(Path(sys.base_prefix)/"tcl/tcl8.6/license.terms","TCL_LICENSE.txt"),(Path(sys.base_prefix)/"tcl/tk8.6/license.terms","TK_LICENSE.txt")):
        if source.is_file():shutil.copy2(source,licenses/name)
    notices=out/"ThirdPartyNotices.zip"
    with zipfile.ZipFile(notices,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.write(ROOT/"THIRD_PARTY_NOTICES.txt","THIRD_PARTY_NOTICES.txt")
        for file in licenses.rglob("*"):
            if file.is_file():z.write(file,str(Path("licenses")/file.relative_to(licenses)))
    manifest={"product":PRODUCT,"version":VERSION,"filename":exe.name,"size":exe.stat().st_size,"sha256":digest(exe),"download_base":f"https://github.com/{UPDATE_REPOSITORY}/releases/download/v{VERSION}","components":{"spacing_model":{"filename":model.name,"size":model.stat().st_size,"sha256":digest(model)},'ocr_runtime':{'filename':native.name,'size':native.stat().st_size,'sha256':digest(native),'runtime_id':RUNTIME_ID}}}
    info(create=True)
    for path in (exe,model,notices,native):upload(path)
    result={"manifest":manifest,"assets":[{"name":p.name,"size":p.stat().st_size,"sha256":digest(p)} for p in (exe,model,notices,native)]}
    path=ROOT/"build-result.json";path.write_text(json.dumps(result,indent=2),encoding="utf-8")
    subprocess.run(["git","config","user.name","github-actions[bot]"],check=True)
    subprocess.run(["git","config","user.email","41898282+github-actions[bot]@users.noreply.github.com"],check=True)
    subprocess.run(["git","add","build-result.json"],check=True)
    subprocess.run(["git","commit","-m","Record verified Windows build payload hashes"],check=True)
    subprocess.run(["git","pull","--rebase","origin","main"],check=True)
    subprocess.run(["git","push"],check=True)
    print("Build complete. Awaiting local Ed25519 signature before publication.")


def finalize():
    from updater import verify_manifest
    manifest=json.loads((ROOT/"latest.json").read_text(encoding="utf-8"));verify_manifest(manifest)
    build_result=json.loads((ROOT/"build-result.json").read_text())
    assert {k:v for k,v in manifest.items() if k!="signature"}==build_result["manifest"]
    release=info();assets={a["name"]:a for a in release["assets"]}
    out=ROOT/"public-final";out.mkdir(exist_ok=True)
    for item in build_result["assets"]:
        asset=assets[item["name"]]
        assert asset["digest"]=="sha256:"+item["sha256"] and asset["size"]==item["size"]
        path=out/item["name"]
        with path.open("wb") as target:
            subprocess.run(["gh","api",f"repos/{UPDATE_REPOSITORY}/releases/assets/{asset['id']}","-H","Accept: application/octet-stream"],stdout=target,check=True)
        assert digest(path)==item["sha256"]
    archive=out/f"{PRODUCT}-Setup-{VERSION}.zip"
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.write(out/manifest["filename"],f"{PRODUCT}.exe")
        z.write(ROOT/"latest.json","업데이트배포/latest.json")
        model=manifest["components"]["spacing_model"]["filename"]
        z.write(out/model,"업데이트배포/"+model)
        native=manifest['components']['ocr_runtime']['filename']
        z.write(out/native,'업데이트배포/'+native)
        for name in ("install.cmd","install.ps1","THIRD_PARTY_NOTICES.txt"):z.write(ROOT/name,name)
        with zipfile.ZipFile(out/"ThirdPartyNotices.zip") as notices:
            for item in notices.infolist():
                if item.filename!="THIRD_PARTY_NOTICES.txt":z.writestr(item.filename,notices.read(item))
        z.writestr("설치안내.txt","ZIP 전체 압축 해제 후 기존 프로그램을 종료하고 install.cmd를 실행하세요. 이전 버전 삭제는 필요 없습니다. 설정과 이력은 보존됩니다. 실행할 때 GitHub에서 자동 업데이트합니다. 최초 업무 설정과 원본 한글 양식은 별도로 받은 비공개 설치본 또는 양식 보충 ZIP을 한 번 설치하세요. 공개본에는 개인 설정과 업무 문서가 없습니다.".encode("utf-8-sig"))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    upload(archive);upload(ROOT/"latest.json")
    # Only our disposable, locally reproducible transport files are removed.
    for asset in info()["assets"]:
        if asset["name"].startswith(("part-v2-","transport-setup-")):
            response=requests.delete(asset["url"],headers=headers(),timeout=30);response.raise_for_status()
    expected={p["name"]:p["sha256"] for p in build_result["assets"]}
    expected.update({archive.name:digest(archive),"latest.json":digest(ROOT/"latest.json")})
    actual={a["name"]:a for a in info()["assets"]}
    for name,sha in expected.items():assert actual[name]["state"]=="uploaded" and actual[name]["digest"]=="sha256:"+sha
    response=requests.patch(f"{API}/releases/{release['id']}",headers=headers(),json={"draft":False,"make_latest":"true"},timeout=30)
    response.raise_for_status();assert not response.json()["draft"]
    print("Published verified installer and signed online update.")
    from release_retention import prune
    published=requests.get(f"{API}/releases/{release['id']}",headers=headers(),timeout=30)
    published.raise_for_status()
    print("Release retention:",json.dumps(prune(API,headers(),published.json()),ensure_ascii=True))


if __name__=="__main__":
    {"build":build,"finalize":finalize}[sys.argv[1]]()
