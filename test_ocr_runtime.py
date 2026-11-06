import hashlib
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import ocr_runtime as runtime


class OcrRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.config={'_auth_data_dir':str(self.root)}
        self.files={'tesseract.exe':b'fixture engine','tessdata/kor.traineddata':b'fixture model'}
        hashes={name:hashlib.sha256(data).hexdigest() for name,data in self.files.items()}
        self.identity=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
        self.patch=patch.dict(sys.modules,{'ocr_runtime_info':SimpleNamespace(FILES=hashes,RUNTIME_ID=self.identity)});self.patch.start();self.addCleanup(self.patch.stop)
        runtime.valid.cache_clear();self.addCleanup(runtime.valid.cache_clear)
        self.archive=self.root/'runtime.zip';self.write_zip(self.files)

    def write_zip(self,files):
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)

    def test_install_once_then_reuse_offline_without_unpacking_or_downloading(self):
        folder=runtime.ensure(self.config,archive=self.archive);before={p:p.stat().st_mtime_ns for p in folder.rglob('*') if p.is_file()}
        self.archive.unlink();runtime.valid.cache_clear()
        with patch('updater.stage_component',side_effect=AssertionError('must remain offline')):
            self.assertEqual(runtime.ensure(self.config),folder)
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in before})
        self.assertEqual(len(list((self.root/'ocr-runtime').glob(self.identity+'-*'))),1)

    def test_modified_file_is_not_accepted_after_process_validation_cache_reset(self):
        folder=runtime.ensure(self.config,archive=self.archive);(folder/'tesseract.exe').write_bytes(b'modified')
        runtime.valid.cache_clear();self.assertIsNone(runtime.installed(self.config))
        repaired=runtime.ensure(self.config,archive=self.archive)
        self.assertNotEqual(folder,repaired);self.assertEqual((repaired/'tesseract.exe').read_bytes(),self.files['tesseract.exe'])

    def test_unknown_paths_and_wrong_engine_hash_are_rejected_before_activation(self):
        for files in ({**self.files,'../outside.exe':b'bad'},{**self.files,'tesseract.exe':b'modified'}):
            self.write_zip(files)
            with self.assertRaises(ValueError):runtime.ensure(self.config,archive=self.archive)
            self.assertIsNone(runtime.installed(self.config));self.assertFalse((self.root/'outside.exe').exists())

    def test_pending_or_tampered_pointer_does_not_escape_runtime_storage(self):
        root=self.root/'ocr-runtime';root.mkdir()
        (root/(self.identity+'.json')).write_text(json.dumps({'folder':'../outside'}))
        self.assertIsNone(runtime.installed(self.config))

