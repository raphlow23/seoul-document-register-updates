"""Force a release-route 504 and verify the real repository's signed manifest."""
import json
from pathlib import Path
import requests
from release_info import UPDATE_BASE_URL,UPDATE_REPOSITORY
from updater import read_manifest

def run(output):
    original=requests.get;seen=[]
    class Unavailable:
        status_code=504;history=[];headers={};url=UPDATE_BASE_URL+'/latest.json'
        def close(self):pass
    def get(url,**kwargs):
        seen.append(url)
        if url==UPDATE_BASE_URL+'/latest.json':return Unavailable()
        return original(url,**kwargs)
    try:
        requests.get=get
        manifest=read_manifest(UPDATE_BASE_URL)
    finally:requests.get=original
    expected='https://raw.githubusercontent.com/'+UPDATE_REPOSITORY+'/main/latest.json'
    assert seen==[UPDATE_BASE_URL+'/latest.json',expected]
    result={'forced_http_504':True,'signed_repository_fallback_verified':True,'manifest_version':manifest['version'],'real_sheet_writes':False,'real_shared_folder_writes':False}
    Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8');return result
