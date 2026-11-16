import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import release


class ReleaseTest(unittest.TestCase):
    def test_selected_commit_is_exported_and_incompatible_patch_leaves_no_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            checkout = root / "checkout"
            subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
            git = ["git", "-C", str(checkout)]
            marker = checkout / "README.md"
            marker.write_text("committed", encoding="utf-8")
            subprocess.run([*git, "add", "README.md"], check=True)
            subprocess.run([*git, "-c", "user.name=Test", "-c", "user.email=test@users.noreply.github.com", "-c", "commit.gpgsign=false",
                            "commit", "--quiet", "-m", "test source"], check=True)
            marker.write_text("uncommitted", encoding="utf-8")
            export = root / "source"
            commit = release.export_source(checkout, export)
            self.assertNotEqual(commit, release.TESTED_UPSTREAM)
            self.assertEqual(commit, subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip())
            self.assertEqual((export / "README.md").read_text(encoding="utf-8"), "committed")

            runtime = root / "runtime"
            with self.assertRaisesRegex(ValueError, rf"incompatible with src/core/verify.cpp.*{commit[:12]}"):
                release.prepare(SimpleNamespace(runtime=runtime, strata=checkout, pack=root))
            self.assertFalse(runtime.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "uncommitted")

    def test_source_archive_rejects_link_entries(self):
        entry = tarfile.TarInfo("source.txt")
        self.assertEqual(release.checked_source_members([entry]), [entry])
        for entry_type in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(entry_type=entry_type), self.assertRaises(tarfile.FilterError):
                entry.type = entry_type
                release.checked_source_members([entry])

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_runtime_metadata_write_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"
            release.write_json(path, {"value": 1})
            release.write_json(path, {"value": 2})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(release.read_json(path), {"value": 2})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_profile_rejects_different_pack_and_incompatible_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            pack = root / "models/pack"
            engine = root / "runtime/build/strata"
            config = {"exe": "existing", "cwd": "models", "args": ["--pack", "pack"]}
            self.assertEqual(release.profile_with_engine(config, pack, engine, root)["exe"], str(engine))
            self.assertEqual(config["exe"], "existing")
            for unsupported in (["--pack", "other"], ["--pack", "pack", "--mtp-prob"],
                                ["--pack", "pack", "--batch=1"], ["--pack", "pack", "--eos-ids=2"]):
                with self.subTest(args=unsupported), self.assertRaises(ValueError):
                    release.profile_with_engine({**config, "args": unsupported}, pack, engine, root)

    def test_start_uses_isolated_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runtime, pack, original = root / "runtime", root / "pack", root / "existing.json"
            runtime.mkdir()
            table = runtime / "utf8-policy.bin"
            table.write_bytes(b"test-policy")
            release.write_json(runtime / "runtime.json", {"upstream": "1" * 40, "pack": str(pack),
                               "tokenizer": {"table_sha256": release.digest(table)}})
            release.write_json(runtime / "launch.json", {"working_dir": str(root), "profile": str(original),
                               "port": 18095})
            baseline = b'{"exe":"untouched","args":["--pack","pack"]}'
            original.write_bytes(baseline)

            def launch(command, *, cwd, env):
                self.assertEqual(cwd, root)
                self.assertEqual(command[-5:], ["--host", "127.0.0.1", "--port", "18095", "--offline"])
                self.assertEqual(env[release.TABLE_ENV], str(table))
                self.assertEqual(env[release.STATE_ENV], str(runtime / "policy-state"))
                profile = Path(command[command.index("--config") + 1])
                effective = json.loads(profile.read_bytes())
                self.assertEqual(effective["exe"], str(runtime / "build/strata"))
                self.assertEqual(effective["tokenizer"], str(pack / "tokenizer"))
                self.assertEqual(effective["args"], ["--pack", "pack"])
                self.temp_profile = profile
                return 0

            with patch.object(release, "TABLE_SHA", release.digest(table)), patch.object(release.subprocess, "call", launch):
                self.assertEqual(release.serve(SimpleNamespace(runtime=runtime, offline=True)), 0)
            self.assertFalse(self.temp_profile.exists())
            self.assertEqual(original.read_bytes(), baseline)
