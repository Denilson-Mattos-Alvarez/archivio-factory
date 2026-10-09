# Sicurezza di Archivio Factory

Archivio Factory è un progetto nella fase iniziale **v0.1**, distribuito con [licenza MIT](LICENSE). La licenza consente l'uso del codice e ne definisce le condizioni; non costituisce una certificazione di sicurezza o una garanzia di servizio.

## Segnalazione privata

Segnala una possibile vulnerabilità tramite il [modulo privato GitHub Security Advisory](https://github.com/Denilson-Mattos-Alvarez/archivio-factory/security/advisories/new) del repository `Denilson-Mattos-Alvarez/archivio-factory`. Il modulo richiede che il proprietario abbia abilitato la segnalazione privata delle vulnerabilità nelle impostazioni GitHub.

Non pubblicare dettagli sfruttabili, credenziali o video privati in un issue o in una pull request pubblica. Se il modulo non è disponibile, occorre concordare prima un canale privato con il responsabile del repository.

Una segnalazione utile comprende:

- Commit o versione interessata, sistema operativo e configurazione rilevante.
- Comportamento osservato, risultato atteso e impatto su accessi, versioni, approvazioni o dati.
- Passaggi riproducibili con account e file sintetici; eventuale prova minima del problema.
- Log depurati da password, chiavi Factory, cookie, URL riservati e dati personali.

Le segnalazioni riguardano la linea iniziale v0.1 e il codice corrente del ramo `main`. Non sono dichiarati tempi garantiti di risposta o correzione, né manutenzione di versioni storiche. La pubblicazione dei dettagli va concordata dopo l'analisi e la disponibilità di una correzione o protezione verificata.

## Collaudo e dipendenze

La [CI](.github/workflows/collaudo.yml) esegue Python 3.12 su Windows e Ubuntu: installazione delle dipendenze runtime da `requirements-lock.txt` con hash obbligatori e sole wheel, controllo Django, controllo delle migrazioni e suite `manage.py test tests`. La matrice usa SQLite e directory temporanee con dati sintetici, senza credenziali di produzione; le prove specifiche di PostgreSQL vengono saltate soltanto in questa matrice.

Un job Ubuntu separato esegue la suite completa, incluse le prove di concorrenza, su un servizio PostgreSQL 16 temporaneo fissato a digest. Il [bootstrap CI](tests/prepara_postgres_ci.py) accetta solo `GITHUB_ACTIONS=true`, flag di laboratorio, host loopback e database `archivio_ci`; crea un ruolo applicativo distinto, schema privato e ruoli fittizi `anon`/`authenticated` senza login. Il privilegio `CREATEDB` del ruolo applicativo è necessario soltanto alla creazione dei database di test Django in questo job e non è una configurazione di produzione. Nomi di ruolo/schema già occupati fanno fallire il bootstrap. Il runner rifiuta un backend diverso da PostgreSQL e fa fallire il job se una prova viene saltata. La connessione senza TLS e le password pubbliche fittizie sono limitate al servizio temporaneo della CI.

Un job distinto usa `pip-audit==2.10.1` per consultare il database pubblico delle vulnerabilità PyPI sugli otto pacchetti runtime bloccati, comprese le dipendenze transitive. L'ambiente dello strumento di audit non fa parte di quei risultati. Lo strumento è fissato di versione; le sue dipendenze vengono risolte da PyPI all'esecuzione. Errori di raccolta e vulnerabilità segnalate fanno fallire l'audit; non vengono ignorate o corrette automaticamente. Un esito senza segnalazioni riguarda le vulnerabilità note al momento dell'esecuzione e non esclude difetti ancora sconosciuti o problemi del codice applicativo.

[Dependabot](.github/dependabot.yml) propone aggiornamenti per Python e GitHub Actions tramite pull request settimanali. Le Actions della CI sono fissate a commit completi verificati nei repository ufficiali. Gli aggiornamenti richiedono revisione e collaudo: se cambiano pacchetti runtime, rigenerare e verificare anche gli hash del lockfile. Questi file non configurano deploy o merge automatici. L'eventuale protezione del ramo e l'obbligo di superare la CI vanno impostati dal proprietario su GitHub.

## Uso e limiti

Prima di ospitare dati reali, verificare nella distribuzione prevista HTTPS, configurazione Django di produzione, privilegi PostgreSQL, accesso ai file privati, revoca delle chiavi, limiti degli upload e backup di database e media. Provare il ripristino in una destinazione distinta e controllare integrità, accessi e funzionalità. Il job PostgreSQL verifica la concorrenza nel servizio isolato; non testa Supabase cloud, Auth/PostgREST/Storage o le loro policy RLS. I ruoli fittizi non equivalgono a una verifica di quei servizi. La CI non dimostra sicurezza della rete/TLS, correttezza della configurazione del server reale, ripristino su altro host o comportamento sotto carico di produzione.

Tenere `.env`, runtime, database, backup, file multimediali e segreti fuori dal repository. Usare chiavi Factory limitate allo spazio e alle operazioni necessarie. Le prove automatiche e gli audit delle dipendenze integrano la revisione e il collaudo della distribuzione; non sostituiscono un pentest né garantiscono la sicurezza degli aggiornamenti futuri.
