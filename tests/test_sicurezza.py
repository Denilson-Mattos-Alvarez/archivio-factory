"""Prove di isolamento, conservazione e workflow; solo utenti/file sintetici."""
import hashlib
import io
import json
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signals import request_finished
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from archivio import services
from archivio.models import AccessoSpazio, Approvazione, ChiaveFactory, FileRevisione, Produzione, Revisione, Spazio

MP4_1 = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2' + b'video-sintetico-uno'
MP4_2 = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2' + b'video-sintetico-due'


def upload(data=MP4_1, name='video.mp4'):
    return SimpleUploadedFile(name, data, content_type='video/mp4')


def bytes_response(response):
    return b''.join(response.streaming_content) if response.streaming else response.content


class TrackedAPIClient(APIClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = []

    def request(self, **request):
        response = super().request(**request)
        self.responses.append(response)
        return response


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SicurezzaArchivioTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix='archivio-factory-test-')
        self.addCleanup(self.tmp.cleanup)
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.tmp.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.clients = []
        self.addCleanup(self._chiudi_risposte)
        User = get_user_model()
        self.a = User.objects.create_user('persona_a', password='password-sintetica-A-2026')
        self.b = User.objects.create_user('persona_b', password='password-sintetica-B-2026')
        self.lettore = User.objects.create_user('lettore', password='password-sintetica-L-2026')
        self.sa = Spazio.objects.create(nome='Spazio A')
        self.sb = Spazio.objects.create(nome='Spazio B')
        AccessoSpazio.objects.create(user=self.a, spazio=self.sa, ruolo='revisore')
        AccessoSpazio.objects.create(user=self.b, spazio=self.sb, ruolo='revisore')
        AccessoSpazio.objects.create(user=self.lettore, spazio=self.sa, ruolo='lettore')
        self.pa = services.crea_produzione(self.a, spazio_id=self.sa.id, titolo='Kaapi sintetico', origine='kaapi', riferimento_factory='kaapi-test')
        self.pb = services.crea_produzione(self.b, spazio_id=self.sb.id, titolo='Factory sintetica', riferimento_factory='factory-test')
        self.ra = services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(), copione='Copione A')
        self.rb = services.crea_revisione(self.b, produzione_id=self.pb.id, video=upload(MP4_2), copione='Copione B')
        self.fa = self.ra.file.get(ruolo='video')
        self.fb = self.rb.file.get(ruolo='video')
        key_result = services.genera_chiave(self.sa, 'Factory sintetica')
        if isinstance(key_result[0], ChiaveFactory):
            self.chiave, self.token = key_result
        else:
            self.token, self.chiave = key_result
        self.client = self._client()

    def _chiudi_risposte(self):
        # Il teardown deve liberare anche gli stream parziali, senza inviare
        # request_finished dentro l'atomic di TestCase. Il wrapper streaming
        # Django può ricollegare i receiver mentre response.close() è in corso.
        with patch.object(request_finished, 'send', return_value=[]):
            for client in self.clients:
                for response in client.responses:
                    response.close()

    def _client(self, csrf=False):
        client = TrackedAPIClient(enforce_csrf_checks=csrf)
        self.clients.append(client)
        return client

    def umano(self, user=None, csrf=False):
        client = self._client(csrf=csrf)
        if user:
            client.force_authenticate(user=user)
        return client

    def factory(self, token=None):
        client = self._client()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + (token or self.token))
        return client

    def test_anonimo_non_legge_archivio_o_file(self):
        for path in ['/api/v1/produzioni/', f'/api/v1/revisioni/{self.ra.id}/', f'/api/v1/file/{self.fa.id}/']:
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_json_non_oggetto_rifiutato_senza_errore_server(self):
        client = self.umano(self.a)
        paths = ['/api/v1/produzioni/', f'/api/v1/revisioni/{self.ra.id}/note/',
                 f'/api/v1/revisioni/{self.ra.id}/modifiche/', f'/api/v1/revisioni/{self.ra.id}/programma/']
        for path in paths:
            for payload in ('[]', '"testo"', '42', 'null'):
                with self.subTest(path=path, payload=payload):
                    self.assertEqual(client.generic('POST', path, payload, content_type='application/json').status_code, 400)

    def test_isolamento_lista_e_oggetti_con_id_noto(self):
        client = self.umano(self.a)
        lista = client.get('/api/v1/produzioni/').json()['results']
        self.assertEqual([x['id'] for x in lista], [str(self.pa.id)])
        for path in [f'/api/v1/produzioni/{self.pb.id}/', f'/api/v1/revisioni/{self.rb.id}/',
                     f'/api/v1/file/{self.fb.id}/', f'/api/v1/revisioni/{self.rb.id}/pacchetto/']:
            self.assertEqual(client.get(path).status_code, 404)

    def test_chiave_factory_ha_solo_il_proprio_spazio(self):
        client = self.factory()
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 200)
        self.assertEqual(client.get(f'/api/v1/file/{self.fb.id}/').status_code, 404)
        risposta = client.post('/api/v1/produzioni/', {'spazio_id': str(self.sb.id), 'titolo': 'Intrusione'}, format='json')
        self.assertEqual(risposta.status_code, 404)

    def test_chiave_factory_non_approva_o_programma(self):
        client = self.factory()
        self.assertEqual(client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {}, format='json').status_code, 403)
        risposta = client.post(f'/api/v1/revisioni/{self.ra.id}/programma/',
            {'piattaforma': 'YouTube', 'prevista_il': (timezone.now()+timedelta(days=2)).isoformat()}, format='json')
        self.assertEqual(risposta.status_code, 403)
        self.assertFalse(Approvazione.objects.filter(revisione=self.ra).exists())

    def test_chiave_factory_revocata_subito(self):
        client = self.factory()
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 200)
        ChiaveFactory.objects.filter(pk=self.chiave.id).update(attiva=False)
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 401)

    def test_cred_quando_account_factory_disattivato(self):
        self.chiave.user.is_active = False
        self.chiave.user.save(update_fields=['is_active'])
        self.assertEqual(self.factory().get('/api/v1/produzioni/').status_code, 401)

    def test_token_non_conservato_o_esposto(self):
        self.assertEqual(self.chiave.impronta_sha256, hashlib.sha256(self.token.encode()).hexdigest())
        self.assertNotIn(self.token, str(ChiaveFactory.objects.values().first()))
        self.assertNotIn(self.token, self.factory().get('/api/v1/produzioni/').content.decode())
        self.assertFalse(self.chiave.user.has_usable_password())

    def test_lettore_legge_ma_non_deposita_o_approva(self):
        client = self.umano(self.lettore)
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 200)
        self.assertEqual(client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {}).status_code, 403)
        self.assertEqual(client.post(f'/api/v1/produzioni/{self.pa.id}/revisioni/',
            {'video': upload(), 'copione': 'Nuovo'}, format='multipart').status_code, 403)

    def test_revoca_appartenenza_blocca_file_gia_aperto(self):
        client = self.umano(self.a)
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 200)
        AccessoSpazio.objects.filter(user=self.a, spazio=self.sa).delete()
        self.assertEqual(client.get(f'/api/v1/file/{self.fa.id}/').status_code, 404)

    def test_campi_di_ruolo_e_approvazione_non_accettati(self):
        client = self.factory()
        self.assertEqual(client.post('/api/v1/produzioni/', {'titolo': 'Test', 'is_superuser': True}, format='json').status_code, 400)
        self.assertEqual(client.post(f'/api/v1/produzioni/{self.pa.id}/revisioni/',
            {'video': upload(), 'copione': 'A', 'approvata': 'true'}, format='multipart').status_code, 400)

    def test_patch_e_delete_non_disponibili(self):
        client = self.umano(self.a)
        for path in [f'/api/v1/revisioni/{self.ra.id}/', f'/api/v1/produzioni/{self.pa.id}/']:
            self.assertEqual(client.patch(path, {'copione': 'Sovrascrittura'}, format='json').status_code, 405)
            self.assertEqual(client.delete(path).status_code, 405)
        self.ra.refresh_from_db()
        self.assertEqual(self.ra.copione, 'Copione A')

    def test_due_revisioni_conservate_e_nuova_non_approvata(self):
        services.approva_revisione(self.a, revisione_id=self.ra.id)
        seconda = services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(MP4_2), copione='Copione modificato')
        self.assertEqual(seconda.numero, 2)
        self.assertFalse(Approvazione.objects.filter(revisione=seconda).exists())
        self.assertEqual(bytes_response(self.umano(self.a).get(f'/api/v1/file/{self.fa.id}/')), MP4_1)
        file2 = seconda.file.get(ruolo='video')
        self.assertEqual(bytes_response(self.umano(self.a).get(f'/api/v1/file/{file2.id}/')), MP4_2)
        self.assertEqual(Approvazione.objects.get(revisione=self.ra).hash_manifest, self.ra.manifest_sha256)

    def test_idempotenza_non_crea_duplicati(self):
        client = self.factory()
        url = f'/api/v1/produzioni/{self.pa.id}/revisioni/'
        a = client.post(url, {'video': upload(MP4_2), 'copione': 'Copione'}, format='multipart', HTTP_IDEMPOTENCY_KEY='deposito-test-1')
        b = client.post(url, {'video': upload(MP4_2), 'copione': 'Copione'}, format='multipart', HTTP_IDEMPOTENCY_KEY='deposito-test-1')
        self.assertEqual(a.status_code, 201)
        self.assertEqual(b.status_code, 200)
        self.assertEqual(a.json()['id'], b.json()['id'])
        self.assertEqual(self.pa.revisioni.count(), 2)

    def test_idempotenza_con_payload_diverso_conflitto(self):
        client = self.factory()
        url = f'/api/v1/produzioni/{self.pa.id}/revisioni/'
        self.assertEqual(client.post(url, {'video': upload(), 'copione': 'A'}, format='multipart', HTTP_IDEMPOTENCY_KEY='conflitto-1').status_code, 201)
        risposta = client.post(url, {'video': upload(MP4_2), 'copione': 'B'}, format='multipart', HTTP_IDEMPOTENCY_KEY='conflitto-1')
        self.assertEqual(risposta.status_code, 409)
        self.assertEqual(self.pa.revisioni.count(), 2)

    def test_file_attivi_html_scaricati_non_eseguiti(self):
        revisione = services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(), copione='Copione',
            allegati=[SimpleUploadedFile('pagina.html', b'<script>alert(1)</script>', content_type='text/html')])
        allegato = revisione.file.get(ruolo='allegato')
        risposta = self.umano(self.a).get(f'/api/v1/file/{allegato.id}/')
        self.assertEqual(risposta.status_code, 200)
        self.assertTrue(risposta['Content-Disposition'].startswith('attachment'))
        self.assertEqual(risposta['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(risposta['Content-Type'], 'application/octet-stream')

    def test_video_finto_e_upload_troppo_grande_rifiutati(self):
        client = self.factory()
        url = f'/api/v1/produzioni/{self.pa.id}/revisioni/'
        self.assertEqual(client.post(url, {'video': upload(b'<html>malevolo</html>'), 'copione': 'A'}, format='multipart').status_code, 400)
        with override_settings(ARCHIVIO_MAX_VIDEO_BYTES=10):
            self.assertEqual(client.post(url, {'video': upload(), 'copione': 'A'}, format='multipart').status_code, 400)
        self.assertEqual(self.pa.revisioni.count(), 1)

    def test_manomissione_file_rilevata(self):
        services.percorso_file(self.fa).write_bytes(b'contenuto-manomesso')
        client = self.umano(self.a)
        for path in [f'/api/v1/file/{self.fa.id}/', f'/api/v1/revisioni/{self.ra.id}/pacchetto/']:
            self.assertEqual(client.get(path).status_code, 409)
        self.assertEqual(client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {}).status_code, 409)

    def test_revisione_e_file_immutabili_anche_orm(self):
        self.ra.copione = 'Modifica nascosta'
        with self.assertRaises(ValidationError):
            self.ra.save()
        with self.assertRaises(ValidationError):
            Revisione.objects.filter(pk=self.ra.id).update(copione='B')
        with self.assertRaises(ValidationError):
            self.fa.delete()

    def test_calendario_solo_versione_approvata_con_data_futura(self):
        client = self.umano(self.a)
        url = f'/api/v1/revisioni/{self.ra.id}/programma/'
        futuro = (timezone.now()+timedelta(days=3)).isoformat()
        self.assertEqual(client.post(url, {'piattaforma': 'YouTube', 'prevista_il': futuro}, format='json').status_code, 400)
        self.assertEqual(client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {}).status_code, 200)
        self.assertEqual(client.post(url, {'piattaforma': 'YouTube', 'prevista_il': futuro}, format='json').status_code, 201)
        self.assertEqual(client.post(url, {'piattaforma': 'YouTube', 'prevista_il': '2030-03-01T12:00:00'}, format='json').status_code, 400)
        self.assertEqual(client.post(url, {'piattaforma': 'YouTube', 'prevista_il': (timezone.now()-timedelta(days=1)).isoformat()}, format='json').status_code, 400)

    def test_note_temporizzate_e_richieste_legate_versione(self):
        nota = services.aggiungi_nota(self.a, revisione_id=self.ra.id, testo='Rivedere introduzione', timecode_seconds=1.5)
        richiesta = services.richiedi_modifica(self.a, revisione_id=self.ra.id, testo='Accorciare il montaggio', target='montaggio')
        self.assertEqual(nota.revisione_id, self.ra.id)
        self.assertEqual(richiesta.revisione_id, self.ra.id)
        self.assertEqual(self.factory().get('/api/v1/factory/richieste/').json()['results'][0]['id'], str(richiesta.id))
        self.assertEqual(self.umano(self.b).get('/api/v1/factory/richieste/').json()['results'], [])

    def test_zip_contiene_copione_e_originali_verificabili(self):
        risposta = self.factory().get(f'/api/v1/revisioni/{self.ra.id}/pacchetto/')
        self.assertEqual(risposta.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(bytes_response(risposta))) as z:
            self.assertIn('manifest.json', z.namelist())
            self.assertFalse(any('..' in name.split('/') for name in z.namelist()))
            blobs = [z.read(name) for name in z.namelist() if name.endswith('.mp4')]
            self.assertEqual(blobs, [MP4_1])

    def test_file_privati_non_pubblicati_da_url_statico(self):
        self.assertEqual(self.client.get('/media/' + self.fa.percorso).status_code, 404)
        risposta = self.factory().get(f'/api/v1/file/{self.fa.id}/')
        self.assertIn('no-store', risposta['Cache-Control'])
        self.assertIn('Authorization', risposta['Vary'])

    def test_sessione_senza_csrf_non_puo_approvare(self):
        client = self._client(csrf=True)
        client.force_login(self.a)
        risposta = client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {})
        self.assertEqual(risposta.status_code, 403)

    def test_cookie_umano_e_chiave_factory_non_sommano_privilegi(self):
        client = self._client(csrf=True)
        client.force_login(self.b)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(client.get(f'/api/v1/revisioni/{self.rb.id}/').status_code, 404)
        self.assertEqual(client.get(f'/api/v1/revisioni/{self.ra.id}/').status_code, 200)
        self.assertEqual(client.post(f'/api/v1/revisioni/{self.ra.id}/approva/', {}).status_code, 403)

    def test_copione_html_escapato_nell_interfaccia(self):
        revisione = services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(), copione='<script>alert(1)</script>')
        self.client.force_login(self.a)
        risposta = self.client.get(f'/produzioni/{self.pa.id}/?revisione={revisione.id}')
        self.assertEqual(risposta.status_code, 200)
        self.assertNotContains(risposta, '<script>alert(1)</script>')
        self.assertContains(risposta, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_ricerca_ritrova_copione_di_versione_precedente(self):
        services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(), copione='Introduzione ' * 30 + 'parolaanticaunica')
        services.crea_revisione(self.a, produzione_id=self.pa.id, video=upload(MP4_2), copione='Copione attuale completamente diverso')
        self.client.force_login(self.a)
        risposta = self.client.get('/', {'q': 'parolaanticaunica'})
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, self.pa.titolo)
        self.assertNotContains(risposta, self.pb.titolo)

    def test_tentativi_accesso_limitati_e_nessuna_registrazione_pubblica(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/accesso/', {'username': 'persona_a', 'password': 'errata'}).status_code, 200)
        risposta = self.client.post('/accesso/', {'username': 'persona_a', 'password': 'errata'})
        self.assertEqual(risposta.status_code, 429)
        self.assertEqual(risposta['Retry-After'], '300')
        self.assertEqual(self.client.post('/registrazione/', {}).status_code, 404)

    def test_range_video_verificato_e_privato(self):
        path = f'/api/v1/file/{self.fa.id}/'
        client = self.factory()
        risposta = client.get(path, HTTP_RANGE='bytes=4-11')
        self.assertEqual(risposta.status_code, 206)
        self.assertEqual(bytes_response(risposta), MP4_1[4:12])
        self.assertEqual(risposta['Content-Range'], f'bytes 4-11/{len(MP4_1)}')
        self.assertEqual(risposta['Accept-Ranges'], 'bytes')
        self.assertEqual(self.umano(self.b).get(path, HTTP_RANGE='bytes=4-11').status_code, 404)
        self.assertEqual(self.client.get(path, HTTP_RANGE='bytes=4-11').status_code, 401)
        suffix = client.get(path, HTTP_RANGE='bytes=-5')
        self.assertEqual(bytes_response(suffix), MP4_1[-5:])
        for invalid in ['bytes=100000-', 'bytes=-0', 'bytes=5-1', 'bytes=0-1,4-5', 'nonsense']:
            self.assertEqual(client.get(path, HTTP_RANGE=invalid).status_code, 416)
        mismatch = client.get(path, HTTP_RANGE='bytes=4-11', HTTP_IF_RANGE='"hash-diverso"')
        self.assertEqual(mismatch.status_code, 200)
        self.assertEqual(bytes_response(mismatch), MP4_1)
