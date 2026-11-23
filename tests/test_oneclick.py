import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from local import oneclick


def result(code, message=""):
    return subprocess.CompletedProcess(["git"], code, stderr=message)


class LauncherNetworkTests(unittest.TestCase):
    def test_recovers_completed_temp_file_and_preserves_other_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "legacy"
            legacy.mkdir()
            pending = root / "pending.json"
            original = {"day": "2026-11-23", "id": "1" * 32, "items": [], "schools": {}}
            (legacy / "pending.tmp").write_text(json.dumps(original))
            pending.write_text("existing")
            with patch.object(oneclick, "LEGACY_BASE", legacy):
                oneclick.recover_pending(pending)
                self.assertEqual(pending.read_text(), "existing")
                self.assertTrue((legacy / "pending.tmp").exists())
                pending.unlink()
                pending.with_suffix(".tmp").write_text('{"unfinished":')
                oneclick.recover_pending(pending)
                self.assertEqual(json.loads(pending.read_bytes()), original)
                self.assertFalse((legacy / "pending.tmp").exists())

    def test_windows_proxy_and_fallback_after_timeout(self):
        proxy = "http://127.0.0.1:8888"
        with patch.object(oneclick, "windows_proxy", return_value=proxy), patch("time.sleep"), \
                patch("subprocess.run", side_effect=[result(128, "Failed to connect to github.com"), result(0)]) as run:
            self.assertEqual(oneclick.network_git(Path("."), "fetch").returncode, 0)
        self.assertIn("http.https://github.com/.proxy=" + proxy, run.call_args_list[0].args[0])
        self.assertNotIn("http.https://github.com/.proxy=" + proxy, run.call_args_list[1].args[0])
        self.assertIn("http.lowSpeedTime=30", run.call_args_list[0].args[0])

    def test_retries_stop_and_auth_errors_are_reported_immediately(self):
        with patch.object(oneclick, "windows_proxy", return_value=None), patch("time.sleep"), \
                patch("subprocess.run", return_value=result(128, "Connection timed out")) as run:
            with self.assertRaisesRegex(RuntimeError, "3"):
                oneclick.network_git(Path("."), "fetch")
            self.assertEqual(run.call_count, 3)
        with patch.object(oneclick, "windows_proxy", return_value=None), \
                patch("subprocess.run", return_value=result(128, "Authentication failed")) as run:
            self.assertEqual(oneclick.network_git(Path("."), "push").returncode, 128)
            self.assertEqual(run.call_count, 1)

    def test_shallow_checkout_and_two_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "remote.git"
            oneclick.git(root, "init", "-q", "--bare", "-b", "main", str(origin))
            seed = root / "seed"
            oneclick.git(root, "init", "-q", "-b", "main", str(seed))
            for i in range(2):
                (seed / "report").write_text(str(i))
                oneclick.git(seed, "add", "report")
                oneclick.git(seed, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", str(i))
            remote = origin.as_uri()
            oneclick.git(seed, "push", "-q", remote, "main")
            with patch.object(oneclick, "REMOTE", remote), patch.object(oneclick, "PUSH_URL", remote):
                writers = [root / "first", root / "second"]
                for repo in writers:
                    oneclick.checkout_latest(repo)
                    self.assertEqual(oneclick.git(repo, "rev-list", "--count", "HEAD", capture=True).stdout.strip(), "1")
                for repo in writers:
                    (repo / repo.name).write_text(repo.name)
                    oneclick.git(repo, "add", repo.name)
                    oneclick.publish_commit(repo, repo.name)
                self.assertEqual(oneclick.git(root, "--git-dir", str(origin), "ls-tree", "--name-only", "main", capture=True).stdout.splitlines(),
                                 ["first", "report", "second"])
