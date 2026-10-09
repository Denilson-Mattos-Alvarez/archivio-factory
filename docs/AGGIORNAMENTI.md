# Aggiornamenti, backup e recupero

Codice e dati hanno percorsi separati. Gli aggiornamenti riguardano codice e migrazioni; `runtime/db.sqlite3`, `runtime/media/`, `.env` e segreti non devono essere sostituiti da un checkout. La licenza MIT consente di mantenere una copia del codice, ma la conservazione dei video richiede anche backup dei dati.

## Prima di un aggiornamento

1. Annotare la versione o il commit attuale e la configurazione senza riportare segreti.
2. Creare un backup verificato fuori dal repository e conservarne una copia separata. Se si usa PostgreSQL, seguire la procedura dedicata descritta in [SUPABASE.md](SUPABASE.md).
3. Ripristinare il backup in una cartella nuova e verificare con materiale sintetico o autorizzato che le versioni si aprano e mantengano approvazioni e note.
4. Provare il nuovo codice con quella copia isolata, dipendenze fissate e migrazioni pianificate. Evitare collegamenti al dispatcher reale nella prova.
5. Dopo la verifica, sospendere i depositi durante il passaggio e applicare le migrazioni una sola volta con il codice scelto.

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-lock.txt
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py migrate --plan
```

Leggere il piano prima di `migrate`. La procedura non esegue `db reset`, non cancella il runtime e non scarica automaticamente aggiornamenti al suo avvio. Le versioni delle dipendenze vanno aggiornate intenzionalmente, con controlli e un punto di recupero.

## Backup SQLite

```powershell
python scripts/backup.py --runtime "C:\Archivio\runtime" --output "D:\BackupArchivio\prima-aggiornamento.zip"
```

La cartella padre dello ZIP deve esistere. Il nome destinazione deve essere nuovo; lo strumento rifiuta backup dentro il repository o sovrascritture. Lo ZIP viene scritto temporaneamente sullo stesso volume e pubblicato con un hard link atomico: usare un filesystem che supporti hard link, per esempio NTFS o un normale filesystem locale Linux. Non è richiesto comprimere nuovamente video già compressi.

La snapshot online del database è coerente anche con WAL. Il database viene copiato prima dei media: i blob sono immutabili e la snapshot determina esattamente quali file inserire. Il backup controlla hash e dimensioni dei byte realmente copiati; se un file manca o cambia, interrompe l'operazione e non pubblica lo ZIP. I file orfani e gli upload incompleti non sono parte del backup.

Gli hash verificano l'integrità del contenuto; non cifrano video, copioni, nomi degli utenti o dati del database. Conservare il file con permessi adeguati e una copia indipendente. I segreti del server e i token in chiaro hanno una custodia separata.

## Prova di ripristino

```powershell
python scripts/ripristina.py --backup "D:\BackupArchivio\prima-aggiornamento.zip" --destinazione "C:\Archivio\prova-ripristino"
```

La destinazione deve essere assoluta e non esistere. Lo strumento non usa `extractall`: ispeziona i nomi, rifiuta traversal, nomi ambigui Windows, link simbolici e duplicati, verifica manifest e SHA-256, controlla `PRAGMA integrity_check` e confronta il set dei blob con i riferimenti del database. Solo una copia verificata diventa il nuovo runtime. Un errore elimina la cartella di lavoro temporanea creata per quella prova; non modifica l'archivio originale.

Il limite predefinito del ripristino è 100 GiB di contenuto dichiarato; cambiarlo esplicitamente con `--massimo-gib` per un archivio più grande. Un backup sconosciuto non è una sorgente affidabile solo perché i suoi hash interni coincidono: confrontare anche l'hash dello ZIP conservato separatamente, quando disponibile.

Per avviare la copia, usare un ambiente che punti soltanto al runtime nuovo. In PowerShell:

```powershell
$env:ARCHIVIO_RUNTIME_ROOT = "C:\Archivio\prova-ripristino"
$env:ARCHIVIO_MEDIA_ROOT = "C:\Archivio\prova-ripristino\media"
$env:DATABASE_URL = ""
$env:ARCHIVIO_AMBIENTE = "sviluppo"
python manage.py check
python avvia_locale.py
```

Il server predefinito usa sempre la porta locale 58540: fermare un'istanza precedente prima di avviarlo, oppure scegliere una porta diversa esplicitamente tramite `manage.py runserver`. Non avviare il dispatcher nella copia di prova. Ripristinare la chiave Django da custodia separata se si devono mantenere sessioni o verificare vecchie firme; altrimenti lo sviluppo genera una chiave nuova nella copia. Le API key Factory hanno hash nel database, ma il relativo token deve essere disponibile nel gestore dei segreti del processo autorizzato.

## Se un aggiornamento fallisce

Fermare l'istanza aggiornata, conservare la cartella con l'errore e tornare a una coppia coerente di codice precedente e dati ripristinati in una nuova cartella. Non presumere che vecchio codice legga un database appena migrato. Prima di riattivare depositi, controllare versioni, file, approvazioni, calendario e richieste già esportate, evitando che il dispatcher esegua due volte un job.

La procedura di recupero deve essere provata: la presenza di un file ZIP non dimostra da sola che un video o una revisione siano recuperabili.
