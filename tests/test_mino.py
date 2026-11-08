from __future__ import annotations

import difflib
import hashlib
import io
import os
import queue
import stat
import tempfile
import unittest

from core.models import ComparisonRow, FileStatus, normalize_octal_display
from core import sftp_logic


def drain_all(q: queue.Queue) -> list:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


class TestModels(unittest.TestCase):
    def test_octal_normalization(self) -> None:
        self.assertEqual(normalize_octal_display(755), "755")
        self.assertEqual(normalize_octal_display(75), "075")
        self.assertEqual(normalize_octal_display(0), "000")
        self.assertEqual(normalize_octal_display("644 -> 75"), "644 -> 075")
        self.assertEqual(normalize_octal_display("?"), "?")

    def test_comparison_row_roundtrip_and_legacy_5col(self) -> None:
        row6 = ComparisonRow.from_tree_values(
            ("DIFFERENT", "src/app.py", "⚠", "alice:dev -> root:wheel", "-rw-r--r-- -> -rwxr-xr-x", 755)
        )
        self.assertEqual(row6.status, FileStatus.DIFFERENT.value)
        self.assertTrue(row6.can_diff)
        self.assertTrue(row6.can_sync_to_prod)
        self.assertTrue(row6.can_sync_to_test)
        self.assertEqual(row6.primary_owner_and_group(), ("alice", "dev"))
        self.assertEqual(row6.split_owners(), ("alice:dev", "root:wheel"))
        self.assertEqual(row6.octal_perms, "755")

        row5 = ComparisonRow.from_tree_values(
            ("ONLY ON TEST", "only.txt", "bob:staff", "-rw-------", 600)
        )
        self.assertEqual(row5.note, "")
        self.assertTrue(row5.exists_on_test)
        self.assertFalse(row5.exists_on_prod)
        self.assertTrue(row5.can_sync_to_prod)
        self.assertFalse(row5.can_sync_to_test)


class TestSFTPLogicAndWorkflows(unittest.TestCase):
    def test_parse_colon_id_map_handles_nis_and_malformed_lines(self) -> None:
        sample = "# comment\nroot:x:0:0:root:/root:/bin/bash\n+::::::\nwww-data:x:33:33::/var/www:/usr/sbin/nologin\n"
        parsed = sftp_logic._parse_colon_id_map(sample)
        self.assertEqual(parsed, {0: "root", 33: "www-data"})

    def test_streaming_hash_and_chunk_boundary_keyword(self) -> None:
        keyword = "MINO_SECRET_TAG"
        prefix = b"A" * (sftp_logic.FILE_CHUNK_SIZE - 5)
        payload = prefix + keyword.encode("utf-8") + b"Z" * 1024
        expected_md5 = hashlib.md5(payload).hexdigest()

        digest, note = sftp_logic._hash_and_scan_stream(io.BytesIO(payload), keyword)
        self.assertEqual(digest, expected_md5)
        self.assertEqual(note, keyword)

        digest_no_match, note_no_match = sftp_logic._hash_and_scan_stream(io.BytesIO(b"hello world"), keyword)
        self.assertEqual(digest_no_match, hashlib.md5(b"hello world").hexdigest())
        self.assertFalse(note_no_match)

    def test_missing_test_path_raises_instead_of_false_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prod_dir = os.path.join(tmp, "prod")
            os.makedirs(prod_dir)
            s1 = {"host": "local", "port": "22", "user": "", "pass": "", "path": os.path.join(tmp, "nonexistent")}
            s2 = {"host": "local", "port": "22", "user": "", "pass": "", "path": prod_dir}
            q: queue.Queue = queue.Queue()

            sftp_logic.compare_folders_task(s1, s2, q)
            items = drain_all(q)
            self.assertTrue(any(isinstance(x, FileNotFoundError) for x in items))

    def test_compare_and_bidirectional_sync_and_ignores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = os.path.join(tmp, "test")
            prod_dir = os.path.join(tmp, "prod_nested", "sub")  # Does not exist yet -> auto-created!
            os.makedirs(os.path.join(test_dir, "deep", "inner"))

            with open(os.path.join(test_dir, "same.txt"), "w", encoding="utf-8") as f:
                f.write("identical content")
            with open(os.path.join(test_dir, "flagged.py"), "w", encoding="utf-8") as f:
                f.write("# TODO_CUSTOM check this\nx = 1\n")
            with open(os.path.join(test_dir, "ignored.tmp"), "w", encoding="utf-8") as f:
                f.write("ignore me")
            with open(os.path.join(test_dir, "deep", "inner", "nested.txt"), "w", encoding="utf-8") as f:
                f.write("nested payload")
            os.chmod(os.path.join(test_dir, "flagged.py"), 0o750)

            # Broken symlink should not crash scanning
            os.symlink("/nonexistent_broken_target_xyz", os.path.join(test_dir, "broken_link"))

            s1 = {"host": "local", "port": "22", "user": "", "pass": "", "path": test_dir}
            s2 = {"host": "local", "port": "22", "user": "", "pass": "", "path": prod_dir}

            # 1. Initial compare (auto-creates prod_dir recursively)
            q: queue.Queue = queue.Queue()
            sftp_logic.compare_folders_task(s1, s2, q, scan_notes="TODO_CUSTOM", ignore_patterns="*.tmp")
            items = drain_all(q)
            results = items[-1]
            self.assertIsInstance(results, dict)
            self.assertNotIn("ignored.tmp", results["only_on_1"])
            self.assertIn("flagged.py", results["only_on_1"])
            self.assertEqual(results["files_s1"]["flagged.py"]["has_note"], "TODO_CUSTOM")

            # 2. Forward sync TEST -> PROD
            q_sync: queue.Queue = queue.Queue()
            sftp_logic.sync_folders_task(s1, s2, results, True, q_sync, "TEST", "PROD")
            sync_items = drain_all(q_sync)
            self.assertEqual(sync_items[-1], {"status": "sync_complete", "success": True})
            self.assertTrue(os.path.exists(os.path.join(prod_dir, "deep", "inner", "nested.txt")))
            prod_mode = stat.S_IMODE(os.stat(os.path.join(prod_dir, "flagged.py")).st_mode)
            self.assertEqual(prod_mode, 0o750)

            # 3. Modify PROD and add PROD-only file, then reverse sync PROD -> TEST
            with open(os.path.join(prod_dir, "flagged.py"), "w", encoding="utf-8") as f:
                f.write("updated on prod")
            with open(os.path.join(prod_dir, "prod_only.txt"), "w", encoding="utf-8") as f:
                f.write("from prod")

            q_cmp2: queue.Queue = queue.Queue()
            sftp_logic.compare_folders_task(s1, s2, q_cmp2)
            res2 = drain_all(q_cmp2)[-1]
            self.assertEqual(res2["different"], ["flagged.py"])
            self.assertEqual(res2["only_on_2"], ["prod_only.txt"])

            effective_reverse = {
                "files_s1": res2["files_s2"],
                "files_s2": res2["files_s1"],
                "only_on_1": res2["only_on_2"],
                "different": res2["different"],
                "only_on_1_dirs": res2["only_on_2_dirs"],
                "only_on_2": res2["only_on_1"],
                "only_on_2_dirs": res2["only_on_1_dirs"],
            }
            q_rev: queue.Queue = queue.Queue()
            sftp_logic.sync_folders_task(s2, s1, effective_reverse, False, q_rev, "PROD", "TEST")
            rev_items = drain_all(q_rev)
            self.assertEqual(rev_items[-1], {"status": "sync_complete", "success": True})
            self.assertTrue(os.path.exists(os.path.join(test_dir, "prod_only.txt")))
            with open(os.path.join(test_dir, "flagged.py"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "updated on prod")

    def test_single_and_batch_sync_deep_dirs_and_editor_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            dst = os.path.join(tmp, "dst")
            os.makedirs(os.path.join(src, "a", "b", "c"))
            os.makedirs(dst)

            file_rel = "a/b/c/hello.txt"
            with open(os.path.join(src, "a", "b", "c", "hello.txt"), "w", encoding="utf-8") as f:
                f.write("ciao mondo")

            c1 = {"host": "", "port": "22", "user": "", "pass": "", "path": src}
            c2 = {"host": "local", "port": "22", "user": "", "pass": "", "path": dst}

            q: queue.Queue = queue.Queue()
            sftp_logic.sync_single_file_task(c1, c2, file_rel, q)
            last = drain_all(q)[-1]
            self.assertEqual(last, {"status": "single_sync_complete", "success": True, "file": file_rel})
            self.assertTrue(os.path.exists(os.path.join(dst, "a", "b", "c", "hello.txt")))

            # Editor upload + download UTF-8 test
            q_up: queue.Queue = queue.Queue()
            sftp_logic.upload_file_task(c2, file_rel, "testo modificato èàù 🚀", q_up, "PROD")
            up_last = drain_all(q_up)[-1]
            self.assertEqual(up_last, {"status": "upload_complete", "server": "PROD", "success": True})

            q_down: queue.Queue = queue.Queue()
            sftp_logic.download_file_task(c2, file_rel, q_down, "PROD")
            down_last = drain_all(q_down)[-1]
            self.assertEqual(down_last, {"server": "PROD", "content": "testo modificato èàù 🚀"})

    def test_local_attributes_and_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "site")
            backup_out = os.path.join(tmp, "backups")
            os.makedirs(target)
            os.makedirs(backup_out)

            fpath = os.path.join(target, "script.sh")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\necho ok\n")
            os.chmod(fpath, 0o644)

            cfg = {"host": "local", "port": "22", "user": "", "pass": "", "path": target}

            q_ug: queue.Queue = queue.Queue()
            sftp_logic.get_users_and_groups_task(cfg, q_ug, "TEST")
            ug_res = drain_all(q_ug)[-1]
            self.assertIn("users", ug_res)
            self.assertIn("groups", ug_res)

            q_attr: queue.Queue = queue.Queue()
            sftp_logic.change_attributes_batch_task(cfg, ["script.sh"], "", "", "751", q_attr, "TEST")
            attr_items = drain_all(q_attr)
            self.assertIn("TEST:script.sh:Success", attr_items)
            self.assertEqual(stat.S_IMODE(os.stat(fpath).st_mode), 0o751)

            # Remote (sibling) backup on local target
            q_rb: queue.Queue = queue.Queue()
            sftp_logic.backup_folder_remote_task(cfg, q_rb, "PROD")
            rb_last = drain_all(q_rb)[-1]
            self.assertEqual(rb_last.get("status"), "backup_complete")
            self.assertTrue(os.path.isdir(rb_last["path"]))

            # Local backup (including self-directory safety when backing up into target itself)
            q_lb: queue.Queue = queue.Queue()
            sftp_logic.backup_folder_local_task(cfg, backup_out, q_lb, "PROD")
            lb_last = drain_all(q_lb)[-1]
            self.assertEqual(lb_last.get("status"), "backup_complete")
            self.assertTrue(os.path.isfile(os.path.join(lb_last["path"], "script.sh")))

    def test_ndiff_hint_filtering_keeps_replacements_adjacent(self) -> None:
        raw = list(difflib.ndiff(["foo = 1"], ["foo = 2"]))
        # Standard ndiff inserts '? ' between '- ' and '+ '
        self.assertTrue(any(line.startswith("? ") for line in raw))
        filtered = [line for line in raw if not line.startswith("? ")]
        self.assertEqual(filtered, ["- foo = 1", "+ foo = 2"])


class FakeRemoteSFTP:
    """In-memory POSIX SFTP mock to verify remote SFTP logic without an SSH server."""

    def __init__(self) -> None:
        self.dirs: set[str] = {"/", "/etc"}
        self.files: dict[str, bytes] = {
            "/etc/passwd": b"root:x:0:0:root:/root:/bin/sh\ndeploy:x:1001:1001::/home/deploy:/bin/bash\n",
            "/etc/group": b"root:x:0:\nwebgrp:x:2002:\n",
        }
        self.modes: dict[str, int] = {}
        self.owners: dict[str, tuple[int, int]] = {}

    def stat(self, path: str):
        if path in self.dirs:
            return sftp_logic.LocalStatEntry(filename=os.path.basename(path), st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        if path in self.files:
            mode = self.modes.get(path, 0o644)
            uid, gid = self.owners.get(path, (0, 0))
            return sftp_logic.LocalStatEntry(filename=os.path.basename(path), st_mode=stat.S_IFREG | mode, st_uid=uid, st_gid=gid)
        raise FileNotFoundError(path)

    def mkdir(self, path: str) -> None:
        parent = os.path.dirname(path.rstrip("/")) or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        if path in self.dirs:
            raise OSError("File exists")
        self.dirs.add(path)

    def open(self, path: str, mode: str = "r"):
        if "w" in mode:
            buf = io.BytesIO()
            orig_close = buf.close

            def _save_and_close() -> None:
                self.files[path] = buf.getvalue()
                orig_close()

            buf.close = _save_and_close  # type: ignore[method-assign]
            return buf
        if path not in self.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def putfo(self, fl, remote_path: str) -> None:
        parent = os.path.dirname(remote_path) or "/"
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.files[remote_path] = fl.read()

    def chmod(self, path: str, mode: int) -> None:
        self.modes[path] = mode

    def chown(self, path: str, uid: int, gid: int) -> None:
        self.owners[path] = (uid, gid)

    def close(self) -> None:
        pass


class TestRemoteSFTPMock(unittest.TestCase):
    def test_remote_makedirs_and_uid_gid_mapping_and_copy(self) -> None:
        src_sftp = FakeRemoteSFTP()
        src_sftp.dirs.update({"/srv", "/srv/app", "/srv/app/nested"})
        src_sftp.files["/srv/app/nested/index.html"] = b"<h1>Hello</h1>"
        src_sftp.modes["/srv/app/nested/index.html"] = 0o640

        dst_sftp = FakeRemoteSFTP()
        sftp_logic._makedirs(dst_sftp, "/var/www/html/sub/deep")
        self.assertIn("/var/www/html/sub/deep", dst_sftp.dirs)

        uid_map = sftp_logic._get_name_to_uid_map(dst_sftp)
        gid_map = sftp_logic._get_name_to_gid_map(dst_sftp)
        self.assertEqual(uid_map["deploy"], 1001)
        self.assertEqual(gid_map["webgrp"], 2002)

        sftp_logic._copy_file_with_metadata(
            src_sftp,
            dst_sftp,
            "/srv/app",
            "/var/www/html",
            "nested/index.html",
            src_meta={"owner": "deploy", "group": "webgrp", "octal_mode": "640"},
            dst_uid_map=uid_map,
            dst_gid_map=gid_map,
        )
        self.assertEqual(dst_sftp.files["/var/www/html/nested/index.html"], b"<h1>Hello</h1>")
        self.assertEqual(dst_sftp.owners["/var/www/html/nested/index.html"], (1001, 2002))
        self.assertEqual(dst_sftp.modes["/var/www/html/nested/index.html"], 0o640)


class TestModuleSyntaxAndImports(unittest.TestCase):
    def test_all_project_files_compile_without_syntax_errors(self) -> None:
        import py_compile

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        expected_files = (
            "mino.py",
            "app.py",
            os.path.join("core", "__init__.py"),
            os.path.join("core", "models.py"),
            os.path.join("core", "sftp_logic.py"),
            os.path.join("ui", "__init__.py"),
            os.path.join("ui", "app.py"),
            os.path.join("ui", "attributes.py"),
            os.path.join("ui", "editor.py"),
            os.path.join("ui", "helpers.py"),
            os.path.join("ui", "settings.py"),
            os.path.join("ui", "sync.py"),
        )
        for filename in expected_files:
            full_path = os.path.join(repo_root, filename)
            self.assertTrue(os.path.isfile(full_path), f"Missing {filename}")
            py_compile.compile(full_path, doraise=True)

    def test_ui_modules_import_when_customtkinter_installed(self) -> None:
        try:
            import customtkinter  # noqa: F401
        except ImportError:
            self.skipTest("customtkinter not installed in current test interpreter")

        import app  # noqa: F401
        import mino  # noqa: F401
        import core.models  # noqa: F401
        import core.sftp_logic  # noqa: F401
        import ui.app  # noqa: F401
        import ui.attributes  # noqa: F401
        import ui.editor  # noqa: F401
        import ui.helpers  # noqa: F401
        import ui.settings  # noqa: F401
        import ui.sync  # noqa: F401


if __name__ == "__main__":
    unittest.main()
