"""Interfaccia italiana: ogni azione usa gli stessi permessi dei servizi API."""
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import services
from .models import AccessoSpazio, Approvazione, Produzione, Programmazione


def _contesto(request, **extra):
    spazi = services.spazi_accessibili(request.user)
    return {'spazi': spazi, 'puo_creare': AccessoSpazio.objects.filter(user=request.user, ruolo='revisore').exists(), **extra}


def _revisore(user, spazio_id):
    return AccessoSpazio.objects.filter(user=user, spazio_id=spazio_id, ruolo='revisore').exists()


def _approvazione(revisione):
    return Approvazione.objects.filter(revisione=revisione, hash_manifest=revisione.manifest_sha256).first()


def _scheda(produzione):
    versione = produzione.revisioni.first()
    stato = 'da_rivedere'
    if versione and _approvazione(versione):
        stato = 'programmato' if versione.programmazioni.exists() else 'approvato'
    return {'id': produzione.id, 'titolo': produzione.titolo, 'origine': produzione.origine,
            'origine_label': produzione.get_origine_display(), 'stato': stato,
            'stato_label': {'da_rivedere': 'Da rivedere', 'approvato': 'Approvato', 'programmato': 'Programmato'}[stato],
            'ultima_revisione': versione, 'numero_revisioni': produzione.revisioni.count(),
            'descrizione_preview': (versione.copione[:140] if versione else 'In attesa del primo video.')}


@login_required
def lista(request):
    raccolta = services.produzioni_accessibili(request.user).select_related('spazio').prefetch_related('revisioni')
    schede = [_scheda(p) for p in raccolta]
    conteggi = {'totale': len(schede), **{key: sum(x['stato'] == key for x in schede) for key in ['da_rivedere', 'approvati', 'programmati']}}
    conteggi['approvati'] = sum(x['stato'] == 'approvato' for x in schede)
    conteggi['programmati'] = sum(x['stato'] == 'programmato' for x in schede)
    q = request.GET.get('q', '')[:240].strip()
    origine = request.GET.get('origine', '')
    stato = request.GET.get('stato', '')
    if q:
        trovate = set(raccolta.filter(Q(titolo__icontains=q) | Q(revisioni__copione__icontains=q)).values_list('id', flat=True))
        schede = [p for p in schede if p['id'] in trovate]
    if origine in Produzione.Origine.values:
        schede = [p for p in schede if p['origine'] == origine]
    if stato in {'da_rivedere', 'approvato', 'programmato'}:
        schede = [p for p in schede if p['stato'] == stato]
    return render(request, 'archivio/lista.html', _contesto(request, produzioni=schede, conteggi=conteggi, q=q, origine=origine, stato=stato))


@login_required
def dettaglio(request, produzione_id):
    produzione = services.ottieni_produzione(request.user, None, produzione_id)
    revisioni = produzione.revisioni.all()
    scelta = request.GET.get('revisione', '')
    if scelta:
        try:
            revisione = revisioni.get(pk=UUID(scelta))
        except (ValueError, produzione.revisioni.model.DoesNotExist):
            raise Http404('Versione non disponibile.')
    else:
        revisione = revisioni.first()
    puo_revisione = _revisore(request.user, produzione.spazio_id)
    if not revisione:
        return render(request, 'archivio/dettaglio.html', _contesto(request, produzione=produzione, revisioni=revisioni, revisione=None, puo_revisione=puo_revisione))
    approvazione = _approvazione(revisione)
    file_video = revisione.file.filter(ruolo='video').first()
    allegati = list(revisione.file.exclude(ruolo='video'))
    for file in allegati:
        file.url = '/api/v1/file/' + str(file.id) + '/'
    return render(request, 'archivio/dettaglio.html', _contesto(request,
        produzione=produzione, revisione=revisione, revisioni=revisioni, file_video=file_video,
        video_url=('/api/v1/file/' + str(file_video.id) + '/') if file_video else '', allegati=allegati,
        puo_revisione=puo_revisione, puo_approvare=puo_revisione and approvazione is None,
        puo_programmare=puo_revisione and approvazione is not None, approvazione=approvazione,
        note=revisione.note.select_related('autore'), richieste_modifica=revisione.richieste.all(),
        programmazioni=revisione.programmazioni.all()))


@login_required
def nuova(request, produzione_id=None):
    produzione = services.ottieni_produzione(request.user, None, produzione_id) if produzione_id else None
    if produzione and not _revisore(request.user, produzione.spazio_id):
        raise PermissionDenied('Il tuo accesso consente la sola lettura.')
    spazi = services.spazi_accessibili(request.user).filter(accessi__user=request.user, accessi__ruolo='revisore').distinct()
    if not spazi.exists():
        raise PermissionDenied('Nessuno spazio abilitato al deposito.')
    errori = []
    if request.method == 'POST':
        try:
            with transaction.atomic():
                if produzione is None:
                    produzione = services.crea_produzione(request.user, spazio_id=request.POST.get('spazio_id'),
                        titolo=request.POST.get('titolo'), origine=request.POST.get('origine', 'factory'),
                        riferimento_factory=request.POST.get('riferimento_factory', ''))
                revisione = services.crea_revisione(request.user, produzione_id=produzione.id,
                    video=request.FILES.get('video'), copione=request.POST.get('copione', ''),
                    nota=request.POST.get('nota', ''), allegati=request.FILES.getlist('allegati'))
            messages.success(request, 'Versione depositata. Il video precedente è conservato; questa versione è da rivedere.')
            return redirect(reverse('archivio:dettaglio', args=[produzione.id]) + '?revisione=' + str(revisione.id))
        except ValidationError as error:
            errori = error.messages
            if produzione_id is None:
                produzione = None
    return render(request, 'archivio/nuova.html', _contesto(request, produzione=produzione, spazi=spazi, errori=errori, dati=request.POST))


def _esegui(request, revisione_id, funzione, messaggio, **dati):
    revisione = services.ottieni_revisione(request.user, None, revisione_id)
    try:
        funzione(request.user, revisione_id=revisione.id, **dati)
        messages.success(request, messaggio)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    return redirect(reverse('archivio:dettaglio', args=[revisione.produzione_id]) + '?revisione=' + str(revisione.id))


@login_required
@require_POST
def approva(request, revisione_id):
    return _esegui(request, revisione_id, services.approva_revisione, 'Approvata questa versione del video e del copione.')


@login_required
@require_POST
def nota(request, revisione_id):
    return _esegui(request, revisione_id, services.aggiungi_nota, 'Nota aggiunta a questa versione.',
                   testo=request.POST.get('testo'), timecode_seconds=request.POST.get('timecode_seconds') or None)


@login_required
@require_POST
def modifica(request, revisione_id):
    return _esegui(request, revisione_id, services.richiedi_modifica,
        'Richiesta salvata. La Factory la acquisirà quando il connettore sarà avviato.',
        testo=request.POST.get('testo'), target=request.POST.get('target'))


@login_required
@require_POST
def programma(request, revisione_id):
    valore = request.POST.get('prevista_il', '')
    try:
        prevista = datetime.fromisoformat(valore)
        if timezone.is_naive(prevista):
            locale = prevista.replace(tzinfo=ZoneInfo('Europe/Rome'))
            # Evita orari inesistenti durante il cambio verso l'ora legale.
            if locale.astimezone(ZoneInfo('UTC')).astimezone(ZoneInfo('Europe/Rome')).replace(tzinfo=None) != prevista:
                raise ValueError('Orario locale inesistente.')
            prevista = locale
    except ValueError:
        revisione = services.ottieni_revisione(request.user, None, revisione_id)
        messages.error(request, 'Inserisci una data e un orario validi nel fuso Europe/Rome.')
        return redirect('archivio:dettaglio', produzione_id=revisione.produzione_id)
    return _esegui(request, revisione_id, services.programma_revisione,
        'Data inserita nel calendario. Dopo l’uscita potrai segnarla come pubblicata.',
        piattaforma=request.POST.get('piattaforma'), prevista_il=prevista)


@login_required
def calendario(request):
    eventi = Programmazione.objects.filter(revisione__produzione__spazio__in=services.spazi_accessibili(request.user)).select_related('revisione__produzione')
    for evento in eventi:
        evento.puo_segnare = evento.stato == 'programmato' and _revisore(request.user, evento.revisione.produzione.spazio_id)
    return render(request, 'archivio/calendario.html', _contesto(request, eventi=eventi))


@login_required
@require_POST
def pubblicata(request, programmazione_id):
    try:
        services.segna_pubblicata(request.user, programmazione_id=programmazione_id)
        messages.success(request, 'Pubblicazione registrata. Il video e la revisione restano conservati.')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    return redirect('archivio:calendario')


@login_required
def richieste(request):
    richieste = services.richieste_accessibili(request.user).select_related('revisione__produzione')
    return render(request, 'archivio/richieste.html', _contesto(request, richieste=richieste))


@login_required
def guida(request):
    return render(request, 'archivio/guida.html', _contesto(request))
