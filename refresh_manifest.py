"""Republish a signed manifest asset without rebuilding an already public release."""
import hashlib,json,os
from pathlib import Path
import requests
from updater import verify_manifest
from release_info import UPDATE_REPOSITORY,VERSION

def main():
    payload=Path('latest.json').read_bytes();manifest=verify_manifest(json.loads(payload))
    assert manifest['version']==VERSION
    api='https://api.github.com/repos/'+UPDATE_REPOSITORY
    headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'}
    response=requests.get(api+'/releases/tags/v'+VERSION,headers=headers,timeout=30);response.raise_for_status();release=response.json()
    assert not release['draft'] and release['tag_name']=='v'+VERSION
    executable=next(a for a in release['assets'] if a['name']==manifest['filename'])
    assert executable['state']=='uploaded' and executable['size']==manifest['size'] and executable['digest']=='sha256:'+manifest['sha256']
    for asset in release['assets']:
        if asset['name']=='latest.json':
            response=requests.delete(asset['url'],headers=headers,timeout=30);response.raise_for_status()
    response=requests.post('https://uploads.github.com/repos/'+UPDATE_REPOSITORY+'/releases/'+str(release['id'])+'/assets',params={'name':'latest.json'},headers={**headers,'Content-Type':'application/json'},data=payload,timeout=60)
    response.raise_for_status();asset=response.json()
    assert asset['state']=='uploaded' and asset['digest']=='sha256:'+hashlib.sha256(payload).hexdigest()
    print('Signed manifest refreshed for public v'+VERSION+'; executable and installer unchanged.')

if __name__=='__main__':main()
