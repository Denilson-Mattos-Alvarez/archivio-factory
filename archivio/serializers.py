from collections.abc import Mapping
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import serializers

from .models import Approvazione, FileRevisione, NotaRevisione, Produzione, Programmazione, Revisione, RichiestaModifica


class FileSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = FileRevisione
        fields = ["id", "ruolo", "nome_originale", "sha256", "dimensione", "mime", "url"]
        read_only_fields = fields

    def get_url(self, obj):
        return f"/api/v1/file/{obj.pk}/"


class ApprovazioneSerializer(serializers.ModelSerializer):
    revisore = serializers.CharField(source="revisore.username")

    class Meta:
        model = Approvazione
        fields = ["revisore", "hash_manifest", "approvata_il"]
        read_only_fields = fields


class NotaSerializer(serializers.ModelSerializer):
    autore = serializers.CharField(source="autore.username")

    class Meta:
        model = NotaRevisione
        fields = ["id", "testo", "timecode_seconds", "autore", "creata_il"]
        read_only_fields = fields


class RichiestaSerializer(serializers.ModelSerializer):
    autore = serializers.CharField(source="autore.username")
    produzione = serializers.UUIDField(source="revisione.produzione_id")
    titolo_produzione = serializers.CharField(source="revisione.produzione.titolo")
    origine = serializers.CharField(source="revisione.produzione.origine")
    riferimento_factory = serializers.CharField(source="revisione.produzione.riferimento_factory")
    manifest_sha256 = serializers.CharField(source="revisione.manifest_sha256")

    class Meta:
        model = RichiestaModifica
        fields = ["id", "revisione", "produzione", "titolo_produzione", "origine", "riferimento_factory", "manifest_sha256", "testo", "target", "stato", "nuova_revisione", "autore", "creata_il"]
        read_only_fields = fields


class ProgrammazioneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Programmazione
        fields = ["id", "piattaforma", "prevista_il", "stato", "creata_il"]
        read_only_fields = fields


class RevisioneBreveSerializer(serializers.ModelSerializer):
    approvazione = ApprovazioneSerializer(read_only=True)

    class Meta:
        model = Revisione
        fields = ["id", "numero", "nota", "creata_il", "manifest_sha256", "approvazione"]
        read_only_fields = fields


class RevisioneSerializer(serializers.ModelSerializer):
    file = FileSerializer(many=True, read_only=True)
    approvazione = ApprovazioneSerializer(read_only=True)
    note = NotaSerializer(many=True, read_only=True)
    richieste = RichiestaSerializer(many=True, read_only=True)
    programmazioni = ProgrammazioneSerializer(many=True, read_only=True)
    pacchetto_url = serializers.SerializerMethodField()

    class Meta:
        model = Revisione
        fields = ["id", "produzione", "numero", "copione", "nota", "creata_il", "manifest_sha256", "idempotency_key", "file", "approvazione", "note", "richieste", "programmazioni", "pacchetto_url"]
        read_only_fields = fields

    def get_pacchetto_url(self, obj):
        return f"/api/v1/revisioni/{obj.pk}/pacchetto/"


class ProduzioneSerializer(serializers.ModelSerializer):
    revisioni = RevisioneBreveSerializer(many=True, read_only=True)
    spazio_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Produzione
        fields = ["id", "spazio_id", "titolo", "origine", "riferimento_factory", "creata_il", "revisioni"]
        read_only_fields = fields


class InputRigoroso(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)
        sconosciuti = set(data.keys()) - set(self.fields)
        if sconosciuti:
            raise serializers.ValidationError({"campi": "La richiesta contiene campi non ammessi."})
        return super().to_internal_value(data)


class ProduzioneInput(InputRigoroso):
    spazio_id = serializers.UUIDField(required=False)
    titolo = serializers.CharField(max_length=240)
    origine = serializers.ChoiceField(choices=Produzione.Origine.choices, default="factory")
    riferimento_factory = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class RevisioneInput(InputRigoroso):
    copione = serializers.CharField(max_length=500_000, allow_blank=True, trim_whitespace=False)
    nota = serializers.CharField(max_length=10_000, allow_blank=True, trim_whitespace=False, default="")
    video = serializers.FileField()
    allegati = serializers.ListField(child=serializers.FileField(), required=False, default=list)


class NotaInput(InputRigoroso):
    testo = serializers.CharField(max_length=10_000)
    timecode_seconds = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0, required=False, allow_null=True)


class ModificaInput(InputRigoroso):
    testo = serializers.CharField(max_length=10_000)
    target = serializers.ChoiceField(choices=RichiestaModifica.Target.choices)


class DataConFuso(serializers.DateTimeField):
    def to_internal_value(self, value):
        if not isinstance(value, str):
            raise serializers.ValidationError("Indica una data ISO 8601 con fuso orario.")
        try:
            data = parse_datetime(value)
        except ValueError:
            data = None
        if data is None or timezone.is_naive(data):
            raise serializers.ValidationError("Indica una data ISO 8601 con fuso orario.")
        return data


class ProgrammaInput(InputRigoroso):
    piattaforma = serializers.CharField(max_length=120)
    prevista_il = DataConFuso()


class CompletaInput(InputRigoroso):
    nuova_revisione_id = serializers.UUIDField()
