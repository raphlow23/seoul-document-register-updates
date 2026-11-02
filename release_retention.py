"""Remove obsolete release binaries only after two healthy published versions exist."""
import re
import requests


def version(tag):
    match=re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)",tag or "")
    return tuple(map(int,match.groups())) if match else None


def healthy(release):
    tag=release.get("tag_name","");number=tag.removeprefix("v")
    required={"latest.json",f"SeoulDocumentRegister-{number}.exe",f"SeoulDocumentRegister-Setup-{number}.zip"}
    uploaded={a["name"] for a in release.get("assets",[]) if a.get("state")=="uploaded" and a.get("size",0)>0}
    return not release.get("draft") and not release.get("prerelease") and required<=uploaded


def plan(releases,current):
    if not healthy(current) or version(current.get("tag_name")) is None:raise ValueError("Current release is not verified and published; retention is disabled.")
    this=version(current["tag_name"])
    candidates=[r for r in releases if version(r.get("tag_name")) is not None and version(r["tag_name"])<this and healthy(r)]
    if not candidates:return [current],[]
    previous=max(candidates,key=lambda r:version(r["tag_name"]))
    floor=version(previous["tag_name"])
    remove=[r for r in releases if version(r.get("tag_name")) is not None and version(r["tag_name"])<floor and r["id"] not in (current["id"],previous["id"])]
    return [current,previous],remove


def prune(api,headers,current):
    releases=[]
    for page in range(1,101):
        response=requests.get(api+"/releases",headers=headers,params={"per_page":100,"page":page},timeout=30)
        response.raise_for_status();items=response.json();releases.extend(items)
        if len(items)<100:break
    else:raise ValueError("Release inventory exceeded the retention limit; nothing was deleted.")
    keep,remove=plan(releases,current)
    result={"kept":[r["tag_name"] for r in keep],"deleted":[],"asset_bytes_removed":0}
    for release in sorted(remove,key=lambda r:version(r["tag_name"])):
        # Recheck identity before destructive requests; no branch, tag or source commit is removed.
        response=requests.get(api+f"/releases/{release['id']}",headers=headers,timeout=30)
        response.raise_for_status();actual=response.json()
        if actual["id"]!=release["id"] or actual["id"] in {r["id"] for r in keep} or actual["tag_name"]!=release["tag_name"] or version(actual["tag_name"])>=version(keep[-1]["tag_name"]):raise ValueError("Release identity changed; cleanup stopped.")
        for asset in actual.get("assets",[]):
            response=requests.delete(api+f"/releases/assets/{asset['id']}",headers=headers,timeout=30)
            response.raise_for_status();result["asset_bytes_removed"]+=asset.get("size",0)
        response=requests.delete(api+f"/releases/{actual['id']}",headers=headers,timeout=30)
        response.raise_for_status();result["deleted"].append(actual["tag_name"])
    return result
