import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import updater
from local_setup import prepare
from release_info import UPDATE_BASE_URL


class Response:
    def __init__(self,url,body=b"",status=200,headers=None):
        self.url,self.body,self.status_code=url,body,status
        self.headers=headers or {};self.history=[];self.closed=False
    def iter_content(self,chunk_size):yield self.body
    def close(self):self.closed=True


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.key=Ed25519PrivateKey.generate()
        self.public=base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        self.bytes=b"MZ"+b"verified program"*200
        self.source="https://github.com/example/updates/releases/latest/download"
        self.manifest={"product":updater.PRODUCT,"version":"9.0.0","filename":updater.PRODUCT+"-9.0.0.exe","size":len(self.bytes),"sha256":__import__('hashlib').sha256(self.bytes).hexdigest(),"download_base":"https://github.com/example/updates/releases/download/v9.0.0"}
        self.manifest["signature"]=base64.b64encode(self.key.sign(updater.canonical(self.manifest))).decode()

    def test_online_download_signature_hash_and_cache_reuse(self):
        def get(url,**kwargs):
            self.assertFalse(kwargs["allow_redirects"])
            body=json.dumps(self.manifest).encode() if url.endswith("latest.json") else self.bytes
            return Response(url,body)
        with patch("requests.get",side_effect=get):
            update=updater.check_and_stage(self.source,self.root,current="1.3.0",public_key=self.public)
            self.assertEqual(Path(update["staged"]).read_bytes(),self.bytes)
            again=updater.check_and_stage(self.source,self.root,current="1.3.0",public_key=self.public)
            self.assertEqual(again,update)

    def test_redirect_to_https_github_assets_only(self):
        with patch("requests.get",side_effect=[Response(self.source,status=302,headers={"Location":"https://release-assets.githubusercontent.com/asset"}),Response("https://release-assets.githubusercontent.com/asset",b"payload")]) as get:
            self.assertEqual(updater.read_online(self.source,100),b"payload")
            self.assertEqual(get.call_count,2)
        for bad in ("http://github.com/asset","https://example.com/asset","https://github.com.evil.example/asset"):
            with patch("requests.get",return_value=Response(self.source,status=302,headers={"Location":bad})) as get:
                with self.assertRaises(ValueError):updater.read_online(self.source,100)
                self.assertEqual(get.call_count,1)

    def test_bad_hash_does_not_stage_or_run_download(self):
        with patch("requests.get",side_effect=lambda url,**k:Response(url,json.dumps(self.manifest).encode() if url.endswith("latest.json") else b"MZcorrupt")):
            with self.assertRaises(ValueError):updater.check_and_stage(self.source,self.root,current="1.3.0",public_key=self.public)
        self.assertFalse(list(self.root.rglob("*.exe")))
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_missing_release_is_explicit_error_and_bounded_response(self):
        with patch("requests.get",return_value=Response(self.source,status=404)):
            with self.assertRaisesRegex(ValueError,"404"):updater.read_online(self.source,10)
        with patch("requests.get",return_value=Response(self.source,b"a"*20)):
            with self.assertRaises(ValueError):updater.read_online(self.source,10)

    def official(self):return 'https://github.com/'+updater.UPDATE_REPOSITORY+'/releases/latest/download'

    def test_release_504_uses_signed_repository_manifest_and_downloads_verified_exe(self):
        source=self.official();seen=[]
        def get(url,**kwargs):
            seen.append(url)
            if url==source+'/latest.json':return Response(url,status=504)
            return Response(url,json.dumps(self.manifest).encode() if url.endswith('latest.json') else self.bytes)
        with patch('requests.get',side_effect=get):
            update=updater.check_and_stage(source,self.root,current='1.3.0',public_key=self.public)
        self.assertEqual(Path(update['staged']).read_bytes(),self.bytes)
        self.assertEqual(seen[1],'https://raw.githubusercontent.com/'+updater.UPDATE_REPOSITORY+'/main/latest.json')
        self.assertEqual(seen[2],self.manifest['download_base']+'/'+self.manifest['filename'])

    def test_connection_timeout_also_uses_repository_manifest(self):
        import requests
        with patch('requests.get',side_effect=[requests.Timeout('edge timeout'),Response(self.official(),json.dumps(self.manifest).encode())]) as get:
            self.assertEqual(updater.read_manifest(self.official()+'/latest.json',self.public),self.manifest)
        self.assertEqual(get.call_count,2)

    def test_binary_http_504_retries_download_route_with_verified_hash(self):
        seen=[]
        def get(url,**kwargs):
            seen.append(url)
            if url.endswith('latest.json'):return Response(url,json.dumps(self.manifest).encode())
            return Response(url,self.bytes) if url.endswith('?download=1') else Response(url,status=504)
        with patch('requests.get',side_effect=get):
            update=updater.check_and_stage(self.source,self.root,current='1.3.0',public_key=self.public)
        self.assertEqual(Path(update['staged']).read_bytes(),self.bytes)
        self.assertEqual(seen[-1],seen[-2]+'?download=1')

    def test_interrupted_asset_retry_discards_partial_bytes_and_rejects_corruption(self):
        import requests
        class Interrupted(Response):
            def iter_content(self,chunk_size):
                yield b'partial bytes'
                raise requests.ConnectionError('connection reset')
        def get(url,**kwargs):
            if url.endswith('latest.json'):return Response(url,json.dumps(self.manifest).encode())
            return Response(url,b'MZcorrupt') if url.endswith('?download=1') else Interrupted(url)
        with patch('requests.get',side_effect=get):
            with self.assertRaises(ValueError):updater.check_and_stage(self.source,self.root,current='1.3.0',public_key=self.public)
        self.assertFalse(list(self.root.rglob('*.exe')));self.assertFalse(list(self.root.rglob('*.part')))

    def test_custom_feed_does_not_silently_switch_to_product_repository(self):
        with patch('requests.get',return_value=Response(self.source,status=504)) as get:
            with self.assertRaisesRegex(ValueError,'504'):updater.read_manifest(self.source,self.public)
        self.assertEqual(get.call_count,1)

    def test_invalid_signature_is_rejected_without_mirror_retry(self):
        bad={**self.manifest,'version':'10.0.0'}
        with patch('requests.get',return_value=Response(self.official(),json.dumps(bad).encode())) as get:
            with self.assertRaisesRegex(ValueError,'서명'):updater.read_manifest(self.official(),self.public)
        self.assertEqual(get.call_count,1)

    def test_fallback_signature_is_checked_before_any_exe_download(self):
        bad={**self.manifest,'signature':'invalid'}
        with patch('requests.get',side_effect=[Response(self.official(),status=504),Response(self.official(),json.dumps(bad).encode())]) as get:
            with self.assertRaisesRegex(ValueError,'서명'):updater.check_and_stage(self.official(),self.root,current='1.3.0',public_key=self.public)
        self.assertEqual(get.call_count,2);self.assertFalse(list(self.root.rglob('*.exe')))

    def test_oversized_or_unsafe_response_is_not_hidden_by_fallback(self):
        for response in (Response(self.official(),b'x'*32769),Response(self.official(),status=302,headers={'Location':'https://example.com/latest.json'})):
            with patch('requests.get',return_value=response) as get:
                with self.assertRaises(ValueError):updater.read_manifest(self.official(),self.public)
            self.assertEqual(get.call_count,1)

    def test_private_install_preserves_history_credentials_and_existing_templates(self):
        package=self.root/"package";data=self.root/"data"
        package.mkdir();data.mkdir();(package/"local-templates").mkdir();(data/"hwp-templates").mkdir()
        (package/"initial-config.json").write_text(json.dumps({"root":"private-root","spreadsheet_id":"private-sheet"}))
        for name in ("catalog.json",*[f"template{i}.hwp" for i in range(1,7)]):(package/"local-templates"/name).write_bytes(b"original template")
        (data/"hwp-templates/template1.hwp").write_bytes(b"user template")
        (data/"config.json").write_text(json.dumps({"root":"user-root","recipient":"user","oauth_client_path":"saved-client","auto_update":False}))
        (data/"history.sqlite").write_bytes(b"history")
        config=prepare(package,data)
        self.assertEqual(config["root"],"user-root");self.assertEqual(config["spreadsheet_id"],"private-sheet")
        self.assertEqual(config["oauth_client_path"],"saved-client");self.assertTrue(config["auto_update"])
        self.assertEqual(config["update_url"],UPDATE_BASE_URL)
        self.assertEqual((data/"history.sqlite").read_bytes(),b"history")
        self.assertEqual((data/"hwp-templates/template1.hwp").read_bytes(),b"user template")


if __name__=="__main__":unittest.main()
