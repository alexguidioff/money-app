# Money

**Italiano** · [English](README.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md)

Un'app di finanza personale che gira **a casa tua**: i tuoi movimenti, i tuoi
conti, i tuoi investimenti restano nel tuo database, su una macchina che
controlli tu. Nessun servizio esterno vede i tuoi numeri.

Nasce dal tentativo di sostituire un foglio di calcolo cresciuto per anni, e ne
conserva l'ossessione: **i numeri devono tornare**. Ogni aggregato è calcolato
dai movimenti, non copiato da qualche parte, e i saldi si possono confrontare
con quelli dichiarati per accorgersi subito quando qualcosa non quadra.

## Cosa fa

- **Movimenti** — entrate, uscite, trasferimenti fra conti, ricorrenze.
  Import da PDF o CSV dell'estratto conto, con anteprima prima di salvare: il
  PDF si legge anche quando la banca non disegna una tabella ma allinea solo
  il testo in colonne.
- **Conti** — stile stato patrimoniale, divisi fra banche, attività, passività
  e investimenti, con il saldo calcolato messo accanto a quello atteso.
- **Budget** — pianificazione per categoria e mese, confronto con lo speso,
  andamento annuale.
- **Obiettivi** — quanto manca, a che ritmo, entro quando.
- **Debiti** — prestiti e linee di credito con il piano di ammortamento
  teorico accanto a quanto è stato davvero erogato, addebitato e restituito.
- **Patrimonio** — la serie mensile del netto, leggibile anche in altre valute
  al cambio del mese, non a quello di oggi.
- **Investimenti** — portafoglio valorizzato ai prezzi di mercato scaricati dai
  ticker, ripartizione per settore e per titolo sottostante, andamento del
  singolo strumento con sopra i propri acquisti e le proprie vendite.
- **Pensionamento e FIRE** — quanto capitale serve, in che anno ci arrivi e cosa lo
  sposta: tasso di risparmio, rendite e pensioni future, spese che in pensione
  cambiano, con le note fiscali di dieci paesi europei.
- **Appunti** — note libere per ricordare perché hai deciso qualcosa.
- **Insieme** — chi vuole condivide i propri *totali* con gli altri account
  della stessa installazione. Mai i movimenti, mai le categorie, mai i conti.
- **Avvisi** — budget sforati, quotazioni ferme, backup che non gira. Si
  chiudono e non tornano.

In cima a ogni pagina c'è un **"?"** che spiega a cosa serve quella pagina e da
cosa dipende per riempirsi di numeri.

L'interfaccia è in italiano, inglese, tedesco, spagnolo e francese.

## Come si avvia

Serve solo Docker.

```bash
git clone https://github.com/alexguidioff/money-app.git money
cd money
./setup.sh                    # crea il .env con due password casuali
docker compose up -d --build
```

Poi apri <http://localhost:3010> e **crea il primo account** dalla schermata
che ti si presenta. L'app parte vuota: non ci sono dati di nessun altro.

Per aggiungere altre persone, dalla stessa schermata di accesso: ognuna avrà i
propri movimenti, separati. La password è facoltativa — finché nessuno ne
imposta una, l'app si apre senza chiedere niente.

## Le due password del database

Non sono quelle con cui entri nell'app: sono le chiavi con cui l'app parla al
database, e **nessuno le deve digitare mai**. `setup.sh` ne genera due a caso e
le scrive nel file `.env`, che Docker legge all'avvio.

Servono a una persona solo per aprire il database a mano o per rimettere in
piedi un backup fuori dall'app: in entrambi i casi si leggono da `.env`.

Se il `.env` va perso con i dati ancora dentro, non si perde niente: dentro il
container il database accetta la connessione locale senza password.

```bash
./setup.sh                                  # un .env nuovo, con password nuove
docker compose exec db psql -U money -d money \
  -c "alter role money password '<la POSTGRES_PASSWORD del nuovo .env>'"
docker compose up -d                        # l'altra la riscrive l'app da sola
```

## Cancellare una persona

Da **Impostazioni → Account**, e **solo il proprio account**: non esiste un
amministratore che possa cancellare i numeri di qualcun altro. Va scritto il
proprio nome per conferma, e prima di cancellare l'app scarica i dati nel
formato di scambio e fa un backup del database — la copia resta anche accanto
ai backup, sul server.

## Come sono separati i dati fra le persone

Non dalla disciplina delle query, ma **dal database**. Ogni tabella per-utente
ha una politica di riga di PostgreSQL legata all'utente della richiesta, e
l'app si collega con un ruolo senza privilegi: una query che dimentichi il
filtro non vede i dati altrui, semplicemente non trova nulla.

È una scelta deliberata. Il rischio di un'app come questa non è rompersi in
modo visibile: è mescolare in silenzio i numeri di due persone.

## Dove stanno i dati

In un volume Docker con PostgreSQL. Non escono da lì:

- **Backup automatici** ogni giorno, più uno prima di ogni import che sostituisce
  i dati. Si ripristinano dall'interfaccia, in Report.
- **Export completo** nel "formato di scambio": un foglio per entità, date ISO,
  nessuna formula. Serve a portarsi via tutto e a rimetterlo in un'altra
  installazione — c'è un test che verifica che il giro esporta-importa
  restituisca esattamente ciò che ha preso.

L'unica cosa che esce verso internet sono le **quotazioni** degli strumenti che
hai configurato con un ticker, e i **cambi** delle valute che scegli.

## Metterla su un NAS

L'app è pensata per finire su una macchina sempre accesa, raggiungibile dai
dispositivi di casa.

1. `MONEY_BIND_ADDRESS=0.0.0.0` nel `.env`
2. Mettici davanti una rete privata — [Tailscale](https://tailscale.com) è la
   via più semplice: traffico cifrato, nessuna porta aperta sul router, e con
   `tailscale serve` anche un certificato HTTPS vero
3. `MONEY_APP_ORIGIN=https://...` con l'indirizzo da cui aprirai l'app. Serve
   due volte: il cookie di sessione capisce da solo che sta dietro HTTPS, e
   l'API accetta richieste solo da `localhost`, `127.0.0.1` e da questo
   indirizzo — un altro sito aperto nello stesso browser non può leggere né
   modificare i tuoi dati
4. Costruisci le immagini **sul NAS** (`docker compose up -d --build`), non
   copiarle: l'architettura può essere diversa da quella del tuo computer

Senza una rete privata davanti, non esporla: password e movimenti viaggerebbero
in chiaro sulla rete locale. Metti una password a ogni account: dopo dieci
tentativi sbagliati in un quarto d'ora l'accesso a quel nome si blocca per un
po'.

## Com'è fatta

- **API** — FastAPI e SQLAlchemy su PostgreSQL 17
- **Interfaccia** — React (con vinext su Vite), in cinque lingue
- **Gateway** — nginx davanti a entrambi, così il browser parla a una sola origine

```bash
pnpm install
docker compose exec api python -m pytest tests/ -q   # test del backend
pnpm test                                            # test unitari e contratti del frontend
pnpm test:e2e                                        # end-to-end con Playwright su uno stack usa-e-getta
scripts/gate.sh                                      # tutto in fila, poi pubblica solo se è tutto verde
```

I test coprono il motore di calcolo, la lettura degli estratti conto, il giro
export-import e gli aggregati che in passato si sono rotti in silenzio: un
modello di ricorrenza contato come spesa reale, il capitale investito calcolato
dimenticando le posizioni vendute.
I **contratti** confrontano le risposte vere dell'API con i tipi che
l'interfaccia legge, e i corpi che l'interfaccia manda con quelli che l'API
accetta: un campo rinominato da una parte sola ferma il gate.

Gli end-to-end (`pnpm test:e2e`, prima `pnpm exec playwright install chromium`)
girano su un progetto Compose separato, porta 3011, database in memoria: non
toccano mai l'installazione che usi. Servono le immagini `money-app-api` e
`money-app-web`, che `docker compose build` crea.

## Licenza

MIT.
