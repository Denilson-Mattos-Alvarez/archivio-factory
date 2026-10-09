# Sicurezza e conservazione dei dati

L'archivio richiede accesso autenticato. Ogni produzione appartiene a uno spazio; gli utenti ricevono un ruolo da lettore o revisore in quello spazio. Le chiavi Factory sono limitate a uno spazio e conservate nel database tramite impronta SHA-256, mentre il token è mostrato solo al momento della creazione.

## Controlli applicativi

- Le query di metadati, file, pacchetti e richieste passano dai controlli di spazio del backend.
- Le chiavi della Factory non possono approvare, richiedere modifiche editoriali, programmare o dichiarare pubblicato un video. Queste azioni richiedono il revisore umano.
- I file non sono pubblicati come `/media/`: l'applicazione controlla l'accesso e l'integrità prima di aprirli. Le risposte private indicano `Cache-Control: private, no-store` e variano per cookie/autorizzazione.
- Le sessioni umane richiedono CSRF per le azioni che scrivono dati. Le password sono gestite da Django; non vengono memorizzate in chiaro.
- L'accesso sospende per cinque minuti un indirizzo remoto dopo dieci tentativi falliti. Anche questo contatore usa la cache del processo: più istanze richiedono una cache condivisa o un limite concordato sul proxy. Il backend non considera attendibili header client per ricostruire l'indirizzo.
- Il deposito valida i campi ammessi, i limiti dei file e i nomi; revisioni e file hanno identificatori UUID e percorsi gestiti dal server.
- Il vincolo del riferimento impedisce produzioni duplicate. La chiave di idempotenza impedisce che un retry diventi una nuova revisione e rifiuta riutilizzi con contenuto diverso.
- L'approvazione registra l'hash del manifest della revisione precisa; programmazione e pubblicazione registrata richiedono quell'approvazione e la verifica dei file.
- Il ponte non esegue istruzioni libere, non estrae ZIP remoti e non segue redirect con la chiave.

L'immutabilità riguarda il normale accesso tramite applicazione e modelli: non rende il disco o un amministratore del database tecnicamente incapace di modificare dati. SHA-256 rileva cambiamenti rispetto ai metadati registrati. Un amministratore che controlla sia file sia database può alterare entrambi; una custodia separata dei backup conserva un punto di confronto indipendente.

Il manifest del pacchetto di revisione è firmato dal backend con una chiave Django. Il backup dell'intero archivio ha hash per rilevare corruzione, ma non è cifrato né firmato con una chiave esterna. Chi può riscrivere l'intero ZIP può anche riscriverne il manifest: usare permessi e una custodia dei backup affidabile, eventualmente registrando l'hash dello ZIP in un sistema separato.

## Collaudo

I test verificano controlli per spazio, accessi privati, ruoli, campi non ammessi, approvazione per versione e integrità. I test del ponte usano un server HTTP sintetico locale per la trasmissione multipart, i retry, il recupero della revisione storica e la consegna. I test di backup eseguono copie SQLite reali, compreso un database con WAL, e provano ripristino, corruzione, traversal, link e riferimenti mancanti.

Un test che passa non è una garanzia di assenza di vulnerabilità. La configurazione del server, le dipendenze future, il collegamento reale alla Factory e l'eventuale progetto Supabase richiedono la propria verifica. Le prove svolte sul progetto originale sono distinte dalle prove precedenti su software upstream: nessun esito di Reloops o MediaCMS certifica questo codice.

## Installazione locale e produzione

`avvia_locale.py` usa il server di sviluppo Django su `127.0.0.1:58540`, con statici locali. Non usarlo come server Internet. In produzione configurare un server WSGI/ASGI, HTTPS e host espliciti; la modalità `produzione` abilita cookie sicuri, redirect HTTPS e HSTS. La terminazione TLS del proxy deve essere concordata con il backend, senza considerare attendibili header inviati direttamente da un client. Le dipendenze del server di produzione non sono aggiunte automaticamente dal lanciatore locale.

Tenere `ARCHIVIO_MEDIA_ROOT` fuori da qualunque directory servita pubblicamente dal proxy e non aggiungere alias pubblici per video, allegati o backup. Impostare un limite totale del corpo HTTP e del tempo di upload sul server/proxy: i limiti applicativi per file non sono una quota globale di disco. Sorvegliare lo spazio disponibile. Il rate limit Django predefinito usa la cache del processo e non è un limite condiviso fra più istanze.

Limitare gli accessi al runtime con i permessi del sistema operativo. Su un disco condiviso o sincronizzato verificare anche gli accessi del servizio di sincronizzazione: una cartella esclusa da Git può comunque essere copiata da OneDrive o altri servizi. Proteggere disco e backup secondo la riservatezza dei video.

## Segreti e accessi

Non committare `.env`, `runtime/secret.key`, token Factory, credenziali del database o password. Non inserire chiavi nei workflow n8n esportati: usare il loro archivio credenziali. Non depositare questi file come allegati della produzione.

Per revocare una chiave, disattivare la relativa `ChiaveFactory.attiva` tramite un'operazione amministrativa autorizzata; non esporre un endpoint generico di modifica utenti o ruoli. Creare una nuova chiave per il processo interessato e aggiornare il gestore dei segreti. La chiave Django e i token in chiaro vanno custoditi separatamente dal backup dei dati. Una chiave Django nuova invalida le sessioni e le firme HMAC dei vecchi pacchetti esportati.

Non conservare credenziali predefinite negli esempi o avviare una demo pubblica. L'utente del bootstrap deve scegliere una password forte tramite il prompt interattivo. L'importazione di un allegato non concede il permesso di eseguirlo: chi configura il dispatcher deve validare formati e target e scrivere output separati.

## Segnalazioni

Per una vulnerabilità, fornire versione/commit, ambiente, comportamento atteso e una riproduzione con dati sintetici. Non includere chiavi o video privati. Prima di pubblicare una riproduzione, rimuovere informazioni che consentano accesso a un'installazione reale. L'esistenza dei test di sicurezza non sostituisce una revisione indipendente prima di un'esposizione pubblica.
