"""Contratti del ponte su server HTTP sintetico locale; non una prova della Factory reale."""
import hashlib
import json
import os
import tempfile
import threading
import unittest
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from client_factory import APIError, ClientError, FactoryClient, Multipart, file_fingerprint


class State:
    def __init__(self):
        self.productions = []
        self.revisions = []
        self.files = {}
        self.requests = []
        self.idempotency = {}
        self.fail_upload_after_commit = False
        self.fail_create_after_commit = False
        self.fail_ack_once = False
        self.foreign_next = False
        self.bad_file_hash = False
        self.uploads = 0
        self.downloads = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    @property
    def state(self):
        return self.server.state

    def respond(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if self.headers.get("Authorization") != "Bearer af_synthetic_test_only":
            self.respond(401, {})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        parsed = urlsplit(self.path)
        route = parsed.path
        if route == "/api/v1/produzioni/":
            query = parse_qs(parsed.query)
            rows = [row for row in self.state.productions if row["origine"] == query["origine"][0] and row["riferimento_factory"] == query["riferimento_factory"][0]]
            self.respond(200, {"results": rows, "next": "http://example.invalid/api/v1/produzioni/" if self.state.foreign_next else None, "count": len(rows)})
        elif route == "/api/v1/factory/richieste/":
            self.respond(200, {"results": [row for row in self.state.requests if row["stato"] == "in_attesa"], "next": None})
        elif route.startswith("/api/v1/revisioni/"):
            revision = next((row for row in self.state.revisions if row["id"] == route.split("/")[4]), None)
            if revision:
                self.respond(200, revision)
            else:
                self.respond(404, {})
        elif route.startswith("/api/v1/file/"):
            self.state.downloads += 1
            payload = self.state.files[route.split("/")[4]]
            if self.state.bad_file_hash:
                payload = payload[:-1] + b"!"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.respond(404, {})

    def do_POST(self):
        if not self.authorized():
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        route = urlsplit(self.path).path
        if route == "/api/v1/produzioni/":
            row = json.loads(body)
            if any(old["origine"] == row["origine"] and old["riferimento_factory"] == row["riferimento_factory"] for old in self.state.productions):
                self.respond(409, {})
                return
            row["id"] = str(uuid.uuid4())
            self.state.productions.append(row)
            if self.state.fail_create_after_commit:
                self.state.fail_create_after_commit = False
                self.respond(503, {})
            else:
                self.respond(201, row)
        elif route.startswith("/api/v1/produzioni/") and route.endswith("/revisioni/"):
            self.state.uploads += 1
            production = route.split("/")[4]
            key = (production, self.headers.get("Idempotency-Key"))
            if key in self.state.idempotency:
                self.respond(200, self.state.idempotency[key])
                return
            message = BytesParser(policy=policy.default).parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
            row = {"id": str(uuid.uuid4()), "produzione": production, "numero": 1 + sum(item["produzione"] == production for item in self.state.revisions), "file": []}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True)
                if name in {"video", "allegati"}:
                    file_id = str(uuid.uuid4())
                    self.state.files[file_id] = payload
                    row["file"].append({"id": file_id, "ruolo": "video" if name == "video" else "allegato", "nome_originale": part.get_filename(), "mime": part.get_content_type(), "sha256": hashlib.sha256(payload).hexdigest(), "dimensione": len(payload)})
                else:
                    row[name] = payload.decode("utf-8")
            row["manifest_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            self.state.revisions.append(row)
            self.state.idempotency[key] = row
            if self.state.fail_upload_after_commit:
                self.state.fail_upload_after_commit = False
                self.respond(502, {})
            else:
                self.respond(201, row)
        elif route.startswith("/api/v1/factory/richieste/"):
            request = next(row for row in self.state.requests if row["id"] == route.split("/")[5])
            if route.endswith("/esportata/"):
                if self.state.fail_ack_once:
                    self.state.fail_ack_once = False
                    self.respond(503, {})
                else:
                    request["stato"] = "esportata"
                    self.respond(200, request)
            elif route.endswith("/completa/"):
                revision = next(row for row in self.state.revisions if row["id"] == json.loads(body)["nuova_revisione_id"])
                if revision["produzione"] != request["produzione"] or revision["id"] == request["revisione"]:
                    self.respond(409, {})
                else:
                    request["stato"] = "consegnata"
                    request["nuova_revisione"] = revision["id"]
                    self.respond(200, request)
            else:
                self.respond(404, {})
        else:
            self.respond(404, {})


class FactoryClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="af-client-test-", dir=os.getenv("ARCHIVIO_TEST_TMP"))
        self.root = Path(self.temp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"synthetic-video-version-one")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.state = self.state = State()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = FactoryClient(f"http://127.0.0.1:{self.server.server_port}", "af_synthetic_test_only", timeout=2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def deposit(self, **kwargs):
        return self.client.deposit(title="Notizia sintetica", origin="kaapi", reference="lab-external-001", video=self.video, script="Copione à v1", **kwargs)

    def request(self, revision):
        row = {"id": str(uuid.uuid4()), "revisione": revision["id"], "produzione": revision["produzione"], "manifest_sha256": revision["manifest_sha256"], "target": "montaggio", "testo": "Ridurre introduzione", "stato": "in_attesa", "origine": "kaapi", "riferimento_factory": "lab-external-001"}
        self.state.requests.append(row)
        return row

    def test_two_versions_and_repeat_have_distinct_immutable_bytes(self):
        attachment = self.root / "episode.json"
        attachment.write_text('{"scena": 1}')
        first = self.deposit(attachments=[attachment])
        again = self.deposit(attachments=[attachment])
        self.assertEqual(first["id"], again["id"])
        self.video.write_bytes(b"synthetic-video-version-two")
        second = self.deposit(attachments=[attachment])
        self.assertEqual(len(self.state.productions), 1)
        self.assertEqual([row["numero"] for row in self.state.revisions], [1, 2])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.state.files[first["file"][0]["id"]], b"synthetic-video-version-one")
        self.assertEqual(self.state.files[second["file"][0]["id"]], b"synthetic-video-version-two")
        self.assertEqual(first["copione"], "Copione à v1")

    def test_ambiguous_upload_retry_cannot_duplicate_version(self):
        self.state.fail_upload_after_commit = True
        row = self.deposit()
        self.assertEqual(self.state.uploads, 2)
        self.assertEqual(len(self.state.revisions), 1)
        self.assertEqual(row["id"], self.state.revisions[0]["id"])

    def test_attachment_order_does_not_create_a_duplicate_version(self):
        first_file, second_file = self.root / "a.json", self.root / "b.json"
        first_file.write_text('{"a":1}')
        second_file.write_text('{"b":2}')
        first = self.deposit(attachments=[first_file, second_file])
        second = self.deposit(attachments=[second_file, first_file])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.state.revisions), 1)

    def test_ambiguous_production_create_reconciles_by_reference(self):
        self.state.fail_create_after_commit = True
        self.deposit()
        self.assertEqual(len(self.state.productions), 1)

    def test_historical_request_downloads_requested_version(self):
        first = self.deposit()
        request = self.request(first)
        self.video.write_bytes(b"newer-video")
        self.deposit()
        first["file"][0]["nome_originale"] = "../../outside.mp4"
        exported = self.client.pull_requests(self.root)
        directory = Path(exported[0]["cartella"])
        job = json.loads((directory / "job.json").read_text())
        self.assertEqual((directory / job["file"][0]["percorso"]).read_bytes(), b"synthetic-video-version-one")
        self.assertEqual(job["revisione"], first["id"])
        self.assertEqual(job["stato_ponte"], "da_dispatcher")
        self.assertEqual(request["stato"], "esportata")
        self.assertFalse((self.root.parent / "outside.mp4").exists())

    def test_changed_download_is_not_exported_and_staging_removed(self):
        self.request(self.deposit())
        self.state.bad_file_hash = True
        with self.assertRaisesRegex(ClientError, "SHA-256"):
            self.client.pull_requests(self.root)
        self.assertEqual(self.state.requests[0]["stato"], "in_attesa")
        self.assertFalse(any(path.name.startswith(("richiesta-", ".richiesta-")) for path in self.root.iterdir()))

    def test_ack_failure_retains_bundle_for_safe_retry(self):
        self.request(self.deposit())
        self.state.fail_ack_once = True
        with self.assertRaises(APIError):
            self.client.pull_requests(self.root)
        downloads = self.state.downloads
        self.assertEqual(len(self.client.pull_requests(self.root)), 1)
        self.assertEqual(self.state.downloads, downloads)

    def test_local_bundle_edit_cannot_be_silently_reused(self):
        self.request(self.deposit())
        self.state.fail_ack_once = True
        with self.assertRaises(APIError):
            self.client.pull_requests(self.root)
        directory = next(path for path in self.root.iterdir() if path.name.startswith("richiesta-"))
        (directory / "copione.txt").write_text("Edited outside dispatcher")
        with self.assertRaises(ClientError):
            self.client.pull_requests(self.root)
        self.assertEqual(self.state.requests[0]["stato"], "in_attesa")

    def test_delivery_ack_after_new_revision_preserves_source(self):
        first = self.deposit()
        request = self.request(first)
        self.video.write_bytes(b"rerender-video")
        second = self.deposit(request_id=request["id"])
        self.assertEqual(request["nuova_revisione"], second["id"])
        self.assertEqual(request["stato"], "consegnata")
        self.assertEqual(self.state.files[first["file"][0]["id"]], b"synthetic-video-version-one")

    def test_foreign_pagination_and_plaintext_remote_rejected(self):
        self.state.foreign_next = True
        with self.assertRaises(ClientError):
            self.deposit()
        self.assertEqual(len(self.state.productions), 0)
        with self.assertRaises(ClientError):
            FactoryClient("http://example.invalid", "af_synthetic_test_only")
        with self.assertRaises(ClientError):
            self.client._url("https://example.invalid/api/v1/file/x/")

    def test_multipart_refuses_changed_source_and_header_injection(self):
        row = file_fingerprint(self.video)
        multipart = Multipart({}, [("video", row)])
        self.video.write_bytes(b"changed")
        with self.assertRaises(ClientError):
            b"".join(multipart)
        if "\n" not in str(self.root):
            self.assertRaises(ClientError, file_fingerprint, self.root / "not-existing\nfile.mp4")


if __name__ == "__main__":
    unittest.main()
