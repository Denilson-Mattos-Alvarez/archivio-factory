"""Deposita un bundle locale dichiarato in JSON. Non chiama la Factory o un modello AI."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client_factory import ClientError, FactoryClient


def local_file(root, value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ClientError("I percorsi nel bundle devono essere relativi e usare /.")
    relative = Path(value)
    if relative.is_absolute() or any(piece in {".", ".."} for piece in value.split("/")):
        raise ClientError("Percorso del bundle non ammesso.")
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
        raise ClientError("File del bundle assente o fuori dalla sua cartella.")
    current = candidate
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ClientError("Link nel bundle non ammesso.")
        current = current.parent
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--url", default=os.getenv("ARCHIVIO_URL", "http://127.0.0.1:58540"))
    args = parser.parse_args()
    try:
        root = args.bundle.resolve().parent
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        expected = {"titolo", "origine", "riferimento_factory", "video", "copione", "nota", "allegati", "richiesta_id"}
        if not isinstance(bundle, dict) or set(bundle) - expected:
            raise ClientError("Campi del bundle non ammessi.")
        video = local_file(root, bundle["video"])
        script = local_file(root, bundle["copione"]).read_bytes().decode("utf-8") if bundle.get("copione") else ""
        attachments = [local_file(root, name) for name in bundle.get("allegati", [])]
        revision = FactoryClient(args.url, os.getenv("ARCHIVIO_FACTORY_API_KEY", "")).deposit(
            title=bundle["titolo"], origin=bundle["origine"], reference=bundle["riferimento_factory"],
            video=video, script=script, note=bundle.get("nota", ""), attachments=attachments,
            request_id=bundle.get("richiesta_id"))
        print(json.dumps({"produzione": revision["produzione"], "revisione": revision["id"], "numero": revision["numero"]}))
    except (ClientError, OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, "Deposito interrotto: " + str(error) + "\n")


if __name__ == "__main__":
    main()
