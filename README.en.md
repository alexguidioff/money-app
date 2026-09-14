# Money

[Italiano](README.md) · **English** · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md)

A personal finance app that runs **at your home**: your transactions, accounts
and investments stay in your own database, on a machine you control. No
external service ever sees your numbers.

It was born as a replacement for a spreadsheet that had grown over the years,
and it keeps that spreadsheet's obsession: **the numbers have to add up**.
Every total is computed from the transactions, never copied from somewhere
else, and computed balances can be compared with declared ones, so you notice
straight away when something doesn't match.

## What it does

- **Transactions** — income, expenses, transfers between accounts, recurring
  items. Import bank statements from PDF or CSV, with a preview before saving:
  PDFs are read even when the bank doesn't draw a table and only lines the text
  up in columns.
- **Accounts** — balance-sheet style, split into banks, assets, liabilities and
  investments, with the computed balance next to the expected one.
- **Budget** — planning by category and month, compared with actual spending,
  yearly trend.
- **Goals** — how much is missing, at what pace, by when.
- **Debts** — loans and credit lines, with the theoretical amortisation plan
  next to what was actually drawn, charged and repaid.
- **Net worth** — the monthly series of your net worth, also readable in other
  currencies at that month's exchange rate, not today's.
- **Investments** — portfolio valued at market prices downloaded from tickers,
  breakdown by sector and by underlying holding, and each instrument's history
  with your own buys and sells on top.
- **Retirement and FIRE** — how much capital you need, in which year you get
  there and what moves it: savings rate, future annuities and pensions,
  expenses that change in retirement, with tax notes for ten European countries.
- **Notes** — free-form notes to remember why you decided something.
- **Together** — anyone who wants to can share their *totals* with the other
  accounts on the same installation. Never transactions, categories or accounts.
- **Alerts** — budgets exceeded, stale quotes, backups not running. Dismiss
  them and they don't come back.

At the top of every page there is a **"?"** explaining what the page is for and
what it needs in order to fill up with numbers.

The interface is available in Italian, English, German, Spanish and French.

## Getting started

All you need is Docker.

```bash
git clone https://github.com/alexguidioff/money-app.git money
cd money
./setup.sh                    # creates .env with two random passwords
docker compose up -d --build
```

Then open <http://localhost:3010> and **create the first account** on the
screen you are shown. The app starts empty: there is nobody else's data in it.

To add more people, use the same sign-in screen: each person gets their own,
separate transactions. Passwords are optional — as long as nobody sets one, the
app opens without asking for anything.

## The two database passwords

They are not the ones you use to sign in to the app: they are the keys the app
uses to talk to the database, and **nobody ever has to type them**. `setup.sh`
generates two random ones and writes them to the `.env` file, which Docker reads
at startup.

A person only needs them to open the database by hand or to restore a backup
outside the app: in both cases, read them from `.env`.

If `.env` gets lost while the data is still there, nothing is lost: inside the
container the database accepts local connections without a password.

```bash
./setup.sh                                  # a new .env, with new passwords
docker compose exec db psql -U money -d money \
  -c "alter role money password '<POSTGRES_PASSWORD from the new .env>'"
docker compose up -d                        # the app rewrites the other one itself
```

## Deleting a person

From **Settings → Account**, and **only your own account**: there is no
administrator who can delete someone else's numbers. You type your own name to
confirm, and before deleting, the app exports the data in the interchange
format and backs up the database — a copy also stays next to the backups, on
the server.

## How data is kept apart between people

Not by query discipline, but **by the database**. Every per-user table has a
PostgreSQL row-level security policy tied to the user making the request, and
the app connects with an unprivileged role: a query that forgets the filter
doesn't see other people's data, it simply finds nothing.

This is a deliberate choice. The risk for an app like this is not breaking
visibly: it is silently mixing up two people's numbers.

## Where the data lives

In a Docker volume with PostgreSQL. It doesn't leave it:

- **Automatic backups** every day, plus one before every import that replaces
  the data. You restore them from the interface, in Reports.
- **Full export** in the "interchange format": one sheet per entity, ISO dates,
  no formulas. It lets you take everything with you and load it into another
  installation — a test checks that the export-import round trip gives back
  exactly what it took.

The only things that go out to the internet are **quotes** for the instruments
you configured with a ticker, and **exchange rates** for the currencies you pick.

## Running it on a NAS

The app is designed to end up on an always-on machine, reachable from the
devices at home.

1. `MONEY_BIND_ADDRESS=0.0.0.0` in `.env`
2. Put a private network in front of it — [Tailscale](https://tailscale.com) is
   the easiest way: encrypted traffic, no ports opened on the router, and with
   `tailscale serve` a real HTTPS certificate too
3. `MONEY_APP_ORIGIN=https://...` with the address you will open the app from.
   It is used twice: the session cookie works out by itself that it is behind
   HTTPS, and the API only accepts requests from `localhost`, `127.0.0.1` and
   this address — another website open in the same browser can neither read
   nor change your data
4. Build the images **on the NAS** (`docker compose up -d --build`), don't copy
   them: its architecture may differ from your computer's

Without a private network in front of it, don't expose it: passwords and
transactions would travel unencrypted over the local network. Set a password on
every account: after ten wrong attempts within fifteen minutes, sign-in for
that name is blocked for a while.

## How it is built

- **API** — FastAPI and SQLAlchemy on PostgreSQL 17
- **Interface** — React (with vinext on Vite), in five languages
- **Gateway** — nginx in front of both, so the browser talks to a single origin

```bash
pnpm install
docker compose exec api python -m pytest tests/ -q   # backend tests
pnpm test                                            # frontend unit and contract tests
pnpm test:e2e                                        # Playwright end-to-end on a throwaway stack
scripts/gate.sh                                      # all of it in a row, then publishes only if everything is green
```

The tests cover the calculation engine, bank statement parsing, the
export-import round trip and the totals that broke silently in the past: a
recurring template counted as a real expense, invested capital computed while
forgetting sold positions. The **contracts** compare the API's real responses
with the types the interface reads, and the bodies the interface sends with
what the API accepts: a field renamed on one side only stops the gate.

The end-to-end tests (`pnpm test:e2e`, after `pnpm exec playwright install
chromium`) run on a separate Compose project, port 3011, in-memory database:
they never touch the installation you use. They need the `money-app-api` and
`money-app-web` images, which `docker compose build` creates.

## License

MIT.
