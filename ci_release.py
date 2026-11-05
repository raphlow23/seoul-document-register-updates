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
        response=requests.post(f"{API}/releases",headers=headers(),json={"tag_name":"v"+VERSION,"target_commitish":"main","name":"v"+VERSION+" — 업데이트 후 자동 실행·문서와 도면 첨부","draft":True,"prerelease":False,"body":"업데이트 후 새 프로그램 창이 준비된 것을 확인하고 완료합니다. 시작 직후 종료되면 최대 3회 재시도하며, 업데이트 도우미의 임시 실행환경을 새 프로그램에 물려주지 않습니다. 다운로드·추가 목록에는 PDF·HWP/HWPX·엑셀·HTML·워드·프레젠테이션·텍스트·ZIP·DWG·DXF를 표시하며 이미지와 기타 파일은 제외합니다. ZIP 안에서도 같은 기준으로 문서·도면을 모읍니다. 첨부 목록은 파일 수정 날짜가 최신인 순서로 정렬하고 날짜를 표시합니다. 건축허가·사업계획승인은 시청공문/협의도면/시청회신, 사용승인·사용검사는 시청공문/협의도면/현장사진/시청회신을 선택해 만들 수 있습니다. 위치별 선택을 따로 기억하고 선택된 폴더만 연속 번호로 만듭니다. 수신 접수 완료 후 저장된 공문 폴더를 자동으로 열어 인쇄와 수기 접수번호 기재를 이어갈 수 있습니다. 원본 ZIP과 기존 처리 이력·설정·한글 양식은 보존합니다. 정상 게시 후 최신·직전 릴리스만 보관합니다."},timeout=30)
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
    seed=out/"empty-seed.json";seed.write_text('{"root":"","spreadsheet_id":""}')
    print("Public executable check:",verify(exe,seed));seed.unlink()
    subprocess.run([str(exe),"--self-test"],check=True,timeout=90)
    subprocess.run([str(exe),"--test-workflow"],check=True,timeout=90)
    subprocess.run([sys.executable,"-m","unittest","-q","test_browser_startup","test_queue_visibility","test_browser_tabs","test_template_library","test_outgoing_folders","test_release_retention","test_browser_ranges","test_file_drop","test_workflow_navigation","test_template_defaults","test_hwp_anchor_slots","test_file_rollback","test_receipt_attachments","test_zip_intake","test_browser_sheets","test_attachment_files","test_ocr_review","test_folder_name_picker","test_updater"],check=True,timeout=90)
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
    manifest={"product":PRODUCT,"version":VERSION,"filename":exe.name,"size":exe.stat().st_size,"sha256":digest(exe),"download_base":f"https://github.com/{UPDATE_REPOSITORY}/releases/download/v{VERSION}","components":{"spacing_model":{"filename":model.name,"size":model.stat().st_size,"sha256":digest(model)}}}
    info(create=True)
    for path in (exe,model,notices):upload(path)
    result={"manifest":manifest,"assets":[{"name":p.name,"size":p.stat().st_size,"sha256":digest(p)} for p in (exe,model,notices)]}
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
