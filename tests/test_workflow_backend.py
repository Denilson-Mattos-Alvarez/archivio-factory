"""Consegne e registrazioni editoriali: permessi, integrità e retry sicuri."""
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from archivio import services
from archivio.models import AccessoSpazio, Approvazione, EventoAudit, Revisione, Spazio


MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"video-sintetico-workflow"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkflowBackendTests(TestCase):
    def setUp(self):
        cache.clear()
        # La radice runtime è configurabile anche fuori dal checkout (ad esempio
        # runner.temp in CI). Ogni test possiede soltanto questa nuova sottocartella.
        scratch = Path(settings.RUNTIME_ROOT).resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="test-workflow-", dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        User = get_user_model()
        self.a = User.objects.create_user("revisore_workflow_a", password="password-sintetica")
        self.b = User.objects.create_user("revisore_workflow_b", password="password-sintetica")
        self.lettore = User.objects.create_user("lettore_workflow", password="password-sintetica")
        self.sa = Spazio.objects.create(nome="Spazio A")
        self.sb = Spazio.objects.create(nome="Spazio B")
        AccessoSpazio.objects.create(user=self.a, spazio=self.sa, ruolo="revisore")
        AccessoSpazio.objects.create(user=self.b, spazio=self.sb, ruolo="revisore")
        AccessoSpazio.objects.create(user=self.lettore, spazio=self.sa, ruolo="lettore")
        self.pa = services.crea_produzione(self.a, spazio_id=self.sa.id, titolo="Produzione A")
        self.pb = services.crea_produzione(self.b, spazio_id=self.sb.id, titolo="Produzione B")
        self.altra = services.crea_produzione(self.a, spazio_id=self.sa.id, titolo="Altra produzione A")
        self.r1 = self.revisione(self.a, self.pa, "Prima versione")
        self.r2 = self.revisione(self.a, self.pa, "Seconda versione")
        self.rb = self.revisione(self.b, self.pb, "Video B")
        self.raltra = self.revisione(self.a, self.altra, "Video diverso")
        self.richiesta = services.richiedi_modifica(self.a, revisione_id=self.r1.id, testo="Rivedi il montaggio", target="montaggio")
        self.chiave, self.token = services.genera_chiave(self.sa, "Factory test workflow")

    def revisione(self, user, produzione, testo):
        return services.crea_revisione(user, produzione_id=produzione.id, video=SimpleUploadedFile("video.mp4", MP4, content_type="video/mp4"), copione=testo)

    def umano(self, user=None):
        client = APIClient()
        if user:
            client.force_authenticate(user=user)
        return client

    def factory(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)
        return client

    def completa(self, client, revisione=None):
        return client.post(f"/api/v1/factory/richieste/{self.richiesta.id}/completa/", {"nuova_revisione_id": str((revisione or self.r2).id)}, format="json")

    def programmazione(self):
        services.approva_revisione(self.a, revisione_id=self.r1.id)
        return services.programma_revisione(self.a, revisione_id=self.r1.id, piattaforma="YouTube", prevista_il=timezone.now() + timedelta(days=2))

    def pubblicata(self, client, programmazione):
        return client.post(f"/api/v1/programmazioni/{programmazione.id}/pubblicata/", {}, format="json")

    def test_consegna_factory_idempotente_non_approva_nuova_versione(self):
        client = self.factory()
        risposta = self.completa(client)
        self.assertEqual(risposta.status_code, 200)
        self.assertEqual(risposta.data["stato"], "consegnata")
        self.assertEqual(str(risposta.data["nuova_revisione"]), str(self.r2.id))
        self.assertEqual(self.completa(client).status_code, 200)
        self.assertFalse(Approvazione.objects.filter(revisione=self.r2).exists())
        self.assertEqual(EventoAudit.objects.filter(azione="richiesta_consegnata", oggetto_id=self.richiesta.id).count(), 1)
        # Un ack tardivo della presa in carico non deve annullare la consegna.
        ack = client.post(f"/api/v1/factory/richieste/{self.richiesta.id}/esportata/", {}, format="json")
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.data["stato"], "consegnata")

    def test_consegna_rifiuta_produzione_diversa_e_versione_non_successiva(self):
        client = self.factory()
        self.assertEqual(self.completa(client, self.raltra).status_code, 400)
        self.assertEqual(self.completa(client, self.r1).status_code, 400)
        self.assertEqual(self.completa(client, self.rb).status_code, 404)
        self.richiesta.refresh_from_db()
        self.assertEqual(self.richiesta.stato, "in_attesa")
        self.assertIsNone(self.richiesta.nuova_revisione_id)

    def test_consegna_gia_registrata_non_puo_cambiare_riferimento(self):
        client = self.factory()
        self.assertEqual(self.completa(client).status_code, 200)
        r3 = self.revisione(self.a, self.pa, "Terza versione")
        self.assertEqual(self.completa(client, r3).status_code, 409)
        self.richiesta.refresh_from_db()
        self.assertEqual(self.richiesta.nuova_revisione_id, self.r2.id)

    def test_consegna_richiede_accesso_scrittura_e_chiave_attiva(self):
        self.assertEqual(self.completa(self.umano()).status_code, 401)
        self.assertEqual(self.completa(self.umano(self.lettore)).status_code, 403)
        self.assertEqual(self.completa(self.umano(self.b)).status_code, 404)
        self.chiave.attiva = False
        self.chiave.save(update_fields=["attiva"])
        self.assertEqual(self.completa(self.factory()).status_code, 401)
        self.assertEqual(self.completa(self.umano(self.a)).status_code, 200)

    def test_consegna_integrita_fallita_non_cambia_stato(self):
        services.percorso_file(self.r2.file.get(ruolo="video")).write_bytes(b"contenuto alterato")
        self.assertEqual(self.completa(self.factory()).status_code, 409)
        self.richiesta.refresh_from_db()
        self.assertEqual(self.richiesta.stato, "in_attesa")
        self.assertFalse(EventoAudit.objects.filter(azione="richiesta_consegnata", oggetto_id=self.richiesta.id).exists())

    def test_pubblicazione_manual_human_e_idempotente(self):
        programmazione = self.programmazione()
        self.assertEqual(self.pubblicata(self.factory(), programmazione).status_code, 403)
        self.assertEqual(self.pubblicata(self.umano(self.lettore), programmazione).status_code, 403)
        self.assertEqual(self.pubblicata(self.umano(self.b), programmazione).status_code, 404)
        self.assertEqual(self.pubblicata(self.umano(), programmazione).status_code, 401)
        human = self.umano(self.a)
        self.assertEqual(self.pubblicata(human, programmazione).status_code, 200)
        self.assertEqual(self.pubblicata(human, programmazione).status_code, 200)
        programmazione.refresh_from_db()
        self.assertEqual(programmazione.stato, "pubblicato")
        self.assertEqual(EventoAudit.objects.filter(azione="pubblicazione_registrata", oggetto_id=programmazione.id).count(), 1)

    def test_pubblicazione_rifiuta_approvazione_di_hash_diverso(self):
        programmazione = self.programmazione()
        Approvazione.objects.filter(revisione=self.r1).update(hash_manifest="0" * 64)
        self.assertEqual(self.pubblicata(self.umano(self.a), programmazione).status_code, 400)
        programmazione.refresh_from_db()
        self.assertEqual(programmazione.stato, "programmato")

    def test_pubblicazione_integrita_fallita_non_cambia_stato(self):
        programmazione = self.programmazione()
        services.percorso_file(self.r1.file.get(ruolo="video")).write_bytes(b"contenuto alterato")
        self.assertEqual(self.pubblicata(self.umano(self.a), programmazione).status_code, 409)
        programmazione.refresh_from_db()
        self.assertEqual(programmazione.stato, "programmato")
        self.assertFalse(EventoAudit.objects.filter(azione="pubblicazione_registrata", oggetto_id=programmazione.id).exists())

    def test_sessione_senza_csrf_non_puo_registrare_pubblicazione(self):
        programmazione = self.programmazione()
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.a)
        self.assertEqual(self.pubblicata(client, programmazione).status_code, 403)

    def test_completamento_rifiuta_campi_aggiuntivi(self):
        risposta = self.factory().post(f"/api/v1/factory/richieste/{self.richiesta.id}/completa/", {"nuova_revisione_id": str(self.r2.id), "stato": "consegnata"}, format="json")
        self.assertEqual(risposta.status_code, 400)

    def test_header_autorizzazione_vuoto_di_spazi_non_causa_errore_server(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="   ")
        self.assertEqual(client.get("/api/v1/produzioni/").status_code, 401)

    def test_replay_deposito_non_nasconde_file_gia_manomesso(self):
        revisione = services.crea_revisione(self.a, produzione_id=self.pa.id, video=SimpleUploadedFile("video.mp4", MP4), copione="Versione idempotente", idempotency_key="test-integrita-replay")
        services.percorso_file(revisione.file.get(ruolo="video")).write_bytes(b"video alterato")
        risposta = self.factory().post(f"/api/v1/produzioni/{self.pa.id}/revisioni/", {"video": SimpleUploadedFile("video.mp4", MP4), "copione": "Versione idempotente"}, format="multipart", HTTP_IDEMPOTENCY_KEY="test-integrita-replay")
        self.assertEqual(risposta.status_code, 409)
        self.assertEqual(self.pa.revisioni.count(), 3)

    def test_unicode_non_valido_nel_copione_viene_rifiutato(self):
        with self.assertRaises(ValidationError):
            services.crea_revisione(self.a, produzione_id=self.pa.id, video=SimpleUploadedFile("video.mp4", MP4), copione="Testo\ud800")
        self.assertEqual(self.pa.revisioni.count(), 2)

    def test_uuid_ricostruito_non_sovrascrive_revisione_immutabile(self):
        contraffatta = Revisione(id=self.r1.id, produzione=self.pa, numero=self.r1.numero, copione="Sovrascrittura", manifest_sha256="0" * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            contraffatta.save()
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.copione, "Prima versione")
        with self.assertRaises(ValidationError):
            Revisione.objects.bulk_create([contraffatta], update_conflicts=True, update_fields=["copione"], unique_fields=["id"])
