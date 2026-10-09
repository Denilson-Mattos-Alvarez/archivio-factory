"""Ripristina un backup verificato in una NUOVA cartella runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path

try:
    from .backup import BackupError, CHUNK, FORMAT, REPOSITORY, checked_directory, checked_path, database_blobs, safe_relative
except ImportError:
    from backup import BackupError, CHUNK, FORMAT, REPOSITORY, checked_directory, checked_path, database_blobs, safe_relative


def inspect_archive(archive: zipfile.ZipFile, maximum_bytes: int) -> dict:
    infos = archive.infolist()
    if len(infos) > 100_000 or not infos:
        raise BackupError("Numero di file non ammesso.")
    names, cases, total = set(), set(), 0
    for info in infos:
        name = safe_relative(info.filename)
        if info.is_dir() or name in names or name.casefold() in cases:
            raise BackupError("Cartelle esplicite, nomi duplicati o collisioni non ammessi.")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
            raise BackupError("Link simbolici o file speciali nello ZIP.")
        if info.flag_bits & 1:
            raise BackupError("ZIP cifrato non supportato da questo ripristino.")
        total += info.file_size
        names.add(name)
        cases.add(name.casefold())
    if total > maximum_bytes or "manifest.json" not in names:
        raise BackupError("Dimensione oltre il limite o manifest assente.")
    if archive.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
        raise BackupError("Manifest troppo grande.")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (ValueError, UnicodeError) as error:
        raise BackupError("Manifest JSON non valido.") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), list):
        raise BackupError("Formato backup non supportato.")
    rows = {}
    for row in manifest["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            raise BackupError("Voce manifest non valida.")
        name = safe_relative(row["path"])
        if name != "db.sqlite3" and not name.startswith("media/"):
            raise BackupError("Il backup contiene file estranei al runtime.")
        if name in rows or not re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"])):
            raise BackupError("Hash o nome manifest non valido.")
        if type(row["size"]) is not int or row["size"] < 0:
            raise BackupError("Dimensione manifest non valida.")
        if name not in names or archive.getinfo(name).file_size != row["size"]:
            raise BackupError("File mancante o dimensione discordante.")
        rows[name] = row
    if "db.sqlite3" not in rows or names != set(rows) | {"manifest.json"}:
        raise BackupError("Il contenuto ZIP non coincide esattamente con il manifest.")
    return rows


def restore_backup(backup: Path, target: Path, maximum_bytes: int = 100 * 1024**3) -> dict:
    if not target.is_absolute():
        raise BackupError("Indicare una destinazione assoluta esplicita.")
    target = checked_directory(target)
    backup = checked_directory(backup)
    if target.exists() or not target.parent.is_dir():
        raise BackupError("La destinazione deve essere nuova e avere una cartella padre esistente.")
    if target == Path(target.anchor) or target == REPOSITORY.resolve() or backup.is_relative_to(target):
        raise BackupError("Destinazione non ammessa.")
    # Riserva il nome mediante creazione esclusiva: nessun'altra cartella viene sovrascritta.
    target.mkdir(mode=0o700)
    staging = None
    committed = False
    try:
        staging = Path(tempfile.mkdtemp(prefix=".archivio-ripristino-", dir=target.parent))
        with zipfile.ZipFile(backup, "r") as archive:
            rows = inspect_archive(archive, maximum_bytes)
            for name, row in rows.items():
                destination = checked_path(staging, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open(name) as source, destination.open("xb") as output:
                    while chunk := source.read(CHUNK):
                        size += len(chunk)
                        if size > row["size"]:
                            raise BackupError("Un file supera la dimensione dichiarata.")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size != row["size"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("Verifica SHA-256 fallita: ripristino annullato.")
        refs = database_blobs(staging / "db.sqlite3")
        expected = {row["path"]: row for row in refs.values()}
        actual = {name: row for name, row in rows.items() if name.startswith("media/")}
        if actual != expected:
            raise BackupError("I blob del backup non coincidono con i riferimenti del database.")
        (staging / "media").mkdir(exist_ok=True)
        # target è la cartella vuota creata da noi; non si tocca alcun runtime precedente.
        # Spostamento atomico su Windows; rename di directory su POSIX sostituisce solo la nostra cartella vuota.
        if any(target.iterdir()):
            raise BackupError("La cartella riservata è stata modificata durante il ripristino.")
        if os.name == "nt":
            target.rmdir()
            os.rename(staging, target)
        else:
            os.rename(staging, target)
        committed = True
        return {"runtime": str(target), "files": len(rows), "bytes": sum(row["size"] for row in rows.values()), "verified": True}
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        if not committed and target.exists() and not any(target.iterdir()):
            target.rmdir()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--destinazione", type=Path, required=True, help="NUOVA cartella assoluta; mai il runtime originale")
    parser.add_argument("--massimo-gib", type=int, default=100, help="Limite totale prima di leggere il backup")
    args = parser.parse_args(argv)
    if args.massimo_gib <= 0:
        parser.error("Il limite deve essere positivo.")
    try:
        print(json.dumps(restore_backup(args.backup, args.destinazione, args.massimo_gib * 1024**3), ensure_ascii=False))
        return 0
    except (BackupError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(1, "Ripristino interrotto: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
