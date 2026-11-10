"""Build-time only: assemble native OCR dependencies and Korean/English models."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import urllib.request
import pefile

base = Path(__file__).resolve().parent
env = base / "build-tools" / "ocr-env"
library = env / "Library" / "bin"
out = base / "ocr-runtime"
out.mkdir(exist_ok=True)
if not (library / "tesseract.exe").is_file():
    raise SystemExit("Create build-tools/ocr-env with conda-forge tesseract=5.5.1 and libarchive=3.8.11=lgpl* first")
from native_dependencies import collect
selected = collect(library)
for name, source in selected.items():
    shutil.copy2(source, out / source.name)
models = out / "tessdata"
models.mkdir(exist_ok=True)
shutil.copy2(env / "share" / "tessdata" / "osd.traineddata", models / "osd.traineddata")

def download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "SeoulDocumentRegister-build"})
    with urllib.request.urlopen(request, timeout=60) as source, path.open("wb") as dest:
        shutil.copyfileobj(source, dest)

info = out / "BUILDINFO.json"
record = json.loads(info.read_text()) if info.exists() else {}
if not record.get("models_commit"):
    headers={"User-Agent":"SeoulDocumentRegister-build"}
    if os.environ.get('GH_TOKEN'):headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
    request = urllib.request.Request("https://api.github.com/repos/tesseract-ocr/tessdata_best/commits/main", headers=headers)
    record["models_commit"] = json.load(urllib.request.urlopen(request, timeout=30))["sha"]
for language in ("kor", "eng"):
    target = models / f"{language}.traineddata"
    if not target.exists():
        download(f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{record['models_commit']}/{target.name}", target)
licenses = out / "licenses"
licenses.mkdir(exist_ok=True)
packages = []
for meta in (env / "conda-meta").glob("*.json"):
    d = json.loads(meta.read_text())
    names = {Path(f).name.lower() for f in d.get("files", [])}
    if not names.intersection(selected) and d["name"] != "tesseract":
        continue
    dest = licenses / d["name"]
    dest.mkdir(exist_ok=True)
    cache = Path(d["link"]["source"])
    for sub in ("licenses", "recipe"):
        if (cache / "info" / sub).is_dir():
            shutil.copytree(cache / "info" / sub, dest / sub, dirs_exist_ok=True)
    packages.append({k:d.get(k) for k in ("name", "version", "build", "license", "url", "sha256")})
    if d["name"] == "libiconv":
        source = dest / f"libiconv-{d['version']}.tar.gz"
        if not source.exists():
            download(f"https://ftp.gnu.org/pub/gnu/libiconv/libiconv-{d['version']}.tar.gz", source)
        recipe = (cache / "info" / "recipe" / "meta.yaml").read_text(encoding="utf-8")
        expected = re.search(r"sha256:\s*['\"]?([a-f0-9]{64})", recipe)
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected and actual != expected.group(1):
            raise ValueError("libiconv corresponding source hash mismatch")
download("https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/LICENSE", licenses / "TESSDATA_LICENSE.txt")
record.update(packages=packages, files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in out.iterdir() if p.is_file() and p.name != "BUILDINFO.json"},
              models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in models.glob("*.traineddata")})
info.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(f"OCR runtime prepared: {len(selected)} binaries; models: {', '.join(record['models'])}")
