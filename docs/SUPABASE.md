# PostgreSQL e Supabase

L'applicazione supporta un database PostgreSQL tramite `DATABASE_URL`. Supabase può ospitare quel database. Questa integrazione usa Django per utenti, sessioni, ruoli e autorizzazione: non usa Supabase Auth, il client JavaScript Supabase o le chiavi `anon`/`service_role`.

I file video restano nella cartella privata `ARCHIVIO_MEDIA_ROOT` dell'applicazione. Lo storage Supabase non è implementato in questa versione. Usare PostgreSQL cloud non trasferisce automaticamente i video nel cloud.

## Preparare un database dedicato

Usare un progetto Supabase dedicato all'archivio e un ruolo PostgreSQL riservato all'applicazione. Lo schema predefinito è `archivio_factory`; non aggiungerlo agli **Exposed schemas** della Data API e non concedere accessi ai ruoli del browser. Se il progetto non usa la Data API, è possibile disabilitarla. Esposizione e privilegi sono controlli distinti: vanno verificati entrambi. [Guida ufficiale ai controlli della Data API](https://supabase.com/docs/guides/api/securing-your-api), [accesso dal solo backend](https://supabase.com/docs/guides/database/secure-data).

Esempio SQL da eseguire come amministratore del nuovo database; la password segnaposto va sostituita tramite una gestione sicura del segreto. Non salvare SQL con la password reale nel repository o nelle note condivise.

```sql
CREATE ROLE archivio_app LOGIN PASSWORD 'SOSTITUIRE_CON_UN_SEGRETO'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT ON DATABASE postgres TO archivio_app;
CREATE SCHEMA archivio_factory AUTHORIZATION archivio_app;
REVOKE ALL ON SCHEMA archivio_factory FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE archivio_app IN SCHEMA archivio_factory
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE archivio_app IN SCHEMA archivio_factory
  REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE archivio_app IN SCHEMA archivio_factory
  REVOKE ALL ON FUNCTIONS FROM PUBLIC, anon, authenticated;
```

Lo schema appartiene al ruolo dell'applicazione, che può applicare le migrazioni Django. I controlli per spazio avvengono nel backend dell'archivio. Questa configurazione non implementa RLS per le identità Django e non autorizza l'accesso diretto dal browser al database. Non sostituire l'account dedicato con un ruolo amministrativo o con la chiave `service_role`.

## Connessione

Per il backend persistente usare la connessione diretta oppure il pooler in modalità **session**, se necessario per IPv4. Prendere host, porta e username dalla pagina **Connect** del proprio progetto; le credenziali del pooler possono avere un formato diverso da quelle della connessione diretta. Questa applicazione non configura il transaction pooler. Le password nell'URI richiedono percent-encoding dei caratteri riservati. [Connessioni ufficiali Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).

Configurazione da inserire soltanto nell'ambiente del server o in `.env` privato:

```dotenv
ARCHIVIO_AMBIENTE=produzione
ARCHIVIO_SCHEMA=archivio_factory
ARCHIVIO_HOSTS=archivio.example.org
ARCHIVIO_ORIGINI_CSRF=https://archivio.example.org
ARCHIVIO_RUNTIME_ROOT=/srv/archivio/runtime
ARCHIVIO_MEDIA_ROOT=/srv/archivio/runtime/media
ARCHIVIO_SECRET_KEY=SEGRETO_CASUALE_DI_ALMENO_50_CARATTERI
DATABASE_URL=postgresql://archivio_app:PASSWORD_CODIFICATA@HOST_REALE:5432/postgres?sslmode=require
```

L'esempio non contiene credenziali utilizzabili. La produzione rifiuta una chiave mancante, host wildcard, database non configurato o modalità SSL non cifrata. `sslmode=require` cifra la connessione; la verifica dell'identità del server richiede una configurazione dei certificati compatibile con `verify-full` e il proprio ambiente PostgreSQL. Lo schema deve già esistere prima di `manage.py migrate`.

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate --plan
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser --username revisore
.venv/bin/python manage.py crea_spazio "Archivio" revisore
```

Le migrazioni su un database nuovo non importano il contenuto SQLite esistente. La migrazione dei dati deve essere un'operazione separata: copia verificata del database e dei file, preservazione degli UUID e confronto di revisioni, approvazioni e hash prima del passaggio al nuovo ambiente.

## Verifica dell'installazione

Prima di caricare dati reali, usare due spazi e utenti sintetici: verificare che un utente o una chiave dello spazio A non legga i metadati, i file o i pacchetti dello spazio B. Verificare anche che una chiave Factory non approvi o programmi un video e che la nuova revisione non erediti l'approvazione precedente. Dal lato Supabase, controllare che lo schema resti assente dalla Data API e che `anon` e `authenticated` non abbiano privilegi sui suoi oggetti.

Il supporto del motore PostgreSQL è presente nel codice. La connessione a un progetto Supabase reale, i certificati, i privilegi effettivi e il recupero di quel progetto devono essere verificati nella propria installazione. Non è stato collegato un account Supabase dell'utente per produrre questa guida.

## Backup PostgreSQL

`scripts/backup.py` e `scripts/ripristina.py` sono strumenti **SQLite**; non costituiscono un backup PostgreSQL. Per PostgreSQL occorrono una procedura dedicata con `pg_dump`/`pg_restore`, una copia coerente dei media e la verifica degli stessi riferimenti e hash. Le credenziali di questi strumenti vanno fornite tramite il gestore dei segreti o un `PGPASSFILE` protetto, non un URI scritto nei log o nella riga di comando.

Durante il primo collaudo sospendere i depositi, esportare lo schema dedicato e copiare i media privati; ripristinare in un database e in una cartella nuovi, quindi confrontare il contenuto. Non affermare che un backup gestito del solo database contenga anche i video locali. La procedura automatica PostgreSQL/Supabase non è inclusa né dichiarata collaudata in questa versione.
