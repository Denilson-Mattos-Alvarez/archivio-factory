from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from archivio.models import Spazio
from archivio.services import genera_chiave


class Command(BaseCommand):
    help = "Genera una chiave Factory limitata a uno spazio. Il token è mostrato una sola volta."

    def add_arguments(self, parser):
        parser.add_argument("spazio_id")
        parser.add_argument("nome")

    def handle(self, *args, **options):
        try:
            spazio = Spazio.objects.get(pk=options["spazio_id"])
            chiave, token = genera_chiave(spazio, options["nome"])
        except (Spazio.DoesNotExist, ValueError, ValidationError) as exc:
            raise CommandError("Spazio o nome della chiave non valido.") from exc
        self.stderr.write(f"Chiave {chiave.id}: conserva il token in un gestore di segreti; non inserirlo nel repository.")
        self.stdout.write(token)
