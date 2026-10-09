"""Prove reali di concorrenza, eseguite soltanto con PostgreSQL isolato."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from unittest import skipUnless
from archivio import services
from archivio.models import AccessoSpazio, Revisione, Spazio

DATA = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2' + b'prova-concorrente'


@skipUnless(connection.vendor == 'postgresql', 'Richiede il laboratorio PostgreSQL separato.')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DepositiConcorrentiTests(TransactionTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='archivio-postgres-concorrenza-')
        self.addCleanup(self.tmp.cleanup)
        self.config = override_settings(PRIVATE_MEDIA_ROOT=Path(self.tmp.name))
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.user = get_user_model().objects.create_user('revisore_concorrenza', password='solo-test')
        space = Spazio.objects.create(nome='Solo concorrenza sintetica')
        AccessoSpazio.objects.create(user=self.user, spazio=space, ruolo='revisore')
        self.production = services.crea_produzione(self.user, spazio_id=space.id, titolo='Deposito concorrente')

    def deposit(self, number, same_key=False):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=self.user.pk)
            revision = services.crea_revisione(user, produzione_id=self.production.id,
                video=SimpleUploadedFile('concorrenza.mp4', DATA),
                copione='Stesso contenuto' if same_key else f'Copione {number}',
                idempotency_key='chiave-condivisa-test' if same_key else '')
            return str(revision.id), revision.numero
        finally:
            # Le connessioni sono locali al worker. CONN_MAX_AGE può essere 60:
            # close_old_connections() lascerebbe sessioni sane aperte al teardown.
            connections.close_all()

    def test_depositi_paralleli_non_perdono_revisioni(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(self.deposit, range(8)))
        self.assertEqual(len({value[0] for value in values}), 8)
        self.assertEqual(sorted(value[1] for value in values), list(range(1, 9)))
        for revision in Revisione.objects.filter(produzione=self.production):
            services.verifica_revisione(revision, verifica_file=True)

    def test_retry_paralleli_creano_una_sola_revisione(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(lambda number: self.deposit(number, True), range(8)))
        self.assertEqual(len({value[0] for value in values}), 1)
        self.assertEqual(self.production.revisioni.count(), 1)
        self.assertEqual(self.production.revisioni.first().file.count(), 1)
        self.assertEqual(len(list(Path(self.tmp.name).rglob('*.mp4'))), 1)
