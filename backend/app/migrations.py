"""Passaggio a piu' utenti, in due mosse separate e ripetibili.

L'app non ha uno strumento di migrazioni: ``create_all`` crea le tabelle nuove
ma non tocca quelle che esistono gia'. Qui stanno le modifiche allo schema che
vanno applicate a un database gia' popolato. Ogni funzione e' idempotente: se e'
gia' stata applicata non fa niente, quindi si puo' rilanciare all'avvio.

La prima mossa aggiunge la colonna dell'utente e assegna tutto al primo. La
seconda accende le politiche di riga. Fra le due l'app funziona identica: e'
voluto, perche' il rischio di questo cambiamento non e' rompere qualcosa in
modo visibile - e' mescolare i dati di due persone in silenzio.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import text, inspect
from sqlalchemy.engine import Engine

logger = logging.getLogger("money.migrations")


def tracked_changes(engine: Engine) -> None:
    """Applies tracked schema changes without rewriting movements or balances."""
    with engine.begin() as conn:
        tx_cols = {c["name"] for c in inspect(conn).get_columns("transactions")}
        if "counts_in_budget" not in tx_cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN counts_in_budget BOOLEAN NOT NULL DEFAULT true"))
        if "incomplete_accepted" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN incomplete_accepted BOOLEAN NOT NULL DEFAULT false"))
        if "refund_of_id" not in tx_cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN refund_of_id INTEGER"))
            conn.execute(text("CREATE INDEX ix_transactions_refund_of_id ON transactions (refund_of_id)"))
        # La migrazione gira anche su database parziali (i test ne creano una
        # tabella per volta): una tabella che non c'e' si salta, non esplode.
        goal_cols = ({c["name"] for c in inspect(conn).get_columns("goals")}
                     if inspect(conn).has_table("goals") else {"kind", "target_account"})
        if "kind" not in goal_cols:
            # I goal esistenti restano com'erano: 'contributions' e' il
            # comportamento attuale, quindi nessun numero gia' mostrato cambia.
            conn.execute(text("ALTER TABLE goals ADD COLUMN kind VARCHAR(30) NOT NULL DEFAULT 'contributions'"))
        if "target_account" not in goal_cols:
            # Nasce vuota di proposito: finche' nessuno dichiara il conto, i goal
            # esistenti sommano come hanno sempre fatto e nessun numero si muove
            # da solo. Indovinarlo qui sarebbe la stessa euristica scartata.
            conn.execute(text("ALTER TABLE goals ADD COLUMN target_account VARCHAR(255)"))
        account_cols = {c["name"] for c in inspect(conn).get_columns("accounts")}
        if "is_active" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true"))
            conn.execute(text("UPDATE accounts SET is_active = false WHERE lower(trim(coalesce(status, ''))) IN ('closed', 'chiusa') OR (lower(trim(name)) = 'soldi da investire' AND counts_in_net_worth = false)"))
        if "is_liquid" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN is_liquid BOOLEAN NOT NULL DEFAULT true"))
            if "source_group" in account_cols:
                conn.execute(text("UPDATE accounts SET is_liquid = false WHERE source_group IN ('asset', 'financial')"))
        if "notes" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN notes TEXT"))
        if "account_type" in account_cols:
            conn.execute(text("ALTER TABLE accounts DROP COLUMN account_type"))
        if "needs_manual_valuation" not in account_cols:
            conn.execute(text(
                "ALTER TABLE accounts ADD COLUMN needs_manual_valuation BOOLEAN NOT NULL DEFAULT false"))
        if "source_group" in account_cols:
            # Il gruppo 'financial' voleva dire due cose insieme: "e' un
            # investimento" e "il suo valore lo dice il ledger". La prima e'
            # un'attivita' come le altre; la seconda non e' una proprieta' del
            # conto ma un fatto che si legge nei collegamenti al ledger, quindi
            # non si salva da nessuna parte. Il saldo non cambia: cambia solo
            # dove sta il conto nel bilancio.
            conn.execute(text("UPDATE accounts SET source_group = 'asset' "
                              "WHERE source_group = 'financial'"))
        if inspect(conn).has_table("liability_profiles"):
            passivi = {c["name"] for c in inspect(conn).get_columns("liability_profiles")}
            if "grace_interest" not in passivi:
                conn.execute(text("ALTER TABLE liability_profiles "
                                  "ADD COLUMN grace_interest VARCHAR(20) NOT NULL DEFAULT 'paid'"))
            # I profili che esistono sono tutti prestiti a rate: il valore
            # predefinito conserva il comportamento di prima, e a dichiararsi
            # linea di credito ci si passa dall'interfaccia, un conto per volta.
            if "kind" not in passivi:
                conn.execute(text("ALTER TABLE liability_profiles "
                                  "ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'term_loan'"))
            if "credit_limit" not in passivi:
                # Facoltativo: vuoto vuol dire senza limite, come uno scoperto
                # che cresce con gli interessi che ci si aggiungono.
                conn.execute(text("ALTER TABLE liability_profiles ADD COLUMN credit_limit NUMERIC(16, 2)"))
        # La tabella nasce con `create_all`, ma chi l'ha gia' creata prima di
        # questa colonna non la vedrebbe: `create_all` non modifica le tabelle
        # che esistono gia'.
        if inspect(conn).has_table("income_streams"):
            flussi_cols = {c["name"] for c in inspect(conn).get_columns("income_streams")}
            if "amount_if_stopping_now" not in flussi_cols:
                conn.execute(text("ALTER TABLE income_streams "
                                  "ADD COLUMN amount_if_stopping_now NUMERIC(16, 2)"))
        if inspect(conn).has_table("retirement_profiles"):
            profilo_cols = {c["name"] for c in inspect(conn).get_columns("retirement_profiles")}
            if "lean_annual_expenses" not in profilo_cols:
                conn.execute(text("ALTER TABLE retirement_profiles "
                                  "ADD COLUMN lean_annual_expenses NUMERIC(16, 2)"))
            if "expense_rules" not in profilo_cols:
                conn.execute(text("ALTER TABLE retirement_profiles ADD COLUMN expense_rules TEXT"))
            if "inflation" not in profilo_cols:
                conn.execute(text("ALTER TABLE retirement_profiles "
                                  "ADD COLUMN inflation NUMERIC(5, 2) NOT NULL DEFAULT 2"))
            if "return_volatility" not in profilo_cols:
                conn.execute(text("ALTER TABLE retirement_profiles "
                                  "ADD COLUMN return_volatility NUMERIC(5, 2) NOT NULL DEFAULT 15"))
        # Resti del workbook Excel che nessuna parte dell'app legge piu': elenchi
        # di opzioni (i conti veri stanno nella tabella dei conti, e quello era
        # un elenco di nomi fermo a un anno fa), un'impostazione senza effetto e
        # una copia del nome che l'utente teneva gia' - e che rinominandosi
        # restava indietro. Idempotente: al secondo avvio non trova piu' niente.
        if inspect(conn).has_table("lookup_options"):
            conn.execute(text("DELETE FROM lookup_options "
                              "WHERE option_group IN ('accounts', 'account_types', 'investment_actions', 'currencies')"))
        if inspect(conn).has_table("app_settings"):
            conn.execute(text("DELETE FROM app_settings WHERE key IN ('budget_rollover', 'display_name', 'currency')"))
        # Le regole di categoria sono state tolte: modello, rotte e pagina. La
        # tabella restava nel database con la sua politica di riga, vuota e
        # senza piu' nessuno che la legga o la esporti.
        conn.execute(text("DROP TABLE IF EXISTS category_rules"))
        if "is_broker" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN is_broker BOOLEAN NOT NULL DEFAULT false"))
        if "valued_by_ledger" in account_cols:
            # Era una seconda verita' accanto ai collegamenti, e due verita'
            # senza arbitro prima o poi divergono.
            conn.execute(text("ALTER TABLE accounts DROP COLUMN valued_by_ledger"))
        if inspect(conn).has_table("liability_profiles"):
            liability_cols = {c["name"] for c in inspect(conn).get_columns("liability_profiles")}
            if "repayment_start_date" not in liability_cols:
                conn.execute(text("ALTER TABLE liability_profiles ADD COLUMN repayment_start_date DATE"))
            if "planned_drawdowns" not in liability_cols:
                conn.execute(text("ALTER TABLE liability_profiles ADD COLUMN planned_drawdowns TEXT"))
        if inspect(conn).has_table("liability_transaction_details"):
            detail_cols = {c["name"] for c in inspect(conn).get_columns("liability_transaction_details")}
            if "refund_of_id" not in detail_cols:
                conn.execute(text("ALTER TABLE liability_transaction_details ADD COLUMN refund_of_id INTEGER"))
                conn.execute(text("CREATE INDEX ix_liability_transaction_details_refund_of_id "
                                  "ON liability_transaction_details (refund_of_id)"))
            if "is_classified" not in detail_cols:
                conn.execute(text("ALTER TABLE liability_transaction_details "
                                  "ADD COLUMN is_classified BOOLEAN NOT NULL DEFAULT false"))
                conn.execute(text("UPDATE liability_transaction_details SET is_classified=true"))
            # Gli addebiti sul conto del debito non avevano una riga di
            # dettaglio: si riconoscevano in lettura da "spesa su quel conto".
            # Ora sono eventi dichiarati come erogazioni e rimborsi. Quelli
            # gia' registrati erano tutti interessi, quindi nascono classificati:
            # chiederne la conferma a posteriori vorrebbe dire far sparire dei
            # numeri corretti dalla pagina finche' qualcuno non li riconferma.
            conn.execute(text("""
                INSERT INTO liability_transaction_details
                    (user_id, liability_account_id, transaction_id, kind,
                     principal_amount, interest_amount, is_classified)
                SELECT t.user_id, a.id, t.id, 'charge', 0, t.amount, true
                FROM transactions t
                JOIN accounts a ON a.name = t.account_name AND a.user_id = t.user_id
                WHERE t.transaction_type = 'Expenses'
                  AND a.source_group = 'liability'
                  AND NOT EXISTS (SELECT 1 FROM liability_transaction_details d
                                  WHERE d.transaction_id = t.id AND d.user_id = t.user_id)
            """))
            if "interest_transaction_id" in detail_cols:
                # Le vecchie rate avevano un giroconto capitale e una spesa
                # interessi separata. Li portiamo al nuovo modello senza
                # cambiare il saldo di nessun conto: il Debt include la rata
                # intera e la spesa viene addebitata al conto passivo.
                old_rows = conn.execute(text(
                    "SELECT d.transaction_id, d.interest_transaction_id, d.liability_account_id, "
                    "d.principal_amount, d.interest_amount, a.name AS liability_name "
                    "FROM liability_transaction_details d JOIN accounts a ON a.id=d.liability_account_id "
                    "WHERE d.interest_transaction_id IS NOT NULL")).mappings().all()
                for row in old_rows:
                    conn.execute(text("UPDATE transactions SET transaction_type='Debt', category='_', amount=:amount "
                                      "WHERE id=:id"),
                                 {"id": row["transaction_id"],
                                  "amount": row["principal_amount"] + row["interest_amount"]})
                    conn.execute(text("UPDATE transactions SET account_name=:name WHERE id=:id"),
                                 {"id": row["interest_transaction_id"], "name": row["liability_name"]})
                conn.execute(text("DROP INDEX IF EXISTS ix_liability_transaction_details_interest_transaction_id"))
                conn.execute(text("ALTER TABLE liability_transaction_details DROP COLUMN interest_transaction_id"))

            # La vecchia "restituzione di un rimborso" non e' un evento del
            # piano debito: e' un normale trasferimento che continua comunque
            # a modificare il saldo del conto passivo.
            conn.execute(text(
                "UPDATE transactions SET transaction_type='Transfers', category='_' "
                "WHERE id IN (SELECT transaction_id FROM liability_transaction_details WHERE kind='refund')"))
            conn.execute(text("DELETE FROM liability_transaction_details WHERE kind='refund'"))

        # --- Le categorie diventano un albero (PIANO-B3) --------------------
        # Finora una categoria non era una riga da nessuna parte: era una
        # stringa scritta dentro movimenti, budget e regole. Qui nascono le
        # righe, e i riferimenti passano dall'id. Gira a ogni avvio, quindi
        # ogni passo guarda prima se l'ha gia' fatto.
        if inspect(conn).has_table("categories"):
            _albero_delle_categorie(conn)

def _colonne_di(conn, tabella: str) -> set[str]:
    """Le colonne di una tabella, o niente se la tabella non c'e'.

    Serve alle migrazioni che devono leggere colonne poi rimosse: su un
    database creato dopo la rimozione quelle colonne non esistono, e il passo
    che le legge si salta invece di far fallire tutto l'avvio.
    """
    return ({colonna["name"] for colonna in inspect(conn).get_columns(tabella)}
            if inspect(conn).has_table(tabella) else set())


def _albero_delle_categorie(conn) -> None:
    """Crea le categorie dai nomi scritti nelle colonne, e collega i riferimenti.

    Tre cose, in quest'ordine: le radici, i riferimenti, e le tre associazioni
    di gruppo che erano gia' dichiarate.

    **Non inventa la gerarchia.** Solo tre categorie hanno un gruppo scritto
    (``budget_plans.category_group``); le altre ventidue no. Decidere se
    "Cardmarket" sia un bisogno o un piacere vorrebbe dire inventare un dato
    dell'utente: l'albero nasce piatto, e i rami li costruisce lui.
    """
    letture = []
    for tabella in ("transactions", "budget_plans"):
        if "category" in _colonne_di(conn, tabella):
            letture.append(
                f"SELECT DISTINCT user_id, trim(category) AS nome FROM {tabella} "
                "WHERE category IS NOT NULL AND trim(category) NOT IN ('', '_')")
    if "value" in _colonne_di(conn, "lookup_options"):
        letture.append(
            "SELECT DISTINCT user_id, trim(value) AS nome FROM lookup_options "
            "WHERE option_group IN ('categories_expenses', 'categories_income', 'categories_savings') "
            "AND trim(value) <> ''")
    if not letture:
        return

    # Un nome che esiste gia' non si duplica: e' anche il modo in cui questo
    # blocco resta innocuo se gira una seconda volta.
    for user_id, nome in conn.execute(text(" UNION ".join(letture))).all():
        if conn.execute(text("SELECT 1 FROM categories WHERE user_id = :u AND parent_id IS NULL "
                             "AND lower(name) = lower(:n)"), {"u": user_id, "n": nome}).scalar():
            continue
        conn.execute(text("INSERT INTO categories (user_id, parent_id, name, position, active) "
                          "VALUES (:u, NULL, :n, 0, true)"), {"u": user_id, "n": nome})

    # I riferimenti: dal nome all'id, solo dove l'id non c'e' gia'. La radice si
    # cerca per nome, quindi un movimento scritto "Alimentari" continua a
    # puntare alla stessa categoria anche dopo che qualcuno l'ha rinominata da
    # un'altra parte della migrazione.
    for tabella in ("transactions", "budget_plans", "categorization_rules"):
        colonne = _colonne_di(conn, tabella)
        if "category" not in colonne or "category_id" not in colonne:
            continue
        conn.execute(text(
            f"UPDATE {tabella} AS t SET category_id = c.id FROM categories c "
            f"WHERE c.user_id = t.user_id AND c.parent_id IS NULL "
            f"AND lower(c.name) = lower(trim(t.category)) AND t.category_id IS NULL"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tabella}_category_id ON {tabella} (category_id)"))

    # Le tre associazioni che erano dichiarate diventano rami veri: Needs e
    # Wants nascono come radici e le categorie ci finiscono sotto. Per "Other"
    # il nome del gruppo e' anche il nome della categoria, quindi la radice e'
    # gia' li': crearne una seconda darebbe due voci identiche nell'elenco.
    if "category_group" in _colonne_di(conn, "budget_plans") and "category" in _colonne_di(conn, "budget_plans"):
        dichiarati = conn.execute(text(
            "SELECT DISTINCT user_id, trim(category_group) AS gruppo, trim(category) AS nome "
            "FROM budget_plans WHERE category_group IS NOT NULL AND trim(category_group) <> '' "
            "AND category IS NOT NULL AND trim(category) NOT IN ('', '_')")).all()
        for user_id, gruppo, nome in dichiarati:
            radice = conn.execute(text("SELECT id FROM categories WHERE user_id = :u AND parent_id IS NULL "
                                       "AND lower(name) = lower(:n)"), {"u": user_id, "n": gruppo}).scalar()
            if radice is None:
                radice = conn.execute(text("INSERT INTO categories (user_id, parent_id, name, position, active) "
                                           "VALUES (:u, NULL, :n, 0, true) RETURNING id"),
                                      {"u": user_id, "n": gruppo}).scalar()
            conn.execute(text("UPDATE categories SET parent_id = :padre "
                              "WHERE user_id = :u AND parent_id IS NULL AND id <> :padre AND lower(name) = lower(:n)"),
                         {"padre": radice, "u": user_id, "n": nome})

    # Il vincolo che c'era sui budget (uno per categoria e periodo) resta, sulla
    # colonna nuova: su Postgres si puo' aggiungere a una tabella esistente, e su
    # un database appena creato l'ha gia' messo ``create_all``.
    if conn.dialect.name == "postgresql" and "category_id" in _colonne_di(conn, "budget_plans"):
        nomi = {vincolo["name"] for vincolo in inspect(conn).get_unique_constraints("budget_plans")}
        if "budget_plans_period_budget_type_category_id_key" not in nomi:
            conn.execute(text("ALTER TABLE budget_plans ADD CONSTRAINT "
                              "budget_plans_period_budget_type_category_id_key "
                              "UNIQUE (user_id, period, budget_type, category_id)"))


# Tabelle i cui dati appartengono a una persona.
PER_UTENTE = [
    # Le categorie sono di chi le ha create: senza l'isolamento, l'albero di
    # una persona comparirebbe nell'elenco di un'altra.
    "categories",
    "accounts", "budget_plans", "goals", "transactions", "investment_transactions",
    "investment_transaction_details", "transaction_ledger_links", "investment_instruments",
    "app_settings", "lookup_options", "notes",
    "dismissed_notifications", "account_valuations",
    "liability_profiles",
    "liability_transaction_details",
    "retirement_profiles",
    "income_streams",
    # Le regole di categorizzazione sono una scelta di chi importa: senza
    # l'isolamento, le regole di una persona finirebbero nell'anteprima di
    # un'altra. Non va confusa con "category_rules", la tabella cancellata a
    # ogni avvio piu' sotto in questo file.
    "categorization_rules",
    # Un evento e' di chi l'ha creato, agganci compresi: senza l'isolamento il
    # viaggio di una persona comparirebbe nella card di un'altra.
    "events",
    "transaction_events",
]

# Tabelle condivise di proposito: una quotazione e il profilo di uno strumento
# sono gli stessi per tutti, e scaricarli una volta sola e' un vantaggio.
CONDIVISE = ["market_prices", "instrument_profiles", "import_batches"]

# I vincoli di unicita' che devono valere per utente e non per tutti: senza
# questo, due persone non potrebbero avere entrambe un conto "Banca".
VINCOLI = [
    ("accounts", "accounts_source_group_name_key", ["user_id", "source_group", "name"]),
    ("budget_plans", "budget_plans_period_budget_type_category_key", ["user_id", "period", "budget_type", "category"]),
    ("lookup_options", "lookup_options_option_group_value_key", ["user_id", "option_group", "value"]),
]

# Indici unici su singola colonna, da rifare includendo l'utente.
INDICI_UNICI = [
    ("goals", "ix_goals_name", ["user_id", "name"], "name"),
    ("app_settings", "ix_app_settings_key", ["user_id", "key"], "key"),
    ("investment_instruments", "ix_investment_instruments_name", ["user_id", "name"], "name"),
    ("notes", "ix_notes_source_cell", ["user_id", "source_cell"], "source_cell"),
]


def _e_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def _ci_sono_dati(conn) -> bool:
    """Vero se il database contiene gia' movimenti o conti di qualcuno."""
    for tabella in ("transactions", "accounts"):
        esiste = conn.execute(text(
            "select 1 from information_schema.tables where table_name = :t"), {"t": tabella}).scalar()
        if esiste and conn.execute(text(f"select 1 from {tabella} limit 1")).scalar():
            return True
    return False


def _solo_schema(conn, esito: dict) -> dict:
    """Aggiunge le colonne senza assegnarle a nessuno: non c'e' ancora nessuno."""
    for tabella in PER_UTENTE:
        presente = conn.execute(text("""
            select 1 from information_schema.columns
            where table_name = :t and column_name = 'user_id'
        """), {"t": tabella}).scalar()
        if presente:
            continue
        conn.execute(text(f"alter table {tabella} add column user_id integer not null default 1"))
        conn.execute(text(f"create index if not exists ix_{tabella}_user_id on {tabella} (user_id)"))
        esito["colonne"].append(tabella)
    esito["applicata"] = bool(esito["colonne"])
    esito["utente"] = None
    return esito


def aggiungi_colonna_utente(engine: Engine) -> dict:
    """Prima mossa: la colonna dell'utente, con tutto assegnato al primo.

    Su un database che ha gia' dei dati crea un primo utente a cui attribuirli,
    perche' quelle righe devono appartenere a qualcuno. Su un'installazione
    nuova invece non crea nessuno: e' la schermata di accesso a chiedere di
    creare il primo account, e finche' non esiste non si scrive niente.
    """
    if not _e_postgres(engine):
        return {"applicata": False, "motivo": "non postgresql"}

    esito: dict = {"colonne": [], "vincoli": [], "indici": []}
    with engine.begin() as conn:
        conn.execute(text("""
            create table if not exists users (
                id serial primary key,
                username varchar(80) not null unique,
                display_name varchar(120) not null,
                password_hash varchar(255),
                shares_totals boolean not null default false,
                created_at timestamptz not null default now()
            )
        """))
        primo = conn.execute(text("select id from users order by id limit 1")).scalar()
        if primo is None and _ci_sono_dati(conn):
            nome = os.getenv("MONEY_FIRST_USER", "owner")
            primo = conn.execute(text(
                "insert into users (username, display_name, shares_totals) "
                "values (:u, :d, false) returning id"
            ), {"u": nome.lower(), "d": nome.capitalize()}).scalar()
        if primo is None:
            # Installazione nuova e vuota: non c'e' niente da attribuire e
            # nessuno a cui attribuirlo. Le colonne si aggiungono lo stesso.
            return _solo_schema(conn, esito)

        for tabella in PER_UTENTE:
            presente = conn.execute(text("""
                select 1 from information_schema.columns
                where table_name = :t and column_name = 'user_id'
            """), {"t": tabella}).scalar()
            if presente:
                continue
            conn.execute(text(f"alter table {tabella} add column user_id integer"))
            conn.execute(text(f"update {tabella} set user_id = :id"), {"id": primo})
            conn.execute(text(f"alter table {tabella} alter column user_id set not null"))
            conn.execute(text(f"alter table {tabella} alter column user_id set default {primo}"))
            conn.execute(text(f"create index if not exists ix_{tabella}_user_id on {tabella} (user_id)"))
            esito["colonne"].append(tabella)

        for tabella, vincolo, colonne in VINCOLI:
            gia = conn.execute(text("""
                select 1 from pg_constraint where conname = :n
            """), {"n": f"{vincolo}_utente"}).scalar()
            if gia:
                continue
            conn.execute(text(f"alter table {tabella} drop constraint if exists {vincolo}"))
            conn.execute(text(
                f"alter table {tabella} add constraint {vincolo}_utente unique ({', '.join(colonne)})"
            ))
            esito["vincoli"].append(tabella)

        for tabella, indice, colonne, _ in INDICI_UNICI:
            gia = conn.execute(text("select 1 from pg_indexes where indexname = :n"),
                               {"n": f"{indice}_utente"}).scalar()
            if gia:
                continue
            conn.execute(text(f"drop index if exists {indice}"))
            conn.execute(text(
                f"create unique index {indice}_utente on {tabella} ({', '.join(colonne)})"
            ))
            esito["indici"].append(tabella)

    esito["applicata"] = bool(esito["colonne"] or esito["vincoli"] or esito["indici"])
    esito["utente"] = primo
    if esito["applicata"]:
        logger.info("passaggio a piu' utenti: %s", esito)
    return esito


APP_ROLE = os.getenv("MONEY_DB_APP_ROLE", "money_app")


def accendi_isolamento(admin_engine: Engine, *, password: str) -> dict:
    """Seconda mossa: e' il database a separare i dati, non le query.

    Ogni tabella per-utente prende una politica di riga legata a
    ``app.user_id``, il valore che la sessione annuncia a ogni transazione. Una
    query che dimentica il filtro non vede i dati altrui: non trova nulla.

    Serve un ruolo dedicato perche' Postgres ignora le politiche per i
    superutenti, e le ignora anche per il proprietario delle tabelle a meno di
    imporgliele con FORCE. Qui si fanno entrambe le cose.
    """
    if not _e_postgres(admin_engine):
        return {"applicata": False, "motivo": "non postgresql"}

    esito: dict = {"tabelle": [], "ruolo": APP_ROLE}
    with admin_engine.begin() as conn:
        # Postgres non accetta parametri nei comandi di definizione: la
        # password va scritta nel testo, quindi si raddoppiano gli apici.
        literale = "'" + password.replace("'", "''") + "'"
        esiste = conn.execute(text("select 1 from pg_roles where rolname = :r"), {"r": APP_ROLE}).scalar()
        if not esiste:
            conn.execute(text(f"create role {APP_ROLE} login password {literale}"))
        else:
            conn.execute(text(f"alter role {APP_ROLE} with login password {literale}"))
        conn.execute(text(f"alter role {APP_ROLE} nosuperuser nocreatedb nocreaterole"))

        database = conn.execute(text("select current_database()")).scalar()
        conn.execute(text(f'grant connect on database "{database}" to {APP_ROLE}'))
        conn.execute(text(f"grant usage on schema public to {APP_ROLE}"))
        conn.execute(text(f"grant select, insert, update, delete on all tables in schema public to {APP_ROLE}"))
        conn.execute(text(f"grant usage, select on all sequences in schema public to {APP_ROLE}"))
        # Anche per le tabelle che verranno create in futuro.
        conn.execute(text(
            f"alter default privileges in schema public grant select, insert, update, delete on tables to {APP_ROLE}"))
        conn.execute(text(
            f"alter default privileges in schema public grant usage, select on sequences to {APP_ROLE}"))

        for tabella in PER_UTENTE:
            conn.execute(text(f"alter table {tabella} enable row level security"))
            # FORCE vale anche per il proprietario: senza, chi possiede la
            # tabella continuerebbe a vedere tutto e la protezione sarebbe finta.
            conn.execute(text(f"alter table {tabella} force row level security"))
            conn.execute(text(f"drop policy if exists isolamento_utente on {tabella}"))
            # current_setting con true non solleva errore se il valore manca:
            # in quel caso il confronto e' nullo e la politica nega tutto.
            conn.execute(text(f"""
                create policy isolamento_utente on {tabella}
                using (user_id = nullif(current_setting('app.user_id', true), '')::integer)
                with check (user_id = nullif(current_setting('app.user_id', true), '')::integer)
            """))
            esito["tabelle"].append(tabella)

    logger.info("isolamento acceso su %d tabelle", len(esito["tabelle"]))
    esito["applicata"] = True
    return esito
