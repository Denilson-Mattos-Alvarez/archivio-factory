# Collegamento alla Factory

L'archivio è il deposito delle revisioni. La Factory mantiene la generazione del copione, della voce e del montaggio. Il ponte incluso trasferisce i file e gli ordini di modifica; non avvia né simula l'esecuzione della Factory.

## Deposito

Ogni produzione ha un'origine (`kaapi`, `factory`, `altro`) e un `riferimento_factory` stabile. Usare riferimenti distinti, per esempio `shorts:123`, `news:snapshot-id`, `kaapi:trial-id`. Il vincolo è per spazio e origine. Il riferimento identifica la produzione, mentre ogni deposito diverso crea una revisione numerata. Non usare un percorso che cambierà a ogni render come riferimento della produzione.

Il ponte `client_factory.py` legge `ARCHIVIO_FACTORY_API_KEY` dall'ambiente. La chiave `Bearer af_...` appartiene a un solo spazio: può depositare, leggere file, esportare richieste e consegnare risultati; le approvazioni e le decisioni editoriali restano umane. Non riutilizzare la chiave `X-API-Key` della Factory: i due servizi hanno credenziali e scopi diversi.

```powershell
python client_factory.py --url http://127.0.0.1:58540 deposita --titolo "Episodio 123" --origine factory --riferimento-factory "shorts:123" --video "C:\Export\video.mp4" --copione "C:\Export\copione.txt" --nota "Montaggio finale" --allegato "C:\Export\script.json" --allegato "C:\Export\sottotitoli.srt"
```

È supportato HTTPS per installazioni remote. HTTP senza TLS è consentito solo per `localhost`, `127.0.0.1` e `::1`. I redirect sono rifiutati per non inoltrare il token. L'upload è trasmesso a blocchi e il ponte verifica che le sorgenti non cambino durante l'invio.

Il ponte cerca la produzione esatta prima di crearla. Il backend impedisce duplicati dello stesso riferimento. La `Idempotency-Key` del deposito deriva da produzione, nomi e SHA-256 dei file, copione e nota: un retry identico ritorna la revisione già depositata, anche se la prima risposta è andata persa. Un timeout senza esito confermato viene segnalato come incerto. Cambiare contenuto con la stessa chiave manuale produce HTTP 409; il ponte calcola una chiave nuova quando cambia il contenuto.

La CLI stampa soltanto identificatori e hash del deposito, mai il token. `--chiave-env` permette un nome di variabile diverso; `--timeout` imposta il timeout per operazione di rete. Il modulo può essere importato da Python con `FactoryClient(...).deposit(...)`.

## Prelevare una richiesta storica

```powershell
python client_factory.py preleva-richieste --inbox "C:\FactoryInbox"
```

La cartella inbox deve esistere, essere assoluta e non essere un link. Il ponte legge soltanto `stato=in_attesa`; per ogni richiesta crea `richiesta-UUID` con:

- `job.json`: richiesta, istruzioni, target, origine, riferimento Factory, produzione, UUID della revisione e hash del manifest.
- `copione.txt`: il copione della revisione richiesta.
- `revisione.json`: snapshot dei metadati della revisione, con un proprio hash nel job.
- Video e allegati: nomi derivati da UUID, byte e dimensioni verificati contro i descrittori dell'API.

Il nome originale viene conservato nei metadati, ma non decide il percorso locale. Il ponte scarica i file singolarmente, non estrae ZIP ricevuti e non esegue allegati, istruzioni o comandi. Confronta l'hash del manifest associato alla richiesta con quello della revisione ricevuta; verifica gli SHA-256 e le dimensioni di tutti i file. Non dichiara verificata la firma HMAC del pacchetto ZIP: quella appartiene al backend e richiede il suo segreto.

Solo dopo il salvataggio completo viene inviata la conferma `esportata`. Se questa conferma fallisce, il bundle rimane disponibile; al retry viene verificato e riutilizzato. Alterare un bundle già esportato impedisce il riuso automatico: il dispatcher deve scrivere i propri output in una cartella separata.

Lo stato `da_dispatcher` nel job significa che il lavoro deve ancora essere preso in carico. `esportata` significa trasferita alla inbox, non video rigenerato. `consegnata` significa che l'archivio ha ricevuto una revisione successiva della stessa produzione. Non equivale ad approvata o pubblicata.

## Attivare un dispatcher

Un dispatcher controllato dalla Factory deve:

1. Leggere `job.json` e accettare soltanto i target noti `copione`, `montaggio`, `voce`, `sottotitoli`.
2. Risolvere il riferimento tramite una mappa esplicita verso un lavoro della Factory, verificando che corrisponda alla produzione e alla sorgente richiesta.
3. Verificare i file e preparare un nuovo lavoro o una cartella di lavoro separata; conservare la inbox come sorgente storica.
4. Applicare le modifiche passando attraverso le approvazioni richieste dalla Factory. Non trasformare il testo libero della richiesta in un comando shell.
5. Depositare l'output completato nella stessa produzione, con copione e materiali aggiornati e l'opzione `--richiesta-id UUID`.

```powershell
python client_factory.py deposita --titolo "Episodio 123" --origine factory --riferimento-factory "shorts:123" --video "C:\OutputNuovo\video.mp4" --copione "C:\OutputNuovo\copione.txt" --nota "Ridotta l'introduzione" --richiesta-id UUID_DELLA_RICHIESTA
```

Il backend rifiuta una consegna di un'altra produzione o una versione precedente/uguale alla sorgente. La conferma può essere ripetuta con la stessa revisione; non sostituisce una consegna già associata a un risultato diverso. Il nuovo video richiede una propria approvazione umana.

Questa versione include il ponte funzionante e il contratto del job. Non include il dispatcher della Factory reale, non ha chiamato i suoi endpoint e non ne ha modificato il codice. Gli esempi sono pronti per collegare un processo Python, un orchestratore oppure i nodi HTTP di n8n.

## Adattamento della Factory locale esistente

La Factory locale esaminata espone route protette da `X-API-Key` in `11_Automazioni_e_API/pipeline_api/main.py`. I lavori Shorts possono essere richiesti con scope specifico:

| Operazione | Route e corpo | Limite |
| --- | --- | --- |
| Richiedere modifica | `POST /review/change` con `request_id`, `target`, `note` | Target Factory: `script`, `voiceover`, `render`, `subtitles` e altri ammessi dal servizio. Non sono gli stessi nomi dell'archivio. |
| Eseguire un lavoro | `POST /run-request` con `request_id` numerico | Usa lo stato attuale di quel lavoro. |
| Rifare il copione | `POST /editorial/script/regenerate` con `request_id` | Archivia il copione corrente e rigenera dal lavoro attuale. |
| Avviare montaggio | `POST /jobs/montage` con `request_id` | Ammesso negli stati `VISUAL_APPROVED`, `SUBTITLED`, `RENDERED`; il servizio gestisce il proprio job. |

Queste route non accettano il bundle storico dell'archivio come sorgente. Non è corretto scaricare una vecchia revisione e poi chiamare una route che lavora sullo stato più recente dichiarando di avere rigenerato quella revisione. Il dispatcher deve prima predisporre esplicitamente un nuovo lavoro dalla sorgente storica oppure fermarsi e richiedere un'importazione manuale. Il ponte evita esecuzioni globali come `/run` o `/run-step`.

Per News, la route `POST /news/editorial/{snapshot_id}/renders` crea un render dal copione e dalla voce attualmente approvati e usa i propri hash di handoff. Non permette di scegliere una revisione storica arbitraria dell'archivio. I gate di copione e voce della Factory restano necessari.

I depositi possono usare i pacchetti già presenti nella Factory:

| Origine | Materiali da depositare dopo completamento |
| --- | --- |
| News | `02_Ricerca_Idee_e_Trend/news_research/dashboard/editorial/<snapshot_id>/renders/<render_id>/`: `video.mp4`, `script.json`, `episode.it.json`, `words.json`, `render.json`, audio e visual necessari. |
| Kaapi | `13_Render_Remotion/episodes/video-trials/<id>/`: `video.mp4`, `copione.txt`, `script.json`, `episode.it.json`, `words.json`, `status.json` e asset. Sono prove tecniche, da rivedere prima dell'approvazione. |
| Shorts/altro | Output registrato dal relativo lavoro e metadati: video, copione, timeline/progetto, sottotitoli, audio, immagini e diritti/licenze. Alcuni stem della Factory possono essere sovrascritti dal rerender: depositare la revisione completata prima di avviarlo. |

Non importare segreti `.env`, chiavi API, cookie o credenziali OAuth fra gli allegati. I limiti del deposito predefinito sono 512 MiB per video, 25 MiB per allegato e 20 allegati; una raccolta più grande richiede configurazione coerente lato archivio/proxy e un formato di esportazione adatto.

## Contratto HTTP dell'archivio

Origine predefinita `http://127.0.0.1:58540`, prefisso `/api/v1/`. Tutte le route richiedono sessione autenticata o la chiave Factory; le azioni umane non sono autorizzate alla chiave.

| Metodo e percorso | Corpo o risultato |
| --- | --- |
| `GET produzioni/?origine=...&riferimento_factory=...` | Filtri esatti, scope dello spazio della chiave. |
| `POST produzioni/` | JSON `titolo`, `origine`, `riferimento_factory`; `spazio_id` richiesto alla sessione umana, derivato dalla chiave se omesso. |
| `POST produzioni/<id>/revisioni/` | Multipart `video`, `copione`, `nota`, campi ripetuti `allegati`; header `Idempotency-Key` opzionale, fino a 200 caratteri ASCII. |
| `GET revisioni/<id>/` | Revisione, copione, file con hash/dimensione/URL, note, approvazione, richieste e programmazioni. |
| `GET file/<id>/` | File privato, autorizzazione controllata a ogni richiesta. |
| `GET revisioni/<id>/pacchetto/` | ZIP con manifest, hash, firma del backend, copione e file della revisione. |
| `GET factory/richieste/?stato=in_attesa` | Richieste limitate allo spazio, con UUID della sorgente e hash del manifest. |
| `POST factory/richieste/<id>/esportata/` | JSON `{}`; conferma il trasferimento. |
| `POST factory/richieste/<id>/completa/` | JSON `{"nuova_revisione_id":"UUID"}`; consegna verificata. |
| `POST revisioni/<id>/approva/` | Azione del revisore umano, approvazione del manifest esatto. |
| `POST revisioni/<id>/note/` | JSON `testo`, `timecode_seconds` opzionale. |
| `POST revisioni/<id>/modifiche/` | Revisore umano: JSON `target`, `testo`. |
| `POST revisioni/<id>/programma/` | Revisore umano: JSON `piattaforma`, `prevista_il` ISO 8601 con fuso, futura; revisione approvata. |
| `POST programmazioni/<id>/pubblicata/` | Revisore umano: registra la pubblicazione realmente effettuata. |

Le liste restituiscono `{count,next,previous,results}` con paginazione. HTTP 201 indica un deposito nuovo, 200 un deposito ripetuto o una conferma, 409 un conflitto. Ogni integrazione deve verificare lo stato HTTP: salvare file in un nodo successivo a un errore non equivale a un deposito completato.
