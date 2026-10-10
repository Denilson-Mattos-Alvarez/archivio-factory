# Collaudo del 10 ottobre 2026

Prima versione originale di Archivio Factory, licenza MIT, Python 3.12. Tutte le prove usano account, token, video e copioni sintetici. Nessun account Supabase, dispatcher della Factory o canale social reale è stato collegato.

| Prova | Risultato verificato |
| --- | --- |
| Suite automatica su Windows/SQLite | 65 test; 63 superati e 2 di concorrenza PostgreSQL saltati |
| Suite su PostgreSQL 16 isolato | 65 test superati, nessun test saltato |
| Concorrenza PostgreSQL | 8 depositi paralleli conservano 8 revisioni; 8 retry identici creano una sola revisione e un solo video |
| Privilegi PostgreSQL | `anon` e `authenticated` non possono usare lo schema privato o leggere le produzioni |
| Browser Edge, desktop e telefono | 28 controlli superati sull'interfaccia generale, nessun errore console o overflow a 1366, 390 e 320 pixel |
| Ponte HTTP verso l'app locale | 8 controlli superati, inclusi deposito multipart, retry, richiesta storica, consegna, blocco delle approvazioni Factory e revoca |
| Recupero dell'archivio SQLite reale di prova | 22 controlli superati: 4 produzioni, 6 revisioni, 8 file e tutte le 54 righe delle 11 tabelle dell'archivio conservate |
| Accesso dopo il ripristino | Nuova istanza locale: login umano riuscito, video privato con byte e SHA-256 identici, accesso anonimo negato |
| Dipendenze runtime | Audit degli 8 pacchetti bloccati: nessuna vulnerabilità nota segnalata da `pip-audit 2.10.1` al momento della prova |
| Configurazione e migrazioni | Controllo Django senza problemi; nessuna migrazione mancante |

L'accesso fra spazi, i privilegi lettore/revisore/Factory, CSRF, token malformati e revocati, input JSON non oggetto, campi inattesi, percorsi e file privati, Range HTTP, manomissione dei file, approvazioni per revisione e retry sono coperti dalla suite. Il ripristino rifiuta anche traversal, nomi ambigui, link, corruzione, file mancanti e destinazioni esistenti.

La revisione indipendente del codice ha individuato e fatto correggere un errore sui JSON non oggetto, ora verificato con risposte HTTP 400. Il primo passaggio PostgreSQL ha individuato problemi nel cleanup dei test; corretti prima del passaggio completo riuscito. Il laboratorio PostgreSQL e l'istanza di ripristino sono stati rimossi o arrestati senza modificare gli altri servizi locali.

## Riproducibilità

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements-lock.txt
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests
```

La CI esegue la suite su Windows, Ubuntu e PostgreSQL separato. Un job distinto controlla le vulnerabilità note; le Actions sono fissate a commit completi. Gli aggiornamenti proposti da Dependabot richiedono revisione, aggiornamento coerente del lockfile e nuovo collaudo.

Per il browser usare un archivio demo locale creato con `inizializza_demo`, installare Playwright in un ambiente separato e avviare `node tests/ui-smoke.cjs`. Il test accetta esclusivamente la demo sintetica su `127.0.0.1:58540`; non deve ricevere credenziali reali. `ARCHIVIO_BROWSER_CHANNEL=msedge` seleziona Edge se disponibile.

## Perimetro e limiti

Sono prove applicative e verifiche di recupero in laboratorio. Non costituiscono una certificazione né un penetration test indipendente di un servizio distribuito. Non dimostrano assenza di vulnerabilità sconosciute o sicurezza di future modifiche.

Il recupero verificato è SQLite sullo stesso host, in una cartella distinta: non simula la perdita del disco o dell'account cloud. PostgreSQL è stato verificato per funzionamento, concorrenza e privilegi, senza collegare un progetto Supabase reale. Backup PostgreSQL/Supabase, storage cloud, TLS/proxy di produzione, carico reale e dispatcher della propria Factory richiedono collaudo nell'installazione destinata ai dati reali. Il montaggio o la rigenerazione restano compiti della Factory; l'archivio conserva i materiali e le richieste.
