# Esempi senza credenziali

`bundle.json` descrive un deposito locale; `copione.txt` ed `episode.it.json` sono dati sintetici. Il video non è incluso: copiare questi tre file nella propria cartella di export e aggiungervi un MP4 di prova. I percorsi nel JSON sono relativi a quella cartella e devono usare `/`.

Con il server locale avviato e `ARCHIVIO_FACTORY_API_KEY` già impostata tramite il proprio gestore dei segreti:

```powershell
python examples/deposito_python.py "C:\ExportDiProva\bundle.json"
```

Il programma importa il client del repository, valida i file locali e deposita il bundle tramite l'API. Non crea il video, non chiama Kaapi e non avvia un lavoro Factory. Ripetere lo stesso bundle restituisce la stessa revisione. Per un risultato di una richiesta, aggiungere `richiesta_id` con l'UUID reale della richiesta e mantenere il riferimento della produzione originale.

`job.json` mostra soltanto la forma del job ricevuto dal prelievo: UUID e hash sono segnaposto. Non usarlo come job verificato o come prova di un download. Il vero `client_factory.py preleva-richieste` produce gli identificatori reali e i file con SHA-256 verificati.

## Collegamento con nodi HTTP n8n

L'API usa JSON per metadati e conferme, multipart per file. Si può collegarla ai nodi **HTTP Request** senza plugin proprietari. Configurare il token come credenziale Header Auth (`Authorization`, valore `Bearer <token>`), nel gestore credenziali n8n; un workflow esportato non deve contenere il token. Disabilitare i redirect e impostare il timeout. Questo repository fornisce i contratti, non un workflow n8n già collaudato nella propria installazione.

Flusso di deposito:

1. `GET /api/v1/produzioni/?origine=kaapi&riferimento_factory=kaapi%3Aepisodio-001`: controllare i `results`, mantenendo una sola produzione corrispondente.
2. Se assente, `POST /api/v1/produzioni/` con JSON `{"titolo":"Episodio 001","origine":"kaapi","riferimento_factory":"kaapi:episodio-001"}`. Dopo HTTP 409 o una risposta incerta rileggere il riferimento prima di creare nuovamente.
3. `POST /api/v1/produzioni/<id>/revisioni/` con **multipart/form-data**: parametro binary data `video`, parametro testo `copione`, parametro testo `nota`, eventuali binary data ripetuti `allegati`.
4. Impostare `Idempotency-Key` stabile per quel preciso bundle e preservarla nei retry. Non usare soltanto il riferimento della produzione per tutte le versioni; nuovi contenuti richiedono una nuova chiave. Il client Python incluso calcola la chiave dai contenuti se il workflow lo richiama nel proprio ambiente autorizzato.
5. Verificare HTTP 201/200 e conservare `id`, `produzione`, `numero`, `manifest_sha256`. Non ignorare un 409 o un errore e proseguire come se il deposito fosse riuscito.

Flusso delle richieste:

1. `GET /api/v1/factory/richieste/?stato=in_attesa`, seguendo la paginazione soltanto verso la stessa origine.
2. Leggere la revisione indicata, salvare copione e file privati con nomi sicuri, verificarne dimensioni e SHA-256. Per questa parte è preferibile riutilizzare `client_factory.py preleva-richieste`, che esegue già i controlli e rende il bundle disponibile solo dopo il download completo.
3. Dopo il trasferimento verificato, `POST /api/v1/factory/richieste/<id>/esportata/` con `{}`.
4. Il dispatcher Factory esegue il lavoro con il proprio contratto; il risultato viene depositato come nuova revisione.
5. `POST /api/v1/factory/richieste/<id>/completa/` con `{"nuova_revisione_id":"UUID_DEL_RISULTATO"}`. Il ponte effettua questa conferma con `--richiesta-id`.

L'esportazione in una inbox o un nodo HTTP riuscito non certificano un render completato. Non mappare una vecchia revisione alla Factory corrente senza verificare e predisporre la sua sorgente: leggere i limiti in [FACTORY.md](../docs/FACTORY.md).
