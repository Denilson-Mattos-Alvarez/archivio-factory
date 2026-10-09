from django.urls import path

from . import api

app_name = "archivio_api"
urlpatterns = [
    path("produzioni/", api.ProduzioniVista.as_view(), name="produzioni"),
    path("produzioni/<uuid:produzione_id>/", api.ProduzioneVista.as_view(), name="produzione"),
    path("produzioni/<uuid:produzione_id>/revisioni/", api.DepositoVista.as_view(), name="deposito"),
    path("revisioni/<uuid:revisione_id>/", api.RevisioneVista.as_view(), name="revisione"),
    path("revisioni/<uuid:revisione_id>/approva/", api.ApprovazioneVista.as_view(), name="approva"),
    path("revisioni/<uuid:revisione_id>/note/", api.NoteVista.as_view(), name="note"),
    path("revisioni/<uuid:revisione_id>/modifiche/", api.ModificheVista.as_view(), name="modifiche"),
    path("revisioni/<uuid:revisione_id>/programma/", api.ProgrammaVista.as_view(), name="programma"),
    path("revisioni/<uuid:revisione_id>/pacchetto/", api.PacchettoVista.as_view(), name="pacchetto"),
    path("file/<uuid:file_id>/", api.FileVista.as_view(), name="file"),
    path("factory/richieste/", api.RichiesteVista.as_view(), name="richieste"),
    path("factory/richieste/<uuid:richiesta_id>/esportata/", api.EsportataVista.as_view(), name="esportata"),
    path("factory/richieste/<uuid:richiesta_id>/completa/", api.CompletaVista.as_view(), name="completa"),
    path("programmazioni/<uuid:programmazione_id>/pubblicata/", api.PubblicataVista.as_view(), name="pubblicata"),
]
