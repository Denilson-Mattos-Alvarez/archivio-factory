import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.backup import BackupError, create_backup, safe_relative
from scripts.ripristina import restore_backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="af-backup-test-", dir=os.getenv("ARCHIVIO_TEST_TMP"))
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "originale"
        self.media = self.runtime / "media"
        self.media.mkdir(parents=True)
        self.payloads = {"spazio/revisione/video.mp4": b"synthetic-video-v1", "spazio/revisione2/video.mp4": b"synthetic-video-v2"}
        database = sqlite3.connect(self.runtime / "db.sqlite3")
        database.execute("CREATE TABLE archivio_filerevisione (percorso TEXT, sha256 TEXT, dimensione INTEGER)")
        for relative, payload in self.payloads.items():
            path = self.media / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            database.execute("INSERT INTO archivio_filerevisione VALUES (?, ?, ?)", (relative, hashlib.sha256(payload).hexdigest(), len(payload)))
        database.commit()
        database.close()
        self.archive = self.root / "backup.zip"

    def tearDown(self):
        self.temporary.cleanup()

    def test_roundtrip_preserves_versions_original_and_db(self):
        before = (self.runtime / "db.sqlite3").read_bytes()
        created = create_backup(self.runtime, self.archive)
        self.assertEqual(created["files"], 3)
        destination = self.root / "nuovo"
        restored = restore_backup(self.archive, destination)
        self.assertTrue(restored["verified"])
        self.assertEqual((self.runtime / "db.sqlite3").read_bytes(), before)
        for relative, payload in self.payloads.items():
            self.assertEqual((destination / "media" / relative).read_bytes(), payload)
            self.assertEqual((self.media / relative).read_bytes(), payload)
        self.assertNotEqual(*self.payloads.values())

    def test_online_backup_includes_committed_wal_data(self):
        database = sqlite3.connect(self.runtime / "db.sqlite3")
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE marker (value TEXT)")
            database.execute("INSERT INTO marker VALUES ('committed-in-wal')")
            database.commit()
            create_backup(self.runtime, self.archive)
            destination = self.root / "wal-restored"
            restore_backup(self.archive, destination)
            restored = sqlite3.connect(destination / "db.sqlite3")
            try:
                self.assertEqual(restored.execute("SELECT value FROM marker").fetchone()[0], "committed-in-wal")
            finally:
                restored.close()
        finally:
            database.close()

    def test_missing_or_changed_registered_blob_aborts(self):
        (self.media / next(iter(self.payloads))).write_bytes(b"changed")
        with self.assertRaises(BackupError):
            create_backup(self.runtime, self.archive)
        self.assertFalse(self.archive.exists())

    def test_unregistered_orphans_not_in_backup(self):
        (self.media / "unregistered.tmp").write_bytes(b"incomplete-upload")
        create_backup(self.runtime, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("media/unregistered.tmp", archive.namelist())

    def test_existing_backup_and_restore_never_overwritten(self):
        create_backup(self.runtime, self.archive)
        before = self.archive.read_bytes()
        with self.assertRaises(BackupError):
            create_backup(self.runtime, self.archive)
        with self.assertRaises(BackupError):
            restore_backup(self.archive, self.runtime)
        self.assertEqual(self.archive.read_bytes(), before)

    def rewrite(self, edit):
        create_backup(self.runtime, self.archive)
        with zipfile.ZipFile(self.archive) as source:
            entries = [(item.filename, source.read(item.filename)) for item in source.infolist()]
        malicious = self.root / "manomesso.zip"
        with zipfile.ZipFile(malicious, "w") as output:
            edit(entries, output)
        return malicious

    def test_tampered_bytes_rejected_without_target_remaining(self):
        def edit(entries, output):
            for name, payload in entries:
                output.writestr(name, payload[:-1] + b"!" if name.endswith("video.mp4") else payload)
        corrupted = self.rewrite(edit)
        target = self.root / "restore-failed"
        with self.assertRaises(BackupError):
            restore_backup(corrupted, target)
        self.assertFalse(target.exists())

    def test_path_traversal_and_symlink_rejected(self):
        def edit(entries, output):
            for name, payload in entries:
                output.writestr(name, payload)
            output.writestr("../outside.txt", b"forbidden")
        malicious = self.rewrite(edit)
        with self.assertRaises(BackupError):
            restore_backup(malicious, self.root / "traversal")
        self.assertFalse((self.root / "outside.txt").exists())
        symlink = self.root / "symlink.zip"
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(symlink, "w") as output:
            for name in source.namelist():
                item = zipfile.ZipInfo(name)
                item.external_attr = ((stat.S_IFLNK | 0o777) if name == "db.sqlite3" else (stat.S_IFREG | 0o600)) << 16
                output.writestr(item, source.read(name))
        with self.assertRaises(BackupError):
            restore_backup(symlink, self.root / "link")

    def test_manifest_cannot_hide_db_reference(self):
        def edit(entries, output):
            removed = "media/" + next(iter(self.payloads))
            for name, payload in entries:
                if name == removed:
                    continue
                if name == "manifest.json":
                    manifest = json.loads(payload)
                    manifest["files"] = [row for row in manifest["files"] if row["path"] != removed]
                    payload = json.dumps(manifest).encode()
                output.writestr(name, payload)
        corrupted = self.rewrite(edit)
        with self.assertRaisesRegex(BackupError, "riferimenti"):
            restore_backup(corrupted, self.root / "missing")

    def test_portable_paths_and_limits(self):
        for path in ("../x", "/x", "C:/x", "a\\x", "a//x", "a/NUL.mp4", "a/x.", "a/x ", "a/:secret"):
            with self.subTest(path=path), self.assertRaises(BackupError):
                safe_relative(path)
        create_backup(self.runtime, self.archive)
        with self.assertRaises(BackupError):
            restore_backup(self.archive, self.root / "small", maximum_bytes=1)


if __name__ == "__main__":
    unittest.main()
