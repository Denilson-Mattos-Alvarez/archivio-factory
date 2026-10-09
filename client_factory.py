"""Ponte HTTP originale per depositare video e prelevare richieste dalla Factory.

Non avvia render e non esegue file ricevuti dall'archivio. Solo libreria standard.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import mimetypes
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CHUNK = 1024 * 1024
MAX_JSON = 16 * 1024 * 1024


class ClientError(ValueError):
    pass


class APIError(ClientError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"L'archivio ha risposto HTTP {status}.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Non inoltrare mai una chiave Factory verso un'origine o percorso rediretto.
        return None


def file_fingerprint(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ClientError("Usare file regolari esistenti, non link simbolici.")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    name = path.name
    if any(ord(c) < 32 for c in name) or '"' in name or "\\" in name:
        raise ClientError("Nome del file non valido per un deposito HTTP.")
    return {"path": path, "name": name, "sha256": digest.hexdigest(), "size": size}


def real_directory(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir():
        raise ClientError("Usare una cartella inbox assoluta ed esistente.")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ClientError("Inbox e antenati non possono essere link o junction.")
    return path


class Multipart:
    """Iterabile riapribile: un retry trasmette gli stessi file e la stessa chiave."""
    def __init__(self, fields: dict, files: list[tuple[str, dict]]):
        self.boundary = "archivio-" + uuid.uuid4().hex
        self.parts = []
        for name, value in fields.items():
            prefix = f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            data = str(value).encode("utf-8")
            self.parts.append((prefix, data))
        for name, file in files:
            mime = mimetypes.guess_type(file["name"])[0] or "application/octet-stream"
            prefix = (f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{file["name"]}"\r\n'
                      f'Content-Type: {mime}\r\n\r\n').encode("utf-8")
            self.parts.append((prefix, file))
        self.ending = f"--{self.boundary}--\r\n".encode()
        self.length = len(self.ending) + sum(len(prefix) + (len(value) if isinstance(value, bytes) else value["size"]) + 2 for prefix, value in self.parts)

    def __iter__(self):
        for prefix, value in self.parts:
            yield prefix
            if isinstance(value, bytes):
                yield value
            else:
                digest, size = hashlib.sha256(), 0
                with value["path"].open("rb") as stream:
                    while chunk := stream.read(CHUNK):
                        digest.update(chunk)
                        size += len(chunk)
                        yield chunk
                if size != value["size"] or digest.hexdigest() != value["sha256"]:
                    raise ClientError("Il file sorgente è cambiato durante il deposito; ripetere con file stabili.")
            yield b"\r\n"
        yield self.ending


class FactoryClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60):
        base = urllib.parse.urlsplit(base_url.rstrip("/"))
        if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password or base.query or base.fragment:
            raise ClientError("URL archivio non valido.")
        if base.scheme == "http" and base.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ClientError("HTTP senza TLS consentito solo sul computer locale.")
        if base.path not in {"", "/"}:
            raise ClientError("Indicare l'origine dell'archivio, senza /api o altri percorsi.")
        if not api_key.startswith("af_") or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ClientError("Chiave Factory assente o non valida; leggerla da una variabile d'ambiente.")
        if timeout <= 0:
            raise ClientError("Timeout non valido.")
        self.base_url = urllib.parse.urlunsplit((base.scheme, base.netloc, "", "", ""))
        self.api_key, self.timeout = api_key, timeout
        # Il ponte non eredita proxy ambientali: le credenziali vanno direttamente all'origine scelta.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _url(self, path: str) -> str:
        value = urllib.parse.urljoin(self.base_url + "/", path)
        parsed, base = urllib.parse.urlsplit(value), urllib.parse.urlsplit(self.base_url)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or not parsed.path.startswith("/api/v1/") or parsed.fragment:
            raise ClientError("Link API estraneo all'archivio.")
        return value

    def _open(self, method: str, path: str, data=None, headers=None):
        values = {"Authorization": "Bearer " + self.api_key, "Accept": "application/json", **(headers or {})}
        request = urllib.request.Request(self._url(path), data=data, headers=values, method=method)
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            error.close()
            raise APIError(error.code) from None

    def _json(self, method: str, path: str, data=None, headers=None, safe_retry=False):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", **(headers or {})}
        for attempt in range(2 if safe_retry else 1):
            try:
                with self._open(method, path, data, headers) as response:
                    payload = response.read(MAX_JSON + 1)
                if len(payload) > MAX_JSON:
                    raise ClientError("Risposta JSON oltre il limite.")
                return json.loads(payload)
            except APIError as error:
                if not safe_retry or attempt or error.status not in {502, 503, 504}:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
                if not safe_retry or attempt:
                    raise ClientError("Risposta di rete incerta; nessuna conferma di completamento. Ripetere con la stessa sorgente e riferimento.") from error
            except (ValueError, UnicodeError) as error:
                if isinstance(error, ClientError):
                    raise
                raise ClientError("Risposta JSON dell'archivio non valida.") from error
            time.sleep(0.25)
        raise ClientError("Risposta non disponibile.")

    def listing(self, path: str) -> list:
        rows, visited = [], set()
        while path:
            url = self._url(path)
            if url in visited or len(visited) >= 1000:
                raise ClientError("Paginazione API ciclica o oltre il limite.")
            visited.add(url)
            result = self._json("GET", path, safe_retry=True)
            if isinstance(result, list):
                return rows + result
            if not isinstance(result, dict) or not isinstance(result.get("results"), list):
                raise ClientError("Lista API non valida.")
            rows.extend(result["results"])
            path = result.get("next")
        return rows

    def production(self, title: str, origin: str, reference: str, space: str | None = None) -> dict:
        if origin not in {"kaapi", "factory", "altro"} or not reference.strip():
            raise ClientError("Origine non valida o riferimento Factory vuoto.")
        query = {"origine": origin, "riferimento_factory": reference}
        if space:
            query["spazio_id"] = space
        route = "/api/v1/produzioni/?" + urllib.parse.urlencode(query)

        def find():
            matches = self.listing(route)
            if len(matches) > 1:
                raise ClientError("Riferimento ambiguo: più produzioni corrispondenti.")
            return matches[0] if matches else None

        existing = find()
        if existing:
            return existing
        body = {"titolo": title, **query}
        try:
            return self._json("POST", "/api/v1/produzioni/", body)
        except ClientError:
            # Dopo timeout o concorrenza controlla l'esito; non crea nuovamente alla cieca.
            existing = find()
            if existing:
                return existing
            raise

    def deposit(self, *, title: str, origin: str, reference: str, video: Path, script: str = "", note: str = "", attachments=(), space=None, request_id=None) -> dict:
        if request_id is not None:
            try:
                request_id = str(uuid.UUID(str(request_id)))
            except ValueError as error:
                raise ClientError("ID richiesta non valido.") from error
        sources = [("video", file_fingerprint(video))]
        attachment_rows = sorted((file_fingerprint(Path(path)) for path in attachments), key=lambda row: (row["name"], row["sha256"], row["size"]))
        sources.extend(("allegati", row) for row in attachment_rows)
        production = self.production(title, origin, reference, space)
        try:
            production_id = str(uuid.UUID(str(production["id"])))
        except (KeyError, ValueError, TypeError) as error:
            raise ClientError("Identificatore produzione non valido.") from error
        fingerprint = {"produzione": production_id, "copione": script, "nota": note,
                       "file": [{"campo": field, **{key: row[key] for key in ("name", "sha256", "size")}} for field, row in sources]}
        idempotency = "factory-" + hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        multipart = Multipart({"copione": script, "nota": note}, sources)
        result = self._json("POST", f"/api/v1/produzioni/{production_id}/revisioni/", multipart,
                            {"Content-Type": "multipart/form-data; boundary=" + multipart.boundary,
                             "Content-Length": str(multipart.length), "Idempotency-Key": idempotency}, safe_retry=True)
        if not isinstance(result, dict) or not result.get("id"):
            raise ClientError("Il deposito non ha restituito una revisione valida.")
        if request_id:
            # Il backend controlla che la revisione sia successiva e della stessa produzione.
            # Se la risposta è incerta, lo stesso deposito può essere ripetuto senza duplicare la revisione.
            self._json("POST", f"/api/v1/factory/richieste/{request_id}/completa/",
                       {"nuova_revisione_id": result["id"]}, safe_retry=True)
        return result

    def revision(self, revision_id: str) -> dict:
        try:
            revision_id = str(uuid.UUID(str(revision_id)))
        except ValueError as error:
            raise ClientError("ID revisione non valido.") from error
        row = self._json("GET", f"/api/v1/revisioni/{revision_id}/", safe_retry=True)
        if not isinstance(row, dict) or str(row.get("id")) != revision_id or not isinstance(row.get("file"), list):
            raise ClientError("Risposta revisione non valida.")
        return row

    def _download(self, row: dict, destination: Path) -> dict:
        try:
            file_id = str(uuid.UUID(str(row["id"])))
            expected_hash, expected_size = row["sha256"], row["dimensione"]
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or type(expected_size) is not int or expected_size < 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError) as error:
            raise ClientError("Descrittore file non valido.") from error
        digest, size = hashlib.sha256(), 0
        # URL costruito dall'ID: il nome originale o un URL nel JSON non decide la destinazione.
        with self._open("GET", f"/api/v1/file/{file_id}/") as source, destination.open("xb") as output:
            while chunk := source.read(CHUNK):
                size += len(chunk)
                if size > expected_size:
                    raise ClientError("File ricevuto più grande del dichiarato.")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ClientError("SHA-256 o dimensione del file ricevuto non coincide.")
        return {"id": file_id, "ruolo": row.get("ruolo"), "nome_originale": row.get("nome_originale"),
                "percorso": destination.name, "sha256": expected_hash, "dimensione": size}

    def _verify_existing_job(self, directory: Path, request: dict) -> dict:
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise ClientError("Cartella inbox collegata tramite link.")
        try:
            if (directory / "job.json").is_symlink() or (directory / "copione.txt").is_symlink():
                raise ClientError("Il job locale contiene link simbolici.")
            job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
            if str(job["richiesta"]["id"]) != str(request["id"]) or job["revisione"] != str(request["revisione"]) or job["manifest_sha256"] != request["manifest_sha256"]:
                raise ClientError("Il job locale esistente non corrisponde alla richiesta.")
            if hashlib.sha256((directory / "copione.txt").read_bytes()).hexdigest() != job["copione_sha256"]:
                raise ClientError("Il copione locale è cambiato: non esportare nuovamente.")
            metadata_path = directory / "revisione.json"
            if metadata_path.is_symlink() or hashlib.sha256(metadata_path.read_bytes()).hexdigest() != job["metadati_sha256"]:
                raise ClientError("I metadati della revisione locale sono cambiati.")
            for row in job["file"]:
                name = row["percorso"]
                if name != Path(name).name or "/" in name or "\\" in name or ":" in name:
                    raise ClientError("Nome file locale non sicuro.")
                actual = file_fingerprint(directory / name)
                if actual["sha256"] != row["sha256"] or actual["size"] != row["dimensione"]:
                    raise ClientError("Un file del job locale è cambiato.")
            return job
        except (KeyError, ValueError, OSError) as error:
            if isinstance(error, ClientError):
                raise
            raise ClientError("Job locale incompleto o non valido.") from error

    def pull_requests(self, inbox: Path) -> list[dict]:
        inbox = real_directory(Path(inbox))
        exported = []
        for request in self.listing("/api/v1/factory/richieste/?stato=in_attesa"):
            try:
                request_id = str(uuid.UUID(str(request["id"])))
                revision_id = str(uuid.UUID(str(request["revisione"])))
            except (KeyError, ValueError, TypeError) as error:
                raise ClientError("Richiesta Factory non valida.") from error
            destination = inbox / ("richiesta-" + request_id)
            if destination.exists():
                job = self._verify_existing_job(destination, request)
            else:
                staging = Path(tempfile.mkdtemp(prefix=".richiesta-", dir=inbox))
                try:
                    revision = self.revision(revision_id)
                    if revision.get("manifest_sha256") != request.get("manifest_sha256"):
                        raise ClientError("La revisione ricevuta non coincide con quella richiesta.")
                    files = []
                    for row in revision["file"]:
                        file_id = str(uuid.UUID(str(row["id"])))
                        # Estensione presa solo da MIME noto, non da nomi remoti.
                        suffix = {"video/mp4": ".mp4", "video/webm": ".webm", "application/json": ".json", "text/plain": ".txt", "audio/wav": ".wav"}.get(row.get("mime"), ".bin")
                        files.append(self._download(row, staging / (file_id + suffix)))
                    script = revision.get("copione", "")
                    if not isinstance(script, str):
                        raise ClientError("Copione non valido.")
                    (staging / "copione.txt").write_bytes(script.encode("utf-8"))
                    metadata = json.dumps(revision, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
                    (staging / "revisione.json").write_bytes(metadata)
                    job = {"formato": "archivio-factory-job-v1", "stato_ponte": "da_dispatcher", "richiesta": request,
                           "revisione": revision_id, "produzione": str(revision["produzione"]),
                           "manifest_sha256": revision["manifest_sha256"], "copione_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                           "metadati_sha256": hashlib.sha256(metadata).hexdigest(),
                           "file": files, "origine": request.get("origine"), "riferimento_factory": request.get("riferimento_factory")}
                    (staging / "job.json").write_text(json.dumps(job, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
                    # UUID richieste unico; fallisce se il nome esiste già su Windows.
                    if destination.exists():
                        raise ClientError("Un altro processo ha già creato lo stesso job; ripetere il prelievo.")
                    os.rename(staging, destination)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            self._json("POST", f"/api/v1/factory/richieste/{request_id}/esportata/", {})
            exported.append({"richiesta": request_id, "cartella": str(destination), "revisione": job["revisione"], "stato": "esportata, in attesa del dispatcher"})
        return exported


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("ARCHIVIO_URL", "http://127.0.0.1:58540"))
    parser.add_argument("--chiave-env", default="ARCHIVIO_FACTORY_API_KEY", help="Nome della variabile; non il valore segreto")
    parser.add_argument("--timeout", type=float, default=60)
    commands = parser.add_subparsers(dest="command", required=True)
    deposit = commands.add_parser("deposita", help="Crea produzione o aggiunge revisione immutabile")
    deposit.add_argument("--titolo", required=True)
    deposit.add_argument("--origine", required=True, choices=["kaapi", "factory", "altro"])
    deposit.add_argument("--riferimento-factory", required=True)
    deposit.add_argument("--video", type=Path, required=True)
    deposit.add_argument("--copione", type=Path, help="File testo UTF-8")
    deposit.add_argument("--nota", default="")
    deposit.add_argument("--allegato", action="append", type=Path, default=[])
    deposit.add_argument("--spazio", help="Opzionale con chiave Factory già limitata a uno spazio")
    deposit.add_argument("--richiesta-id", help="Collega la nuova revisione a una richiesta verificata dal backend")
    pull = commands.add_parser("preleva-richieste", help="Scarica bundle storico verificato; non avvia render")
    pull.add_argument("--inbox", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        client = FactoryClient(args.url, os.getenv(args.chiave_env, ""), args.timeout)
        if args.command == "deposita":
            script = args.copione.read_bytes().decode("utf-8") if args.copione else ""
            revision = client.deposit(title=args.titolo, origin=args.origine, reference=args.riferimento_factory,
                                      video=args.video, script=script, note=args.nota, attachments=args.allegato, space=args.spazio, request_id=args.richiesta_id)
            result = {"produzione": revision.get("produzione"), "revisione": revision["id"], "numero": revision.get("numero"), "manifest_sha256": revision.get("manifest_sha256")}
        else:
            result = client.pull_requests(args.inbox)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ClientError, OSError, UnicodeError) as error:
        parser.exit(1, "Ponte interrotto: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
