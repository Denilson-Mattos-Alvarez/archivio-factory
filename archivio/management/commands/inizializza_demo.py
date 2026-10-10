"""Dati sintetici opzionali: nessun collegamento con la Factory reale."""
import base64
import hashlib
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from archivio import services
from archivio.models import AccessoSpazio, Spazio


class Command(BaseCommand):
    help = 'Crea una dimostrazione privata locale, con credenziali nuove salvate nel runtime.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='demo')

    def handle(self, *args, **options):
        if settings.ARCHIVIO_AMBIENTE != 'sviluppo' or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('La demo è consentita soltanto nel runtime SQLite locale.')
        username = options['username']
        User = get_user_model()
        if not username or len(username) > 80 or not all(c.isalnum() or c in '-_' for c in username):
            raise CommandError('Nome utente demo non valido.')
        if User.objects.filter(username=username).exists():
            raise CommandError('Utente già presente: la demo non modifica account o dati esistenti.')
        credential = settings.RUNTIME_ROOT / 'accesso-demo.json'
        if credential.exists():
            raise CommandError('Credenziali demo già presenti; nessun dato è stato modificato.')
        videos = json.loads((settings.BASE_DIR / 'config/demo_videos.json').read_text())['video']
        data = []
        for video in videos:
            content = base64.b64decode(video['base64'], validate=True)
            if hashlib.sha256(content).hexdigest() != video['sha256']:
                raise CommandError('Video dimostrativo non integro.')
            data.append(content)
        password = secrets.token_urlsafe(24)
        files_created = []
        try:
            with transaction.atomic():
                user = User.objects.create_user(username, email='demo@example.invalid', password=password, first_name='Dimostrazione')
                space = Spazio.objects.create(nome='Il mio archivio')
                AccessoSpazio.objects.create(spazio=space, user=user, ruolo='revisore')

                def revision(production, index, script, note):
                    item = services.crea_revisione(user, produzione_id=production.id,
                        video=SimpleUploadedFile('dimostrazione.mp4', data[index], content_type='video/mp4'),
                        copione=script, nota=note)
                    files_created.extend(services.percorso_file(file) for file in item.file.all())
                    return item

                kaapi = services.crea_produzione(user, spazio_id=space.id, titolo='Una storia da raccontare', origine='kaapi', riferimento_factory='demo-episodio-001')
                first = revision(kaapi, 0, 'Ogni video racconta un passaggio del nostro lavoro. Conserviamo il copione, i materiali e la prima versione per poterli rivedere.', 'Prima idea del video. Esempio sintetico.')
                services.approva_revisione(user, revisione_id=first.id)
                second = revision(kaapi, 1, 'Ogni versione merita il suo posto. Quando cambiamo un video, conserviamo quello precedente e rileggiamo il copione insieme al nuovo montaggio.', 'Nuovo copione e nuovo montaggio: questa versione riparte da rivedere.')
                services.aggiungi_nota(user, revisione_id=second.id, testo='Accorciare l’introduzione e lasciare più spazio al messaggio finale.', timecode_seconds=1.5)
                services.richiedi_modifica(user, revisione_id=second.id, testo='Rivedere il ritmo dell’introduzione mantenendo la versione precedente.', target='montaggio')

                factory = services.crea_produzione(user, spazio_id=space.id, titolo='La guida del prossimo episodio', origine='factory', riferimento_factory='demo-episodio-002')
                approved = revision(factory, 0, 'Il video e il copione sono pronti per una revisione. Rileggiamo il testo, controlliamo il montaggio e scegliamo quando pubblicare questa versione sui social.', 'Versione approvata, pronta per il calendario.')
                services.approva_revisione(user, revisione_id=approved.id)
                future = timezone.localtime(timezone.now()).replace(hour=16, minute=30, second=0, microsecond=0) + timedelta(days=4)
                services.programma_revisione(user, revisione_id=approved.id, piattaforma='YouTube', prevista_il=future)

                other = services.crea_produzione(user, spazio_id=space.id, titolo='Appunti per la prossima uscita', origine='altro', riferimento_factory='demo-altro-001')
                ready = revision(other, 1, 'Prepariamo la prossima uscita: raccogliamo video, copione e materiali, confrontiamo le versioni e annotiamo le ultime modifiche prima della pubblicazione.', 'Materiale dimostrativo caricato per la prossima uscita.')
                services.approva_revisione(user, revisione_id=ready.id)
        except Exception:
            # Soltanto file nuovi di questa demo; i dati preesistenti non sono toccati.
            for file in files_created:
                file.unlink(missing_ok=True)
            raise
        access = {'url': 'http://127.0.0.1:58540', 'username': username, 'password': password,
                  'spazio_id': str(space.id), 'solo_dimostrazione': True}
        try:
            descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(access, output, ensure_ascii=False, indent=2)
        except OSError as error:
            raise CommandError('Demo creata ma scrittura credenziali non riuscita; reimpostare la password con changepassword.') from error
        self.stdout.write(f'Demo creata: 3 produzioni, 4 revisioni.\nCredenziali locali: {credential}\nNessuna password stampata e nessuna Factory esterna chiamata.')
