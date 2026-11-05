import base64
import json
from pathlib import Path
import queue
import tempfile
import time
import os
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import updater


class UpdateTests(unittest.TestCase):
    def test_restart_environment_is_independent_of_the_exiting_helper(self):
        with patch.dict(os.environ,{'_PYI_APPLICATION_HOME_DIR':'old-runtime','_PYI_PARENT_PROCESS_LEVEL':'1','_MEIPASS2':'old','TCL_LIBRARY':'old-tcl','TK_LIBRARY':'old-tk','KEEP_FIXTURE':'yes'}):
            env=updater.fresh_environment()
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1');self.assertEqual(env['KEEP_FIXTURE'],'yes')
        self.assertFalse(any(name.startswith('_PYI_') for name in env))
        self.assertNotIn('TCL_LIBRARY',env);self.assertNotIn('TK_LIBRARY',env);self.assertNotIn('_MEIPASS2',env)

    def test_restart_retries_early_exit_and_requires_gui_ready_response(self):
        from types import SimpleNamespace
        calls=[]
        def launch(args,**kwargs):
            calls.append(args)
            if len(calls)==2:Path(args[2]).write_text(json.dumps({'status':'ready','version':'1.2.0'}),encoding='utf-8')
            return SimpleNamespace(poll=lambda:1 if len(calls)==1 else None)
        with patch('updater.subprocess.Popen',side_effect=launch),patch('updater.time.sleep'):
            result=updater.restart_target(self.root/'SeoulDocumentRegister.exe',self.root,'1.2.0')
        self.assertEqual(result['attempts'],2);self.assertEqual(result['status'],'ready')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.feed, self.data = self.root / "배포", self.root / "개인 설정"
        self.feed.mkdir()
        self.data.mkdir()
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.manifest = self.publish()

    def signed(self, manifest):
        manifest = {k: v for k, v in manifest.items() if k != "signature"}
        return {**manifest, "signature": base64.b64encode(self.key.sign(updater.canonical(manifest))).decode()}

    def publish(self, version="1.2.0"):
        exe = self.feed / f"SeoulDocumentRegister-{version}.exe"
        exe.write_bytes(b"MZ" + b"new version" * 200)
        manifest = self.signed({"product": "SeoulDocumentRegister", "version": version, "filename": exe.name,
                                "size": exe.stat().st_size, "sha256": updater.file_hash(exe)})
        (self.feed / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def stage(self):
        return updater.check_and_stage(self.feed, self.data, current="1.1.0", public_key=self.public)

    def plan(self):
        staged = self.stage()
        installed = self.root / "설치 폴더"
        installed.mkdir()
        target = installed / "SeoulDocumentRegister.exe"
        target.write_bytes(b"old exe")
        config = self.data / "config.json"
        config.write_text('{"recipient":"담당자"}', encoding="utf-8")
        path = self.data / "updates" / "plan-test.json"
        path.write_text(json.dumps({"parent_pid": 123, "target": str(target), **staged}), encoding="utf-8")
        return path, target, config

    def test_numeric_versions(self):
        self.assertGreater(updater.version_tuple("1.10.0"), updater.version_tuple("1.9.0"))
        for invalid in ("1.2", "v1.2.0", "../1.2.0", None):
            with self.assertRaises(ValueError): updater.version_tuple(invalid)

    def test_missing_feed_and_no_downgrade(self):
        self.assertIsNone(updater.check_and_stage(self.root / "missing", self.data, public_key=self.public))
        self.assertIsNone(updater.check_and_stage(self.feed, self.data, current="1.2.0", public_key=self.public))
        self.assertIsNone(updater.check_and_stage(self.feed, self.data, current="2.0.0", public_key=self.public))

    def test_stage_signed_valid_exe_and_reuse_cache(self):
        update = self.stage()
        self.assertEqual(updater.file_hash(update["staged"]), self.manifest["sha256"])
        (self.feed / self.manifest["filename"]).unlink()
        self.assertEqual(self.stage(), update)

    def test_reject_unsigned_wrong_key_and_tampering(self):
        invalid = [dict(self.manifest, version="9.0.0"), {k:v for k,v in self.manifest.items() if k != "signature"},
                   dict(self.manifest, signature="oops")]
        for manifest in invalid:
            with self.assertRaises(ValueError): updater.verify_manifest(manifest, self.public)
        with self.assertRaises(ValueError): updater.verify_manifest(self.manifest)

    def test_reject_signed_path_traversal_or_wrong_product(self):
        for changes in ({"filename": "../other.exe"}, {"product": "Other"}, {"size": 99999999999}, {"sha256": "oops"}):
            with self.assertRaises(ValueError): updater.verify_manifest(self.signed({**self.manifest, **changes}), self.public)

    def test_reject_corrupted_or_incomplete_exe(self):
        exe = self.feed / self.manifest["filename"]
        original = exe.read_bytes()
        exe.write_bytes(b"MZ" + b"X" * (len(original) - 2))
        with self.assertRaises(ValueError): self.stage()
        self.assertEqual(list((self.data / "updates").glob("*.part")), [])
        exe.write_bytes(b"MZ")
        with self.assertRaisesRegex(ValueError, "배포 중"): self.stage()

    def test_reject_non_windows_exe_even_with_signed_hash(self):
        exe = self.feed / self.manifest["filename"]
        exe.write_bytes(b"NOT WINDOWS" * 200)
        manifest = self.signed({**self.manifest, "size": exe.stat().st_size, "sha256": updater.file_hash(exe)})
        (self.feed / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Windows"): self.stage()

    def test_apply_waits_preserves_config_backup_and_replaces(self):
        plan, target, config = self.plan()
        seen = []
        result = updater.apply_plan(plan, public_key=self.public, wait=lambda p: seen.append(p), launch=False)
        self.assertEqual(seen, [123])
        self.assertEqual(result["status"], "updated")
        self.assertEqual(updater.file_hash(target), self.manifest["sha256"])
        self.assertEqual(target.with_name(target.name + ".previous").read_bytes(), b"old exe")
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["recipient"], "담당자")
        self.assertFalse(plan.exists())

    def test_corrupt_cache_stops_before_shutdown(self):
        plan, target, _ = self.plan()
        info = json.loads(plan.read_text())
        Path(info["staged"]).write_bytes(b"broken")
        with self.assertRaises(ValueError):
            updater.apply_plan(plan, public_key=self.public, wait=lambda p: self.fail("must not wait"), launch=False)
        self.assertEqual(target.read_bytes(), b"old exe")

    def test_failed_new_exe_healthcheck_rolls_back(self):
        plan, target, _ = self.plan()
        with patch("updater.subprocess.run", side_effect=RuntimeError("new version startup failed")), patch("updater.subprocess.Popen") as launch:
            with self.assertRaises(RuntimeError):
                updater.apply_plan(plan, public_key=self.public, wait=lambda p: None, launch=True)
            self.assertEqual(target.read_bytes(), b"old exe")
            self.assertEqual(launch.call_count, 1)

    def test_wrong_target_or_cache_location_rejected(self):
        plan, target, _ = self.plan()
        info = json.loads(plan.read_text())
        info["target"] = str(target.with_name("unrelated.exe"))
        plan.write_text(json.dumps(info))
        with self.assertRaises(ValueError): updater.apply_plan(plan, public_key=self.public, launch=False)
        self.assertEqual(target.read_bytes(), b"old exe")

    def test_watch_baseline_survives_update_gap(self):
        from app import Worker, defaults
        downloads = self.root / "Downloads"
        downloads.mkdir()
        old = downloads / "old.pdf"
        old.write_bytes(b"old")
        config = {**defaults(), "downloads": str(downloads)}
        commands, events = queue.Queue(), queue.Queue()
        worker = Worker(commands, events, config, self.data / "history.sqlite")
        worker.start()
        commands.put(("watch", True))
        while True:
            if events.get(timeout=5)[0] == "idle": break
        commands.put(("prepare_update", {"test": True}))
        while True:
            kind, data = events.get(timeout=5)
            if kind == "update_quiesced": break
        commands.put(("quit", None))
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        new = downloads / "arrived_during_update.pdf"
        new.write_bytes(b"new")
        events = queue.Queue()
        commands = queue.Queue()
        resumed = Worker(commands, events, config, self.data / "history.sqlite")
        resumed.start()
        commands.put(("watch", True))
        while True:
            if events.get(timeout=5)[0] == "idle": break
        commands.put(("quit", None))
        resumed.join(timeout=5)
        self.assertIn(str(old), resumed.seen)
        self.assertNotIn(str(new), resumed.seen)
        self.assertFalse((self.data / "watch-resume.json").exists())

    def test_idle_install_waits_for_unsaved_edits_or_active_work(self):
        from app import App
        class Root:
            def __init__(self): self.waits = []
            def after(self, *args): self.waits.append(args)
            def winfo_children(self): return []
        class Field:
            def get(self): return "edited title"
        class Flag:
            def is_set(self): return False
        class Worker:
            working = Flag()
        app = App.__new__(App)
        app.update_ready, app.updating = {"new": True}, False
        app.config = {"auto_update": True}
        app.root, app.busy, app.commands = Root(), False, queue.Queue()
        app.worker = Worker()
        app.last_activity = time.monotonic() - 120
        app.fields = {"title": Field()}
        app.selected = lambda: [{"title": "original"}]
        app.apply_when_idle()
        self.assertFalse(app.updating)
        self.assertTrue(app.commands.empty())
        self.assertTrue(app.root.waits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
