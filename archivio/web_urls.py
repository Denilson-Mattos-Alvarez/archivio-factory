from django.urls import path
from . import views

app_name = 'archivio'
urlpatterns = [
    path('', views.lista, name='lista'),
    path('nuovo/', views.nuova, name='nuova'),
    path('produzioni/<uuid:produzione_id>/', views.dettaglio, name='dettaglio'),
    path('produzioni/<uuid:produzione_id>/nuova-versione/', views.nuova, name='nuova_revisione'),
    path('revisioni/<uuid:revisione_id>/approva/', views.approva, name='approva'),
    path('revisioni/<uuid:revisione_id>/nota/', views.nota, name='nota'),
    path('revisioni/<uuid:revisione_id>/modifica/', views.modifica, name='modifica'),
    path('revisioni/<uuid:revisione_id>/programma/', views.programma, name='programma'),
    path('calendario/', views.calendario, name='calendario'),
    path('calendario/<uuid:programmazione_id>/pubblicata/', views.pubblicata, name='pubblicata'),
    path('richieste/', views.richieste, name='richieste'),
    path('guida/', views.guida, name='guida'),
]
