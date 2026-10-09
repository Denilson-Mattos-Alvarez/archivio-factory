from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from archivio.models import AccessoSpazio, Spazio


class Command(BaseCommand):
    help = "Crea uno spazio privato e assegna un utente esistente come revisore."

    def add_arguments(self, parser):
        parser.add_argument("nome")
        parser.add_argument("username")

    def handle(self, *args, **options):
        nome = options["nome"].strip()
        if not nome or len(nome) > 120:
            raise CommandError("Il nome deve contenere da 1 a 120 caratteri.")
        try:
            user = get_user_model().objects.get(username=options["username"], is_active=True)
        except get_user_model().DoesNotExist:
            raise CommandError("Utente attivo non trovato. Crealo prima con createsuperuser.") from None
        if not user.has_usable_password():
            raise CommandError("Lo spazio deve essere assegnato a un utente umano, con password utilizzabile.")
        with transaction.atomic():
            spazio = Spazio.objects.create(nome=nome)
            AccessoSpazio.objects.create(spazio=spazio, user=user, ruolo=AccessoSpazio.Ruolo.REVISORE)
        self.stdout.write(f"Spazio creato: {spazio.id}\nRevisore: {user.username}")
