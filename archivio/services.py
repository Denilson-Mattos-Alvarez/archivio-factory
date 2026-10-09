import hashlib
import json
import os
import re
import secrets
import tempfile
import uuid
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from .models import (
    AccessoSpazio, Approvazione, ChiaveFactory, EventoAudit, FileRevisione,
    NotaRevisione, Produzione, Programmazione, Revisione, RichiestaModifica, Spazio,
)


class ConflittoDeposito(ValidationError):
    pass


class ErroreIntegrita(ConflittoDeposito):
    pass


def spazi_accessibili(user, chiave=None):
    if not user or not user.is_authenticated or not user.is_active:
        return Spazio.objects.none()
    if isinstance(chiave, ChiaveFactory):
        # Verifica ancora la revoca: una chiave può essere disabilitata durante una richiesta.
        if chiave.user_id != user.pk or not ChiaveFactory.objects.filter(pk=chiave.pk, attiva=True, user__is_active=True).exists():
            return Spazio.objects.none()
        return Spazio.objects.filter(pk=chiave.spazio_id)
    return Spazio.objects.filter(accessi__user=user)


def _spazio(user, chiave, spazio_id, *, scrittura=False, umano=False):
    try:
        spazio = spazi_accessibili(user, chiave).get(pk=spazio_id)
    except (Spazio.DoesNotExist, ValueError, ValidationError):
        raise Http404("Spazio non trovato.") from None
    if umano and isinstance(chiave, ChiaveFactory):
        raise PermissionDenied("Questa azione richiede un revisore umano.")
    if scrittura and not isinstance(chiave, ChiaveFactory):
        if not AccessoSpazio.objects.filter(user=user, spazio=spazio, ruolo=AccessoSpazio.Ruolo.REVISORE).exists():
            raise PermissionDenied("Il ruolo lettore non consente modifiche.")
    return spazio


def produzioni_accessibili(user, chiave=None):
    return Produzione.objects.filter(spazio__in=spazi_accessibili(user, chiave)).select_related("spazio")


def ottieni_produzione(user, chiave, produzione_id):
    try:
        return produzioni_accessibili(user, chiave).get(pk=produzione_id)
    except (Produzione.DoesNotExist, ValueError, ValidationError):
        raise Http404("Produzione non trovata.") from None


def ottieni_revisione(user, chiave, revisione_id):
    try:
        return Revisione.objects.select_related("produzione__spazio").get(pk=revisione_id, produzione__spazio__in=spazi_accessibili(user, chiave))
    except (Revisione.DoesNotExist, ValueError, ValidationError):
        raise Http404("Revisione non trovata.") from None


def ottieni_file(user, chiave, file_id):
    try:
        return FileRevisione.objects.select_related("revisione__produzione").get(pk=file_id, revisione__produzione__spazio__in=spazi_accessibili(user, chiave))
    except (FileRevisione.DoesNotExist, ValueError, ValidationError):
        raise Http404("File non trovato.") from None


def _testo(valore, nome, limite, *, vuoto=False):
    if not isinstance(valore, str):
        raise ValidationError({nome: "È richiesto un testo."})
    if len(valore) > limite or (not vuoto and not valore.strip()):
        raise ValidationError({nome: f"Inserisci un testo {'non vuoto ' if not vuoto else ''}di massimo {limite} caratteri."})
    if "\x00" in valore:
        raise ValidationError({nome: "Il testo contiene un carattere non valido."})
    try:
        valore.encode("utf-8")
    except UnicodeEncodeError:
        raise ValidationError({nome: "Il testo contiene un carattere Unicode non valido."}) from None
    return valore


def _evento(spazio, user, azione, oggetto, **dettagli):
    return EventoAudit.objects.create(spazio=spazio, autore=user, azione=azione, oggetto_id=oggetto.pk, dettagli=dettagli)


def crea_produzione(user, chiave=None, *, spazio_id, titolo, origine="factory", riferimento_factory=""):
    spazio = _spazio(user, chiave, spazio_id, scrittura=True)
    titolo = _testo(titolo, "titolo", 240).strip()
    riferimento_factory = _testo(riferimento_factory, "riferimento_factory", 240, vuoto=True).strip()
    if origine not in Produzione.Origine.values:
        raise ValidationError({"origine": "Origine non valida."})
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True)
        if riferimento_factory and Produzione.objects.filter(spazio=spazio, origine=origine, riferimento_factory=riferimento_factory).exists():
            raise ConflittoDeposito("Il riferimento Factory è già presente nello spazio.")
        try:
            produzione = Produzione.objects.create(spazio=spazio, titolo=titolo, origine=origine, riferimento_factory=riferimento_factory)
        except IntegrityError:
            raise ConflittoDeposito("Il riferimento Factory è già presente nello spazio.") from None
        _evento(spazio, user, "produzione_creata", produzione, origine=origine)
    return produzione


def radice_privata():
    radice = Path(settings.PRIVATE_MEDIA_ROOT)
    radice.mkdir(parents=True, exist_ok=True)
    if radice.is_symlink():
        raise ValidationError("La directory privata non può essere un collegamento simbolico.")
    return radice.resolve()


def percorso_file(file):
    radice = radice_privata()
    relativo = Path(file.percorso)
    if relativo.is_absolute() or ".." in relativo.parts:
        raise ValidationError("Percorso del file non valido.")
    candidato = radice / relativo
    # Nessuna componente del percorso può rinviare a un file esterno.
    corrente = radice
    for parte in relativo.parts:
        corrente = corrente / parte
        if corrente.is_symlink():
            raise ValidationError("I collegamenti simbolici non sono consentiti.")
    risolto = candidato.resolve()
    if not risolto.is_relative_to(radice) or not risolto.is_file():
        raise Http404("File privato non disponibile.")
    return risolto


def _nome_originale(nome):
    # Il nome è soltanto metadato: il percorso effettivo usa UUID generati qui.
    nome = str(nome or "file").replace("\\", "/").rsplit("/", 1)[-1]
    nome = "".join(c for c in nome if ord(c) >= 32 and ord(c) != 127)
    _testo(nome, "nome_file", 100_000, vuoto=True)
    return (nome[:240] or "file")


def _deposita(upload, revisione_id, ruolo, creati):
    if upload is None or not hasattr(upload, "chunks"):
        raise ValidationError({"video" if ruolo == "video" else "allegati": "È richiesto un file caricato."})
    limite = int(getattr(settings, "ARCHIVIO_MAX_VIDEO_BYTES", 512 * 1024 * 1024) if ruolo == "video" else getattr(settings, "ARCHIVIO_MAX_ALLEGATO_BYTES", 25 * 1024 * 1024))
    if getattr(upload, "size", 0) > limite:
        raise ValidationError("Il file supera la dimensione massima consentita.")
    nome = _nome_originale(upload.name)
    if ruolo == "video" and not nome.lower().endswith(".mp4"):
        raise ValidationError({"video": "Per il video è richiesto un file MP4."})
    file_id = uuid.uuid4()
    relativo = f"revisioni/{revisione_id.hex}/{file_id.hex}.{'mp4' if ruolo == 'video' else 'bin'}"
    radice = radice_privata()
    destinazione = radice / relativo
    if (radice / "revisioni").is_symlink():
        raise ValidationError("Directory del deposito non valida.")
    if not destinazione.parent.resolve().is_relative_to(radice):
        raise ValidationError("Directory del deposito non valida.")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    # La cartella nasce dall'UUID della nuova revisione; non proviene dall'utente.
    if destinazione.parent.is_symlink() or not destinazione.parent.resolve().is_relative_to(radice):
        raise ValidationError("Directory del deposito non valida.")
    digest = hashlib.sha256()
    dimensione = 0
    inizio = b""
    with destinazione.open("xb") as uscita:
        creati.append(destinazione)
        for blocco in upload.chunks():
            dimensione += len(blocco)
            if dimensione > limite:
                raise ValidationError("Il file supera la dimensione massima consentita.")
            if len(inizio) < 64:
                inizio += blocco[:64 - len(inizio)]
            digest.update(blocco)
            uscita.write(blocco)
        uscita.flush()
        os.fsync(uscita.fileno())
    if dimensione == 0:
        raise ValidationError("Non è possibile depositare un file vuoto.")
    if ruolo == "video":
        box_size = int.from_bytes(inizio[:4], "big") if len(inizio) >= 12 else 0
        if len(inizio) < 12 or inizio[4:8] != b"ftyp" or box_size < 12 or box_size > dimensione:
            raise ValidationError({"video": "Il file non ha un'intestazione MP4 riconoscibile."})
    return FileRevisione(id=file_id, revisione_id=revisione_id, ruolo=ruolo, nome_originale=nome, percorso=relativo, sha256=digest.hexdigest(), dimensione=dimensione, mime="video/mp4" if ruolo == "video" else "application/octet-stream")


def manifest_contenuto(produzione_id, copione, nota, file):
    voci = [dict(ruolo=f.ruolo, nome_originale=f.nome_originale, sha256=f.sha256, dimensione=f.dimensione, mime=f.mime) for f in file]
    voci.sort(key=lambda f: (f["ruolo"], f["nome_originale"], f["sha256"]))
    contenuto = dict(formato="archivio-factory/1", produzione_id=str(produzione_id), copione=copione, nota=nota, file=voci)
    return json.dumps(contenuto, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ripulisci_creati(creati):
    # Si rimuovono soltanto i file creati da questa chiamata, mai cartelle ricorsivamente.
    for path in creati:
        path.unlink(missing_ok=True)
    for cartella in {p.parent for p in creati}:
        try:
            cartella.rmdir()
        except OSError:
            pass


def crea_revisione(user, chiave=None, *, produzione_id, video, copione, nota="", allegati=(), idempotency_key=""):
    produzione = ottieni_produzione(user, chiave, produzione_id)
    _spazio(user, chiave, produzione.spazio_id, scrittura=True)
    copione = _testo(copione, "copione", 500_000, vuoto=True)
    nota = _testo(nota, "nota", 10_000, vuoto=True)
    if idempotency_key and (not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", idempotency_key)):
        raise ValidationError("Idempotency-Key non valida.")
    allegati = list(allegati)
    if len(allegati) > int(getattr(settings, "ARCHIVIO_MAX_ALLEGATI", 20)):
        raise ValidationError("Troppi allegati nel deposito.")
    revisione_id = uuid.uuid4()
    creati = []
    conservati = False
    try:
        file = [_deposita(video, revisione_id, "video", creati)]
        file.extend(_deposita(f, revisione_id, "allegato", creati) for f in allegati)
        manifest_hash = hashlib.sha256(manifest_contenuto(produzione.pk, copione, nota, file)).hexdigest()
        with transaction.atomic():
            Spazio.objects.select_for_update().get(pk=produzione.spazio_id)
            produzione = Produzione.objects.select_for_update().get(pk=produzione.pk)
            _spazio(user, chiave, produzione.spazio_id, scrittura=True)
            if idempotency_key:
                esistente = Revisione.objects.filter(produzione=produzione, idempotency_key=idempotency_key).first()
                if esistente:
                    if esistente.manifest_sha256 != manifest_hash:
                        raise ConflittoDeposito("La stessa Idempotency-Key identifica un contenuto diverso.")
                    verifica_revisione(esistente, verifica_file=True)
                    esistente._deposito_ripetuto = True
                    return esistente
            numero = (Revisione.objects.filter(produzione=produzione).aggregate(ultimo=Max("numero"))["ultimo"] or 0) + 1
            try:
                revisione = Revisione.objects.create(id=revisione_id, produzione=produzione, numero=numero, copione=copione, nota=nota, manifest_sha256=manifest_hash, idempotency_key=idempotency_key)
                FileRevisione.objects.bulk_create(file)
            except IntegrityError:
                raise ConflittoDeposito("Deposito concorrente: riprova con la stessa Idempotency-Key.") from None
            _evento(produzione.spazio, user, "revisione_depositata", revisione, numero=numero, manifest_sha256=manifest_hash, numero_file=len(file))
        conservati = True
        return revisione
    finally:
        if not conservati:
            _ripulisci_creati(creati)


def verifica_revisione(revisione, *, verifica_file=False):
    file = list(revisione.file.all())
    if sum(f.ruolo == FileRevisione.Ruolo.VIDEO for f in file) != 1:
        raise ErroreIntegrita("La revisione deve contenere esattamente un video.")
    manifest = manifest_contenuto(revisione.produzione_id, revisione.copione, revisione.nota, file)
    if not secrets.compare_digest(hashlib.sha256(manifest).hexdigest(), revisione.manifest_sha256):
        raise ErroreIntegrita("Il manifest della revisione non supera la verifica di integrità.")
    if verifica_file:
        for f in file:
            with apri_file_verificato(f):
                pass
    return manifest


def apri_file_verificato(file):
    try:
        path = percorso_file(file)
        stream = path.open("rb")
    except (OSError, Http404, ValidationError):
        raise ErroreIntegrita("Il file privato non è disponibile o il suo percorso non è valido.") from None
    try:
        digest = hashlib.sha256()
        dimensione = 0
        for blocco in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(blocco)
            dimensione += len(blocco)
        if dimensione != file.dimensione or not secrets.compare_digest(digest.hexdigest(), file.sha256):
            raise ErroreIntegrita("Il file non supera la verifica di integrità.")
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


def approva_revisione(user, *, revisione_id, chiave=None):
    revisione = ottieni_revisione(user, chiave, revisione_id)
    spazio = _spazio(user, chiave, revisione.produzione.spazio_id, scrittura=True, umano=True)
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True, umano=True)
        verifica_revisione(revisione, verifica_file=True)
        approvazione, creata = Approvazione.objects.get_or_create(revisione=revisione, defaults={"revisore": user, "hash_manifest": revisione.manifest_sha256})
        if approvazione.hash_manifest != revisione.manifest_sha256:
            raise ValidationError("L'approvazione non corrisponde alla revisione.")
        if creata:
            _evento(spazio, user, "revisione_approvata", revisione, manifest_sha256=revisione.manifest_sha256)
    return approvazione


def aggiungi_nota(user, chiave=None, *, revisione_id, testo, timecode_seconds=None):
    revisione = ottieni_revisione(user, chiave, revisione_id)
    spazio = _spazio(user, chiave, revisione.produzione.spazio_id, scrittura=True)
    testo = _testo(testo, "testo", 10_000)
    if timecode_seconds is not None:
        try:
            timecode_seconds = Decimal(str(timecode_seconds))
            if not timecode_seconds.is_finite() or not 0 <= timecode_seconds < Decimal("1000000000"):
                raise InvalidOperation
            timecode_seconds = timecode_seconds.quantize(Decimal("0.001"))
        except (InvalidOperation, ValueError):
            raise ValidationError({"timecode_seconds": "Inserisci un tempo non negativo valido."}) from None
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True)
        nota = NotaRevisione.objects.create(revisione=revisione, autore=user, testo=testo, timecode_seconds=timecode_seconds)
        _evento(spazio, user, "nota_aggiunta", nota, revisione_id=str(revisione.pk))
    return nota


def richiedi_modifica(user, *, revisione_id, testo, target, chiave=None):
    revisione = ottieni_revisione(user, chiave, revisione_id)
    spazio = _spazio(user, chiave, revisione.produzione.spazio_id, scrittura=True, umano=True)
    testo = _testo(testo, "testo", 10_000)
    if target not in RichiestaModifica.Target.values:
        raise ValidationError({"target": "Destinazione della modifica non valida."})
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True, umano=True)
        richiesta = RichiestaModifica.objects.create(revisione=revisione, autore=user, testo=testo, target=target)
        _evento(spazio, user, "modifica_richiesta", richiesta, revisione_id=str(revisione.pk), target=target)
    return richiesta


def programma_revisione(user, *, revisione_id, piattaforma, prevista_il, chiave=None):
    revisione = ottieni_revisione(user, chiave, revisione_id)
    spazio = _spazio(user, chiave, revisione.produzione.spazio_id, scrittura=True, umano=True)
    piattaforma = _testo(piattaforma, "piattaforma", 120).strip()
    if not hasattr(prevista_il, "tzinfo") or timezone.is_naive(prevista_il):
        raise ValidationError({"prevista_il": "Indica una data con fuso orario."})
    if prevista_il <= timezone.now():
        raise ValidationError({"prevista_il": "La data prevista deve essere futura."})
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True, umano=True)
        if not Approvazione.objects.filter(revisione=revisione, hash_manifest=revisione.manifest_sha256).exists():
            raise ValidationError("Approva questa revisione prima di programmarla.")
        verifica_revisione(revisione, verifica_file=True)
        programmazione, creata = Programmazione.objects.get_or_create(revisione=revisione, piattaforma=piattaforma, prevista_il=prevista_il)
        if creata:
            _evento(spazio, user, "revisione_programmata", programmazione, revisione_id=str(revisione.pk), piattaforma=piattaforma, prevista_il=prevista_il.isoformat())
    return programmazione


def richieste_accessibili(user, chiave=None):
    return RichiestaModifica.objects.filter(revisione__produzione__spazio__in=spazi_accessibili(user, chiave)).select_related("revisione__produzione", "autore")


def segna_esportata(user, chiave=None, *, richiesta_id):
    try:
        richiesta = richieste_accessibili(user, chiave).get(pk=richiesta_id)
    except (RichiestaModifica.DoesNotExist, ValueError, ValidationError):
        raise Http404("Richiesta non trovata.") from None
    spazio = _spazio(user, chiave, richiesta.revisione.produzione.spazio_id, scrittura=True)
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        richiesta = RichiestaModifica.objects.select_for_update().get(pk=richiesta.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True)
        if richiesta.stato == RichiestaModifica.Stato.IN_ATTESA:
            richiesta.stato = RichiestaModifica.Stato.ESPORTATA
            richiesta.save(update_fields=["stato"])
            _evento(spazio, user, "richiesta_esportata", richiesta)
    return richiesta


def completa_richiesta(user, chiave=None, *, richiesta_id, nuova_revisione_id):
    try:
        richiesta = richieste_accessibili(user, chiave).get(pk=richiesta_id)
    except (RichiestaModifica.DoesNotExist, ValueError, ValidationError):
        raise Http404("Richiesta non trovata.") from None
    spazio = _spazio(user, chiave, richiesta.revisione.produzione.spazio_id, scrittura=True)
    nuova_revisione = ottieni_revisione(user, chiave, nuova_revisione_id)
    if nuova_revisione.produzione_id != richiesta.revisione.produzione_id or nuova_revisione.numero <= richiesta.revisione.numero:
        raise ValidationError("La consegna deve essere una revisione successiva della stessa produzione.")
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        richiesta = RichiestaModifica.objects.select_for_update().get(pk=richiesta.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True)
        if richiesta.stato == RichiestaModifica.Stato.CONSEGNATA:
            if richiesta.nuova_revisione_id != nuova_revisione.pk:
                raise ConflittoDeposito("Questa richiesta è già stata completata con un'altra revisione.")
            verifica_revisione(nuova_revisione, verifica_file=True)
            return richiesta
        verifica_revisione(nuova_revisione, verifica_file=True)
        richiesta.stato = RichiestaModifica.Stato.CONSEGNATA
        richiesta.nuova_revisione = nuova_revisione
        richiesta.save(update_fields=["stato", "nuova_revisione"])
        _evento(spazio, user, "richiesta_consegnata", richiesta, nuova_revisione_id=str(nuova_revisione.pk), manifest_sha256=nuova_revisione.manifest_sha256)
    return richiesta


def segna_pubblicata(user, *, programmazione_id, chiave=None):
    try:
        programmazione = Programmazione.objects.select_related("revisione__produzione").get(pk=programmazione_id, revisione__produzione__spazio__in=spazi_accessibili(user, chiave))
    except (Programmazione.DoesNotExist, ValueError, ValidationError):
        raise Http404("Programmazione non trovata.") from None
    spazio = _spazio(user, chiave, programmazione.revisione.produzione.spazio_id, scrittura=True, umano=True)
    with transaction.atomic():
        Spazio.objects.select_for_update().get(pk=spazio.pk)
        programmazione = Programmazione.objects.select_for_update().select_related("revisione").get(pk=programmazione.pk)
        _spazio(user, chiave, spazio.pk, scrittura=True, umano=True)
        revisione = programmazione.revisione
        if not Approvazione.objects.filter(revisione=revisione, hash_manifest=revisione.manifest_sha256).exists():
            raise ValidationError("L'approvazione della revisione non è valida.")
        verifica_revisione(revisione, verifica_file=True)
        if programmazione.stato != Programmazione.Stato.PUBBLICATO:
            programmazione.stato = Programmazione.Stato.PUBBLICATO
            programmazione.save(update_fields=["stato"])
            _evento(spazio, user, "pubblicazione_registrata", programmazione, revisione_id=str(revisione.pk), piattaforma=programmazione.piattaforma)
    return programmazione


def crea_pacchetto(user, chiave=None, *, revisione_id):
    revisione = ottieni_revisione(user, chiave, revisione_id)
    file = list(revisione.file.all())
    totale = sum(f.dimensione for f in file) + len(revisione.copione.encode("utf-8")) * 2 + len(revisione.nota.encode("utf-8")) + 100_000
    if totale > int(getattr(settings, "ARCHIVIO_MAX_PACCHETTO_BYTES", 1024 * 1024 * 1024)):
        raise ValidationError("Il pacchetto supera la dimensione massima consentita.")
    manifest = verifica_revisione(revisione)
    contenitore = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b")
    try:
        with zipfile.ZipFile(contenitore, "w", compression=zipfile.ZIP_STORED) as pacchetto:
            pacchetto.writestr("manifest.json", manifest)
            pacchetto.writestr("manifest.sha256", revisione.manifest_sha256 + "\n")
            pacchetto.writestr("manifest.firma", signing.Signer(salt="archivio-factory.manifest.v1").sign(revisione.manifest_sha256))
            pacchetto.writestr("revisione.json", json.dumps({"id": str(revisione.pk), "produzione_id": str(revisione.produzione_id), "numero": revisione.numero, "manifest_sha256": revisione.manifest_sha256}, ensure_ascii=False))
            pacchetto.writestr("copione.txt", revisione.copione.encode("utf-8"))
            for f in file:
                nome = f"{f.ruolo}/{f.id.hex}.{'mp4' if f.ruolo == 'video' else 'bin'}"
                digest = hashlib.sha256()
                dimensione = 0
                with apri_file_verificato(f) as origine, pacchetto.open(nome, "w", force_zip64=True) as uscita:
                    for blocco in iter(lambda: origine.read(1024 * 1024), b""):
                        dimensione += len(blocco)
                        if dimensione > f.dimensione:
                            raise ErroreIntegrita("La dimensione di un file è cambiata dopo il deposito.")
                        digest.update(blocco)
                        uscita.write(blocco)
                if dimensione != f.dimensione or not secrets.compare_digest(digest.hexdigest(), f.sha256):
                    raise ErroreIntegrita("Il file non supera la verifica del pacchetto.")
        contenitore.seek(0)
        return revisione, contenitore
    except Exception:
        contenitore.close()
        raise


def genera_chiave(spazio, nome):
    nome = _testo(nome, "nome", 120).strip()
    token = "af_" + secrets.token_urlsafe(32)
    with transaction.atomic():
        user = get_user_model().objects.create(username="factory_" + uuid.uuid4().hex, is_active=True, is_staff=False, is_superuser=False)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        chiave = ChiaveFactory.objects.create(spazio=spazio, nome=nome, user=user, impronta_sha256=hashlib.sha256(token.encode("ascii")).hexdigest())
        _evento(spazio, None, "chiave_factory_creata", chiave, nome=nome)
    return chiave, token
