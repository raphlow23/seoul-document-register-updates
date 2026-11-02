"""GitHub runner: reassemble only the expected public release payloads."""
import hashlib
import json
from pathlib import Path
import subprocess

REPO = "raphlow23/seoul-document-register-updates"
RELEASE_ID = 431139315


def api(path, *args):
    return json.loads(subprocess.check_output(["gh", "api", path, *args]))


def main():
    index = json.loads(Path("public-upload-index.json").read_text())
    release = api(f"repos/{REPO}/releases/{RELEASE_ID}")
    assert release["draft"] and release["tag_name"] == "v2.0.0"
    assets = {a["name"]: a for a in api(f"repos/{REPO}/releases/{RELEASE_ID}/assets?per_page=100")}
    output = Path("assembled"); output.mkdir(exist_ok=True)
    for item in index:
        target = output / item["name"]
        with target.open("wb") as destination:
            for part in item["parts"]:
                asset = assets[part["name"]]
                assert asset["state"] == "uploaded" and asset["size"] == part["size"]
                temporary = output / "part.tmp"
                with temporary.open("wb") as stream:
                    subprocess.run(["gh", "api", f"repos/{REPO}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream"], stdout=stream, check=True)
                data = temporary.read_bytes()
                assert len(data) == part["size"] and hashlib.sha256(data).hexdigest() == part["sha256"]
                destination.write(data)
                temporary.unlink()
        assert target.stat().st_size == item["size"]
        assert hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == item["sha256"]
    for item in index:
        subprocess.run(["gh", "release", "upload", "v2.0.0", str(output/item["name"]), "--repo", REPO, "--clobber"], check=True)
    final_assets = {a["name"]: a for a in api(f"repos/{REPO}/releases/{RELEASE_ID}/assets?per_page=100")}
    for item in index:
        asset = final_assets[item["name"]]
        assert asset["state"] == "uploaded" and asset["size"] == item["size"]
        assert asset.get("digest") == "sha256:" + item["sha256"]
    # Remove only the reconstructible transport pieces generated for this build.
    for item in index:
        for part in item["parts"]:
            subprocess.run(["gh", "api", "--method", "DELETE", f"repos/{REPO}/releases/assets/{assets[part['name']]['id']}"], check=True)
    api(f"repos/{REPO}/releases/{RELEASE_ID}", "--method", "PATCH", "-F", "draft=false", "-f", "make_latest=true")
    print("Published v2.0.0 after checking every public asset SHA-256.")


if __name__ == "__main__":
    main()
