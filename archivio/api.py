from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from django.db import OperationalError
import re
from django.http import FileResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from django.utils.cache import patch_vary_headers
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .authentication import FactoryAuthentication, SessioneConCsrf
from .models import ChiaveFactory
from . import services
from .serializers import (
    ApprovazioneSerializer, CompletaInput, ModificaInput, NotaInput, NotaSerializer,
    ProduzioneInput, ProduzioneSerializer, ProgrammaInput,
    ProgrammazioneSerializer, RevisioneInput, RevisioneSerializer, RichiestaSerializer,
)


def gestisci_errore(exc, context):
    if isinstance(exc, services.ConflittoDeposito):
        return Response({"errore": exc.messages}, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, DjangoValidationError):
        return Response({"errore": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, DjangoPermissionDenied):
        return Response({"errore": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, OperationalError) and "locked" in str(exc).lower():
        return Response({"errore": "Un altro deposito è in corso. Riprova con la stessa Idempotency-Key."}, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Retry-After": "2"})
    return exception_handler(exc, context)


class PaginazioneArchivio(PageNumberPagination):
    page_size = 25
    page_size_query_param = "dimensione_pagina"
    max_page_size = 100


class VistaArchivio(APIView):
    authentication_classes = [SessioneConCsrf, FactoryAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def get_authenticate_header(self, request):
        return 'Bearer realm="Archivio Factory"'

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        patch_vary_headers(response, ["Cookie", "Authorization"])
        return response

    def lista(self, query, serializer):
        paginazione = PaginazioneArchivio()
        pagina = paginazione.paginate_queryset(query, self.request, view=self)
        return paginazione.get_paginated_response(serializer(pagina, many=True).data)


class ProduzioniVista(VistaArchivio):
    def get(self, request):
        query = services.produzioni_accessibili(request.user, request.auth).prefetch_related("revisioni__approvazione")
        for campo in ["riferimento_factory", "origine", "spazio_id"]:
            if campo in request.query_params:
                valore = request.query_params[campo]
                if campo == "spazio_id":
                    valore = ProduzioneInput().fields["spazio_id"].run_validation(valore)
                query = query.filter(**{campo: valore})
        return self.lista(query, ProduzioneSerializer)

    def post(self, request):
        serializer = ProduzioneInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        dati = serializer.validated_data
        if "spazio_id" not in dati:
            if isinstance(request.auth, ChiaveFactory):
                dati["spazio_id"] = request.auth.spazio_id
            else:
                return Response({"errore": "Indica lo spazio di destinazione."}, status=400)
        produzione = services.crea_produzione(request.user, request.auth, **dati)
        return Response(ProduzioneSerializer(produzione).data, status=201)


class ProduzioneVista(VistaArchivio):
    def get(self, request, produzione_id):
        produzione = services.ottieni_produzione(request.user, request.auth, produzione_id)
        return Response(ProduzioneSerializer(produzione).data)


class DepositoVista(VistaArchivio):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, produzione_id):
        # QueryDict conserva gli allegati multipli sotto lo stesso nome.
        dati = {k: request.data[k] for k in request.data if k != "allegati"}
        if "allegati" in request.FILES:
            dati["allegati"] = request.FILES.getlist("allegati")
        serializer = RevisioneInput(data=dati)
        serializer.is_valid(raise_exception=True)
        revisione = services.crea_revisione(request.user, request.auth, produzione_id=produzione_id, idempotency_key=request.headers.get("Idempotency-Key", ""), **serializer.validated_data)
        ripetuto = getattr(revisione, "_deposito_ripetuto", False)
        return Response(RevisioneSerializer(revisione).data, status=200 if ripetuto else 201, headers={"Idempotency-Replayed": "true" if ripetuto else "false"})


class RevisioneVista(VistaArchivio):
    def get(self, request, revisione_id):
        revisione = services.ottieni_revisione(request.user, request.auth, revisione_id)
        return Response(RevisioneSerializer(revisione).data)


class ApprovazioneVista(VistaArchivio):
    def post(self, request, revisione_id):
        approvazione = services.approva_revisione(request.user, revisione_id=revisione_id, chiave=request.auth)
        return Response(ApprovazioneSerializer(approvazione).data, status=200)


class NoteVista(VistaArchivio):
    def post(self, request, revisione_id):
        serializer = NotaInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        nota = services.aggiungi_nota(request.user, request.auth, revisione_id=revisione_id, **serializer.validated_data)
        return Response(NotaSerializer(nota).data, status=201)


class ModificheVista(VistaArchivio):
    def post(self, request, revisione_id):
        serializer = ModificaInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        richiesta = services.richiedi_modifica(request.user, revisione_id=revisione_id, chiave=request.auth, **serializer.validated_data)
        return Response(RichiestaSerializer(richiesta).data, status=201)


class ProgrammaVista(VistaArchivio):
    def post(self, request, revisione_id):
        serializer = ProgrammaInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        programmazione = services.programma_revisione(request.user, revisione_id=revisione_id, chiave=request.auth, **serializer.validated_data)
        return Response(ProgrammazioneSerializer(programmazione).data, status=201)


class FileVista(VistaArchivio):
    def get(self, request, file_id):
        file = services.ottieni_file(request.user, request.auth, file_id)
        stream = services.apri_file_verificato(file)
        video = file.ruolo == "video" and file.mime == "video/mp4" and file.nome_originale.lower().endswith(".mp4")
        range_header = request.headers.get('Range', '')
        etag = '"' + file.sha256 + '"'
        if range_header and request.headers.get('If-Range', etag) == etag:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            valid = bool(match and (match[1] or match[2]))
            start, end = 0, file.dimensione - 1
            if valid:
                try:
                    if match[1]:
                        start = int(match[1])
                        end = min(int(match[2]), end) if match[2] else end
                    else:
                        suffix = int(match[2])
                        start = max(0, file.dimensione - suffix)
                        valid = suffix > 0
                    valid = valid and start <= end and start < file.dimensione
                except ValueError:
                    valid = False
            if not valid:
                stream.close()
                return Response({'errore': 'Intervallo del file non disponibile.'}, status=416,
                                headers={'Content-Range': f'bytes */{file.dimensione}'})
            stream.seek(start)
            response = StreamingHttpResponse(IntervalloFile(stream, end - start + 1), status=206,
                content_type='video/mp4' if video else 'application/octet-stream')
            response['Content-Range'] = f'bytes {start}-{end}/{file.dimensione}'
            response['Content-Length'] = str(end - start + 1)
            response['Content-Disposition'] = content_disposition_header(not video, file.nome_originale)
        else:
            response = FileResponse(stream, as_attachment=not video, filename=file.nome_originale, content_type="video/mp4" if video else "application/octet-stream")
        response['Accept-Ranges'] = 'bytes'
        response['ETag'] = etag
        return response


class IntervalloFile:
    """Streaming limitato al range autorizzato; chiusura anche se il client interrompe."""
    def __init__(self, stream, length):
        self.stream, self.remaining = stream, length

    def __iter__(self):
        return self

    def __next__(self):
        if self.remaining <= 0:
            self.close()
            raise StopIteration
        data = self.stream.read(min(self.remaining, 64 * 1024))
        if not data:
            self.close()
            raise StopIteration
        self.remaining -= len(data)
        return data

    def close(self):
        self.stream.close()


class PacchettoVista(VistaArchivio):
    def get(self, request, revisione_id):
        revisione, stream = services.crea_pacchetto(request.user, request.auth, revisione_id=revisione_id)
        return FileResponse(stream, as_attachment=True, filename=f"revisione-{revisione.pk}.zip", content_type="application/zip")


class RichiesteVista(VistaArchivio):
    def get(self, request):
        query = services.richieste_accessibili(request.user, request.auth)
        if "stato" in request.query_params:
            query = query.filter(stato=request.query_params["stato"])
        return self.lista(query, RichiestaSerializer)


class EsportataVista(VistaArchivio):
    def post(self, request, richiesta_id):
        richiesta = services.segna_esportata(request.user, request.auth, richiesta_id=richiesta_id)
        return Response(RichiestaSerializer(richiesta).data)


class CompletaVista(VistaArchivio):
    def post(self, request, richiesta_id):
        serializer = CompletaInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        richiesta = services.completa_richiesta(request.user, request.auth, richiesta_id=richiesta_id, **serializer.validated_data)
        return Response(RichiestaSerializer(richiesta).data)


class PubblicataVista(VistaArchivio):
    def post(self, request, programmazione_id):
        programmazione = services.segna_pubblicata(request.user, programmazione_id=programmazione_id, chiave=request.auth)
        return Response(ProgrammazioneSerializer(programmazione).data)
