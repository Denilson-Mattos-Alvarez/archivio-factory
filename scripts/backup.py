"""Backup SQLite coerente e verificabile dei soli file registrati nel database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPOSITORY = Path(__file__).resolve().parents[1]
CHUNK = 1024 * 1024
FORMAT = "archivio-factory-backup-v1"


class BackupError(ValueError):
    """Input o archivio non verificabile; l'originale non viene modificato."""


def safe_relative(value: str) -> str:
    """Nomi portabili anche su Windows: niente alias, traversal o ADS."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise BackupError("Percorso relativo non valido.")
    if value.startswith("/") or any(ord(c) < 32 for c in value):
        raise BackupError("Percorso assoluto o caratteri di controllo.")
    pieces = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for piece in pieces:
        if piece in {"", ".", ".."} or piece.endswith((".", " ")):
            raise BackupError("Percorso ambiguo o traversal.")
        if any(c in piece for c in '<>"|?*') or piece.split(".", 1)[0].upper() in reserved:
            raise BackupError("Nome file non portabile.")
    if str(PurePosixPath(value)) != value:
        raise BackupError("Percorso non canonico.")
    return value


def checked_path(root: Path, relative: str) -> Path:
    relative = safe_relative(relative)
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for piece in relative.split("/"):
        current = current / piece
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise BackupError("Link simbolici o junction non ammessi.")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise BackupError("Il file esce dalla radice autorizzata.")
    return candidate


def checked_directory(path: Path) -> Path:
    """Rifiuta link/junction anche negli antenati, prima di risolvere il nome."""
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise BackupError("Cartella o antenato collegato tramite link.")
    return absolute


def digest_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def open_readonly(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def database_blobs(database: Path) -> dict[str, dict]:
    """Legge la snapshot, non il database attivo durante il copia dei video."""
    result = {}
    with closing(open_readonly(database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("Integrità SQLite non verificata.")
        try:
            rows = connection.execute("SELECT percorso, sha256, dimensione FROM archivio_filerevisione").fetchall()
        except sqlite3.Error as error:
            raise BackupError("Schema archivio-factory assente o incompatibile.") from error
    cases = set()
    for relative, sha256, size in rows:
        relative = safe_relative(relative)
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise BackupError("Hash nel database non valido.")
        if not isinstance(size, int) or size < 0:
            raise BackupError("Dimensione nel database non valida.")
        row = {"path": "media/" + relative, "sha256": sha256, "size": size}
        if relative in result and result[relative] != row:
            raise BackupError("Lo stesso blob ha riferimenti discordanti.")
        if relative not in result and relative.casefold() in cases:
            raise BackupError("Collisione di nomi su filesystem Windows.")
        result[relative] = row
        cases.add(relative.casefold())
    return result


def add_verified(archive: zipfile.ZipFile, path: Path, row: dict) -> None:
    """Verifica i byte effettivamente inseriti nello ZIP, evitando un TOCTOU."""
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source, archive.open(row["path"], "w", force_zip64=True) as destination:
        while chunk := source.read(CHUNK):
            destination.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != row["sha256"] or size != row["size"]:
        raise BackupError("Un blob è mancante, cambiato o non coincide con il database.")


def create_backup(runtime: Path, output: Path, media_root: Path | None = None) -> dict:
    runtime = checked_directory(runtime)
    media = checked_directory(media_root or runtime / "media")
    output = checked_directory(output)
    if output.is_relative_to(REPOSITORY.resolve()):
        raise BackupError("Salvare il backup fuori dal repository.")
    if output.exists() or not output.parent.is_dir():
        raise BackupError("Il file destinazione esiste o la cartella non esiste.")
    database = checked_path(runtime, "db.sqlite3")
    if not database.is_file() or not media.is_dir():
        raise BackupError("Database SQLite o cartella media assente.")
    # Il file temporaneo risiede sullo stesso volume del backup finale.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".archivio-backup-", suffix=".zip.tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with tempfile.TemporaryDirectory(prefix=".archivio-snapshot-", dir=output.parent) as snapshot_dir:
            snapshot = Path(snapshot_dir) / "db.sqlite3"
            with closing(open_readonly(database)) as source, closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
            references = database_blobs(snapshot)
            sha256, size = digest_file(snapshot)
            files = [{"path": "db.sqlite3", "sha256": sha256, "size": size}]
            files.extend(sorted(references.values(), key=lambda row: row["path"]))
            manifest = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "files": files}
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                add_verified(archive, snapshot, files[0])
                for relative, row in sorted(references.items()):
                    blob = checked_path(media, relative)
                    if not blob.is_file():
                        raise BackupError("Blob registrato mancante.")
                    add_verified(archive, blob, row)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        # link() è una pubblicazione atomica che fallisce se il nome esiste già.
        try:
            os.link(temporary, output)
        except OSError as error:
            raise BackupError("Impossibile pubblicare il backup senza sovrascrivere; usare un filesystem con hard link.") from error
        return {"output": str(output), "files": len(files), "bytes": sum(row["size"] for row in files), "sha256": digest_file(output)[0]}
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    # Legge soltanto la selezione del database/runtime, senza caricare o stampare segreti.
    selected = {}
    env_file = REPOSITORY / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"DATABASE_URL", "ARCHIVIO_RUNTIME_ROOT", "ARCHIVIO_MEDIA_ROOT"}:
                selected[key.strip()] = value.strip().strip('"\'')
    for key in {"DATABASE_URL", "ARCHIVIO_RUNTIME_ROOT", "ARCHIVIO_MEDIA_ROOT"}:
        if key in os.environ:
            selected[key] = os.environ[key]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(selected.get("ARCHIVIO_RUNTIME_ROOT") or REPOSITORY / "runtime"))
    parser.add_argument("--media-root", type=Path, default=Path(selected["ARCHIVIO_MEDIA_ROOT"]) if selected.get("ARCHIVIO_MEDIA_ROOT") else None,
                        help="Se ARCHIVIO_MEDIA_ROOT è diverso da runtime/media")
    parser.add_argument("--output", type=Path, required=True, help="ZIP nuovo fuori dal repository")
    args = parser.parse_args(argv)
    if selected.get("DATABASE_URL"):
        parser.error("DATABASE_URL configurata: questo strumento è solo SQLite e non esporta PostgreSQL. Usare una procedura PostgreSQL dedicata.")
    try:
        print(json.dumps(create_backup(args.runtime, args.output, args.media_root), ensure_ascii=False))
        return 0
    except (BackupError, OSError, sqlite3.Error) as error:
        parser.exit(1, "Backup interrotto: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
