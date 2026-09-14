# Note per chi lavora su questo codice con un'AI

Questo file spiega **come è fatta l'app e quali sono le regole che non vanno
rotte**. Leggilo prima di proporre modifiche: molte scelte qui dentro sembrano
strane finché non si sa perché sono state fatte, e disfarle per errore è facile.

## Che cos'è

Un'app di finanza personale per uso domestico. Registra movimenti, conti,
budget, obiettivi e investimenti di una o più persone che condividono la stessa
installazione. Gira in locale con Docker: API FastAPI, interfaccia React (vinext su Vite),
database PostgreSQL, un gateway nginx davanti a entrambi.

Non è un prototipo: contiene i dati veri di chi la usa. **Ogni modifica che
tocca i numeri va verificata contro i numeri prima e dopo**, non solo "provata".

## La regola che governa tutto: i numeri devono tornare

L'app nasce sostituendo un foglio di calcolo, e ne conserva il criterio: se un
numero dell'app e uno del foglio non coincidono, si indaga finché non si capisce
perché — non si aggiusta il codice finché il numero non piace.

Conseguenze pratiche per chi modifica:

- **Niente valori scritti a mano**. Ogni aggregato si calcola dai movimenti.
- **Una sola strada per ogni numero**. Se una serie storica esiste già
  (`net_worth_series`, `portfolio_timeline`), riusala invece di ricalcolarla:
  due strade per lo stesso numero prima o poi divergono.
- **I cambiamenti allo storico non devono muovere il presente**. Aggiungere
  movimenti vecchi cambia i saldi calcolati; va compensato il saldo iniziale dei
  conti, e va verificato che nessun saldo si sia spostato.

## Cose che sembrano bug e non lo sono

- **Il risparmio non è la somma dei movimenti di tipo `Savings`**: è
  `entrate − uscite` del periodo, calcolato. I movimenti `Savings` storici
  esistono ancora ma nessun totale li somma. Per questo la riga di budget dei
  risparmi è **una sola**: un numero unico non si spalma su più categorie.
- **`counts_in_net_worth = false`** su alcuni conti: sono accantonamenti, il
  denaro sta già altrove. Contarli sarebbe contarlo due volte.
- **I template delle ricorrenze** (`is_recurring_template = true`) sono esclusi
  da ogni aggregato tramite `REAL_MOVEMENT`. Descrivono cosa succederà, non cosa
  è successo. Dimenticare quel filtro gonfia i totali senza errori visibili.
- **Il capitale investito comprende le posizioni chiuse**: una vendita riduce
  quanto hai immobilizzato. Sommare solo le posizioni aperte lo gonfia di tutte
  le plusvalenze già realizzate.

## L'isolamento fra utenti non è affidato alle query

Ogni tabella per-utente ha una **politica di riga di PostgreSQL** legata a
`app.user_id`, che la sessione dichiara all'inizio di ogni transazione
(`database.py`, ascoltatore `after_begin`). L'app si collega con un ruolo senza
privilegi: le politiche non valgono per i superutenti né per il proprietario
delle tabelle, quindi il ruolo è deliberatamente limitato.

**Che cosa significa per chi modifica:**

- Una query che dimentica il filtro sull'utente non perde dati altrui: non trova
  nulla. Non serve aggiungere `where user_id = ...` ovunque.
- Per leggere o scrivere **a nome di un altro utente** (la pagina Insieme, la
  preparazione di un account nuovo) si usa `set_current_user(id)` e si apre una
  **nuova sessione**: il valore si dichiara all'inizio della transazione.
- I **dump del database usano la connessione del proprietario**
  (`DATABASE_ADMIN_URL`). Con il ruolo ristretto le politiche filtrerebbero le
  righe e il backup conterrebbe un utente solo, senza che nulla lo segnali.

## Testi e lingue

L'interfaccia è in cinque lingue (`lib/translations.ts`). Regola: **il server
non manda mai frasi da mostrare**. Manda codici e valori — `staleQuote`,
`{"days": 12}` — e il testo lo compone l'interfaccia. Vale anche per le date:
il server manda `2026-08`, il nome del mese lo scrive il client.

Aggiungendo una chiave va aggiunta in **tutte e cinque** le lingue, altrimenti
il controllo dei tipi fallisce.

Ogni pagina ha un **"?" accanto al titolo** (`components/page-help.tsx`) che
dice a cosa serve e **da cosa dipende per riempirsi** — quasi tutte mostrano
numeri che nascono altrove, e quando restano vuote la causa è quella. I testi
stanno in `SECTION_HELP_KEYS`, in `money-dashboard.tsx`. Aggiungendo una pagina
va aggiunta anche la sua voce lì: la mappa è tipizzata, quindi non compila
finché non c'è.

## Come si verifica una modifica

```bash
docker compose exec api python -m pytest tests/ -q   # 22 test
npx tsc -p tsconfig.json --noEmit                    # tipi
docker compose up -d --build api web                 # ricostruisci
```

I test coprono il motore di calcolo, il giro export→import del formato di
scambio, e gli aggregati che si sono rotti in silenzio in passato. `tests/
test_excel_parity.py` confronta i calcoli con un foglio Excel esterno se
presente, e viene saltato se non c'è.

**Il controllo dei tipi e i test non bastano** quando si toccano i numeri.
Prima di una modifica ai dati: fotografa i saldi calcolati di tutti i conti,
applica, rifotografa, confronta. Se qualcosa si è mosso e non doveva, annulla.

## Cosa non fare

- Non ricalcolare a mano un numero che l'app già calcola altrove.
- Non modificare i dati senza un backup (`create_backup()`) e senza un confronto
  prima/dopo.
- Non cancellare righe "di prova" senza guardarle: in questo database ci sono
  movimenti veri con descrizioni che sembrano test.
- Non aggiungere fallback che indovinano l'utente: se non si sa di chi è
  un'azione, va rifiutata, non attribuita a qualcuno a caso.
- Non lasciare in interfaccia elementi che promettono qualcosa che non c'è: una
  pagina vuota che non si riempirà mai va tolta, non nascosta.

## Struttura

```
backend/app/
  main.py             rotte CRUD, avvio, backup, import PDF/CSV
  core_routes.py      aggregati: riepiloghi, patrimonio, investimenti, budget
  calculation_engine.py  regole di segno, saldi, posizioni, tasso di risparmio
  auth.py             account, sessioni, password, allestimento di un account nuovo
  shared.py           i totali di chi condivide, calcolati nei suoi panni
  notifications.py    avvisi ricavati dallo stato, con le chiusure salvate
  interchange.py      formato di scambio: export
  interchange_import.py  formato di scambio: import, con validazione prima di scrivere
  migrations.py       modifiche allo schema su database già popolati
  market_data.py      quotazioni e ricerca ticker
  yahoo_profile.py    composizione degli strumenti (cookie + crumb)
components/
  money-dashboard.tsx  l'interfaccia, in gran parte in un file solo
  login-screen.tsx, account-settings.tsx, shared-totals.tsx, notifications-panel.tsx
  page-help.tsx        il "?" accanto al titolo, con la spiegazione della pagina
lib/translations.ts    tutti i testi, cinque lingue
```

## Un dettaglio che fa perdere ore

Il modulo `numbers-parser` e altre dipendenze installate a mano dentro il
container **spariscono alla ricostruzione**. Se una cosa funzionava e dopo un
`--build` non funziona più, controlla prima questo.
