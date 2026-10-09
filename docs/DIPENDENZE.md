# Dipendenze e licenze

Il codice originale di Archivio Factory è MIT. Questa licenza non cambia quella dei pacchetti installati: il repository contiene requisiti e hash di distribuzioni ufficiali, senza incorporare i loro sorgenti o le wheel. Non contiene codice di Reloops o MediaCMS.

Metadati e avvisi delle distribuzioni runtime installate e bloccate al 10 ottobre 2026:

| Pacchetto | Versione | Licenza dichiarata |
| --- | --- | --- |
| Django | 5.2.18 | BSD-3-Clause |
| djangorestframework | 3.18.3 | BSD-3-Clause |
| psycopg | 3.3.6 | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | LGPL-3.0-only |
| asgiref | 3.12.1 | BSD-3-Clause |
| sqlparse | 0.6.0 | BSD-3-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0; il database IANA incluso ha il proprio avviso |

Queste dipendenze sono software libero e non richiedono l'acquisto di una licenza per usarle. Non sono tutte MIT. Conservare gli avvisi applicabili e leggere le condizioni originali se si redistribuiscono anche le dipendenze, immagini container o un'applicazione confezionata. In particolare, distribuire componenti LGPL modificati o binari richiede il rispetto delle relative condizioni.

Psycopg Binary include librerie native, che mantengono a loro volta i propri avvisi; questo repository non redistribuisce quel contenuto. Un audit di vulnerabilità non è un audit completo di tutte le licenze delle future distribuzioni.

Fonti primarie: [Django](https://github.com/django/django/blob/main/LICENSE), [Django REST Framework](https://github.com/encode/django-rest-framework/blob/master/LICENSE.md), [Psycopg](https://github.com/psycopg/psycopg/blob/master/LICENSE.txt), [asgiref](https://github.com/django/asgiref/blob/main/LICENSE), [sqlparse](https://github.com/andialbrecht/sqlparse/blob/master/LICENSE), [typing_extensions](https://github.com/python/typing_extensions/blob/main/LICENSE), [tzdata](https://github.com/python/tzdata/blob/master/LICENSE).

Le clip dimostrative sono originali, generate con colori e forme per questo progetto. `config/demo_videos.json` contiene quelle clip, non video, copioni o dati dell'utente. Il test facoltativo del browser richiede Playwright, separato dalle otto dipendenze runtime; le sue condizioni restano quelle del relativo progetto.
