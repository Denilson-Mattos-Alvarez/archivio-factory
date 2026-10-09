import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class QuerySetImmutabile(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Le revisioni e i loro file sono immutabili.")

    def delete(self):
        raise ValidationError("Le revisioni e i loro file non possono essere eliminati.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Le revisioni e i loro file sono immutabili.")

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False, update_fields=None, unique_fields=None):
        if update_conflicts or update_fields:
            raise ValidationError("Le revisioni e i loro file sono immutabili.")
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts, update_conflicts=False, update_fields=update_fields, unique_fields=unique_fields)


class ModelloImmutabile(models.Model):
    objects = QuerySetImmutabile.as_manager()

    class Meta:
        abstract = True

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self._state.adding or force_update or update_fields is not None:
            raise ValidationError("La revisione depositata è immutabile: crea una nuova versione.")
        # Un oggetto costruito con un UUID esistente deve fallire in INSERT,
        # senza che Django trasformi implicitamente save() in un UPDATE.
        return super().save(force_insert=True, using=using)

    def delete(self, *args, **kwargs):
        raise ValidationError("Il materiale depositato non può essere eliminato.")


class Spazio(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(max_length=120)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Spazi"

    def __str__(self):
        return self.nome


class AccessoSpazio(models.Model):
    class Ruolo(models.TextChoices):
        REVISORE = "revisore", "Revisore"
        LETTORE = "lettore", "Lettore"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    spazio = models.ForeignKey(Spazio, related_name="accessi", on_delete=models.CASCADE)
    ruolo = models.CharField(max_length=12, choices=Ruolo.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "spazio"], name="accesso_unico_spazio")]
        verbose_name_plural = "Accessi agli spazi"


class ChiaveFactory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spazio = models.ForeignKey(Spazio, related_name="chiavi", on_delete=models.PROTECT)
    nome = models.CharField(max_length=120)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    impronta_sha256 = models.CharField(max_length=64, unique=True, editable=False)
    attiva = models.BooleanField(default=True)
    creata_il = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nome} ({self.spazio})"


class Produzione(models.Model):
    class Origine(models.TextChoices):
        KAAPI = "kaapi", "Kaapi"
        FACTORY = "factory", "Factory"
        ALTRO = "altro", "Altro"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spazio = models.ForeignKey(Spazio, related_name="produzioni", on_delete=models.PROTECT)
    titolo = models.CharField(max_length=240)
    origine = models.CharField(max_length=12, choices=Origine.choices, default=Origine.FACTORY)
    riferimento_factory = models.CharField(max_length=240, blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creata_il", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["spazio", "origine", "riferimento_factory"],
                condition=~models.Q(riferimento_factory=""),
                name="riferimento_factory_unico",
            )
        ]

    def __str__(self):
        return self.titolo


class Revisione(ModelloImmutabile):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    produzione = models.ForeignKey(Produzione, related_name="revisioni", on_delete=models.PROTECT)
    numero = models.PositiveIntegerField()
    copione = models.TextField(blank=True)
    nota = models.TextField(blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)
    manifest_sha256 = models.CharField(max_length=64, editable=False)
    idempotency_key = models.CharField(max_length=200, blank=True, editable=False)

    class Meta:
        ordering = ["-numero"]
        constraints = [
            models.UniqueConstraint(fields=["produzione", "numero"], name="numero_revisione_unico"),
            models.CheckConstraint(condition=models.Q(numero__gte=1), name="numero_revisione_positivo"),
            models.UniqueConstraint(fields=["produzione", "idempotency_key"], condition=~models.Q(idempotency_key=""), name="idempotenza_revisione_unica"),
        ]

    def __str__(self):
        return f"{self.produzione} · versione {self.numero}"


class FileRevisione(ModelloImmutabile):
    class Ruolo(models.TextChoices):
        VIDEO = "video", "Video"
        ALLEGATO = "allegato", "Allegato"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revisione = models.ForeignKey(Revisione, related_name="file", on_delete=models.PROTECT)
    ruolo = models.CharField(max_length=12, choices=Ruolo.choices)
    nome_originale = models.CharField(max_length=240)
    percorso = models.CharField(max_length=200, unique=True)
    sha256 = models.CharField(max_length=64)
    dimensione = models.PositiveBigIntegerField()
    mime = models.CharField(max_length=100)

    class Meta:
        ordering = ["ruolo", "id"]
        constraints = [
            models.UniqueConstraint(fields=["revisione"], condition=models.Q(ruolo="video"), name="video_unico_revisione")
        ]


class Approvazione(models.Model):
    revisione = models.OneToOneField(Revisione, related_name="approvazione", on_delete=models.PROTECT)
    revisore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    hash_manifest = models.CharField(max_length=64)
    approvata_il = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.revisione_id and self.hash_manifest != self.revisione.manifest_sha256:
            raise ValidationError("L'approvazione deve riferirsi al manifest esatto della revisione.")

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self._state.adding or force_update or update_fields is not None:
            raise ValidationError("L'approvazione già registrata non può essere modificata.")
        self.clean()
        return super().save(force_insert=True, using=using)


class NotaRevisione(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revisione = models.ForeignKey(Revisione, related_name="note", on_delete=models.PROTECT)
    autore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    testo = models.TextField()
    timecode_seconds = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creata_il", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(timecode_seconds__isnull=True) | models.Q(timecode_seconds__gte=0), name="timecode_non_negativo")]


class RichiestaModifica(models.Model):
    class Target(models.TextChoices):
        COPIONE = "copione", "Copione"
        MONTAGGIO = "montaggio", "Montaggio"
        VOCE = "voce", "Voce"
        SOTTOTITOLI = "sottotitoli", "Sottotitoli"

    class Stato(models.TextChoices):
        IN_ATTESA = "in_attesa", "In attesa"
        ESPORTATA = "esportata", "Esportata"
        CONSEGNATA = "consegnata", "Consegnata"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revisione = models.ForeignKey(Revisione, related_name="richieste", on_delete=models.PROTECT)
    autore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    testo = models.TextField()
    target = models.CharField(max_length=16, choices=Target.choices)
    stato = models.CharField(max_length=16, choices=Stato.choices, default=Stato.IN_ATTESA)
    nuova_revisione = models.ForeignKey(Revisione, related_name="richieste_origine", on_delete=models.PROTECT, null=True, blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creata_il", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(stato="consegnata", nuova_revisione__isnull=False) | models.Q(stato__in=["in_attesa", "esportata"], nuova_revisione__isnull=True)),
                name="consegna_richiesta_coerente",
            )
        ]


class Programmazione(models.Model):
    class Stato(models.TextChoices):
        PROGRAMMATO = "programmato", "Programmato"
        PUBBLICATO = "pubblicato", "Pubblicato"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revisione = models.ForeignKey(Revisione, related_name="programmazioni", on_delete=models.PROTECT)
    piattaforma = models.CharField(max_length=120)
    prevista_il = models.DateTimeField()
    stato = models.CharField(max_length=16, choices=Stato.choices, default=Stato.PROGRAMMATO)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prevista_il", "id"]
        constraints = [models.UniqueConstraint(fields=["revisione", "piattaforma", "prevista_il"], name="programmazione_unica")]


class EventoAudit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spazio = models.ForeignKey(Spazio, related_name="eventi", on_delete=models.PROTECT)
    autore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    azione = models.CharField(max_length=80)
    oggetto_id = models.UUIDField()
    dettagli = models.JSONField(default=dict)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creata_il", "id"]
