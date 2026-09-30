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
from decimal import Decimal

from sqlalchemy import text, inspect
from sqlalchemy.engine import Engine

from .core_routes import BASE_CURRENCY, fx_symbols

logger = logging.getLogger("money.migrations")


def tracked_changes(engine: Engine) -> None:
    """Applies tracked schema changes without rewriting movements or balances.

    L'unica eccezione sono i giroconti a una gamba sola che l'import di un backup
    scriveva con la stessa cifra da tutte e due le parti: li' la gamba che manca
    si ricava al cambio del giorno, e il saldo del conto d'arrivo non si muove.
    """
    with engine.begin() as conn:
        tx_cols = {c["name"] for c in inspect(conn).get_columns("transactions")}
        if "counts_in_budget" not in tx_cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN counts_in_budget BOOLEAN NOT NULL DEFAULT true"))
        if "incomplete_accepted" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN incomplete_accepted BOOLEAN NOT NULL DEFAULT false"))
        if "destination_amount" not in tx_cols:
            # I movimenti che c'erano muovono lo stesso importo da tutte e due le
            # parti: la colonna vuota vuol dire esattamente questo, quindi nessun
            # saldo gia' calcolato si muove.
            conn.execute(text("ALTER TABLE transactions ADD COLUMN destination_amount NUMERIC(16, 2)"))
        if "currency" not in tx_cols:
            # I movimenti che c'erano erano tutti in euro: la loro valuta e'
            # l'euro, e i totali di quei mesi non si muovono di un centesimo.
            conn.execute(text("ALTER TABLE transactions ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'EUR'"))
        if "refund_of_id" not in tx_cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN refund_of_id INTEGER"))
            conn.execute(text("CREATE INDEX ix_transactions_refund_of_id ON transactions (refund_of_id)"))
        # I movimenti che c'erano prima restano senza lotto: nessuno li ha
        # importati da un file, e indovinare quale import li abbia portati
        # sarebbe una ricostruzione inventata. Vuoto e' la risposta giusta.
        if "import_batch_id" not in tx_cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN import_batch_id INTEGER"))
            conn.execute(text("CREATE INDEX ix_transactions_import_batch_id ON transactions (import_batch_id)"))
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
        if "currency" not in account_cols:
            # I conti che c'erano erano tutti in euro: il default li lascia
            # esattamente com'erano, e nessun numero gia' mostrato si muove.
            conn.execute(text("ALTER TABLE accounts ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'EUR'"))
        if inspect(conn).has_table("budget_plans"):
            budget_cols = {c["name"] for c in inspect(conn).get_columns("budget_plans")}
            if "currency" not in budget_cols:
                # I piani che c'erano erano tutti in euro, perche' un piano in
                # un'altra valuta non si poteva nemmeno scrivere: il default li
                # lascia com'erano e i budget gia' mostrati non si muovono.
                conn.execute(text("ALTER TABLE budget_plans ADD COLUMN currency VARCHAR(3) "
                                  "NOT NULL DEFAULT 'EUR'"))
            _vincolo_budget_per_valuta(conn)
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
        # Lo storico degli import: prima lo scriveva solo il ripristino di un
        # backup, e nessuno lo leggeva. Le 27 righe che esistono sono tutte
        # ripristini, quindi nascono 'interchange' - il valore che descrive
        # quello che sono davvero - e non un valore nuovo da indovinare.
        if inspect(conn).has_table("import_batches"):
            batch_cols = {c["name"] for c in inspect(conn).get_columns("import_batches")}
            if "kind" not in batch_cols:
                conn.execute(text("ALTER TABLE import_batches "
                                  "ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'interchange'"))
            if "rows_accepted" not in batch_cols:
                conn.execute(text("ALTER TABLE import_batches "
                                  "ADD COLUMN rows_accepted INTEGER NOT NULL DEFAULT 0"))
            if "rows_rejected" not in batch_cols:
                conn.execute(text("ALTER TABLE import_batches "
                                  "ADD COLUMN rows_rejected INTEGER NOT NULL DEFAULT 0"))
            if "rejected_reasons" not in batch_cols:
                conn.execute(text("ALTER TABLE import_batches ADD COLUMN rejected_reasons TEXT"))
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
            # La colonna di testo `category` non c'e' piu' su un database nato
            # dopo l'albero: li' la sentinella "_" non serve, e nominarla
            # farebbe fallire l'avvio. Su un database vecchio invece c'e'
            # ancora, e il movimento deve restare senza categoria come prima.
            testo_categoria = (", category='_'" if "category" in _colonne_di(conn, "transactions") else "")
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
                    conn.execute(text("UPDATE transactions SET transaction_type='Debt'"
                                      + testo_categoria + ", amount=:amount WHERE id=:id"),
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
                "UPDATE transactions SET transaction_type='Transfers'" + testo_categoria + " "
                "WHERE id IN (SELECT transaction_id FROM liability_transaction_details WHERE kind='refund')"))
            conn.execute(text("DELETE FROM liability_transaction_details WHERE kind='refund'"))

        # --- Le categorie diventano un albero (PIANO-B3) --------------------
        # Finora una categoria non era una riga da nessuna parte: era una
        # stringa scritta dentro movimenti, budget e regole. Qui nascono le
        # righe, e i riferimenti passano dall'id. Gira a ogni avvio, quindi
        # ogni passo guarda prima se l'ha gia' fatto.
        if inspect(conn).has_table("categories"):
            # Le colonne dei riferimenti nascono qui: `create_all` crea le
            # tabelle nuove ma non tocca quelle che esistono gia', quindi su un
            # database di prima la colonna non c'e'. Senza questo passo il
            # collegamento dei nomi agli id non avrebbe dove scrivere, e
            # l'albero resterebbe vuoto con movimenti e budget scollegati.
            for tabella in ("transactions", "budget_plans", "categorization_rules"):
                colonne = _colonne_di(conn, tabella)
                if colonne and "category_id" not in colonne:
                    conn.execute(text(f"ALTER TABLE {tabella} ADD COLUMN category_id INTEGER "
                                      "REFERENCES categories(id)"))
            _albero_delle_categorie(conn)

            # Le due colonne della B3b. `create_all` crea le tabelle nuove ma
            # non tocca quelle che esistono gia', quindi su un database di
            # prima non ci sono: senza questo passo l'app leggerebbe colonne
            # che il database non ha. `scope` ha un valore di partenza perche'
            # le categorie di prima erano tutte di spesa tranne quelle
            # dell'albero entrate, che il passo sotto rimette a posto; le righe
            # gia' scritte non possono restare senza.
            colonne_cat = _colonne_di(conn, "categories")
            if "scope" not in colonne_cat:
                conn.execute(text("ALTER TABLE categories ADD COLUMN scope VARCHAR(10) NOT NULL "
                                  "DEFAULT 'expense'"))
            if "essenziale" not in colonne_cat:
                conn.execute(text("ALTER TABLE categories ADD COLUMN essenziale VARCHAR(10)"))

            _due_alberi(conn)

            # Le colonne di testo hanno fatto il loro giro: i nomi sono
            # diventati righe, i riferimenti puntano agli id, e l'app non le
            # legge piu'. Una copia che invecchia e' peggio di nessuna copia -
            # un nome cambiato solo di qua sembrerebbe ancora vero - e finche'
            # restano, chi legge il database deve indovinare quale delle due
            # colonne comanda.
            #
            # Il vincolo unico dei budget nominava ``category``: Postgres se lo
            # porta via da solo con la colonna, e al suo posto c'e' quello su
            # ``category_id`` messo qui sopra.
            for tabella, colonne in (("transactions", ("category",)),
                                     ("budget_plans", ("category", "category_group")),
                                     ("categorization_rules", ("category",))):
                presenti = _colonne_di(conn, tabella)
                for colonna in colonne:
                    if colonna in presenti:
                        conn.execute(text(f"ALTER TABLE {tabella} DROP COLUMN {colonna}"))

        # --- La gamba mancante di un giroconto ------------------------------
        # L'import di un backup leggeva un trasferimento fra due valute diverse
        # da una gamba sola - quella che il file registra - e scriveva la stessa
        # cifra da tutte e due le parti. Cosi' il conto di passaggio leggeva una
        # somma di unita' diverse: franchi contati come euro. Qui si ricava la
        # gamba che il file non aveva, al cambio del giorno del movimento, come
        # fa l'anteprima (``_cambia``, in ``main.py``: stessa aritmetica, stesso
        # cambio - se una delle due cambia, l'altra mente).
        #
        # Tocca solo le righe che l'import di un backup ha scritto con una gamba
        # sola. ``source_row`` e' la firma di quell'import: un movimento a mano,
        # un estratto conto o un ripristino non ce l'hanno, e senza quella prova
        # una riga di uscita sola registrata apposta verrebbe "corretta"
        # inventando un numero che nessuno ha mai scritto.
        #
        # Idempotente come il resto del file: dopo la prima volta
        # ``destination_amount`` non e' piu' vuoto e non trova piu' niente. Una
        # riga a cui manca ancora il cambio si salta, e si riprende al primo
        # avvio in cui il cambio c'e'.
        colonne_tx = _colonne_di(conn, "transactions")
        if ({"user_id", "source_row", "destination_amount"} <= colonne_tx
                and inspect(conn).has_table("market_prices")):
            una_gamba = conn.execute(text(
                "SELECT t.id, t.occurred_on, t.amount, "
                "COALESCE(part.currency, :base) AS valuta_partenza, "
                "COALESCE(dest.currency, :base) AS valuta_arrivo "
                "FROM transactions t "
                "JOIN accounts part ON part.name = t.account_name AND part.user_id = t.user_id "
                "JOIN accounts dest ON dest.name = t.destination_name AND dest.user_id = t.user_id "
                "WHERE t.transaction_type = 'Transfers' "
                "AND t.destination_amount IS NULL AND t.source_row IS NOT NULL "
                "AND COALESCE(part.currency, :base) <> COALESCE(dest.currency, :base)"
            ), {"base": BASE_CURRENCY}).mappings().all()
            corrette = 0
            for riga in una_gamba:
                if riga["amount"] is None:
                    continue
                partenza = _cambio_del_giorno(conn, riga["valuta_partenza"], riga["occurred_on"])
                arrivo = _cambio_del_giorno(conn, riga["valuta_arrivo"], riga["occurred_on"])
                if not partenza or not arrivo:
                    continue
                # La cifra del file e' quella del conto su cui il file l'aveva
                # registrata - per queste righe, l'arrivo - e li' resta. La
                # partenza prende il suo valore in euro, arrotondato ai
                # centesimi come ogni importo dell'app.
                cifra = Decimal(str(riga["amount"]))
                # I due importi viaggiano come testo e il database li converte
                # nella sua cifra decimale: Decimal non lo accetta ogni motore
                # come parametro, e la colonna e' comunque decimal(16,2).
                conn.execute(text("UPDATE transactions SET destination_amount = CAST(:cifra AS DECIMAL(16,2)), "
                                  "amount = CAST(:nuovo AS DECIMAL(16,2)) WHERE id = :id"),
                             {"id": riga["id"], "cifra": str(cifra),
                              "nuovo": str((cifra / arrivo * partenza).quantize(Decimal("0.01")))})
                corrette += 1
            if corrette:
                logger.info("Giroconti a una gamba corretti: %s", corrette)

# --- I due alberi (PIANO-B3b) ------------------------------------------
# A cosa servono i soldi, e per quali soldi. La voce della mappa e' il nome che
# la categoria ha gia' ("Travels" e non "Travel", "Study" e non "Education"):
# il nome non e' un'etichetta da riscrivere, e' la chiave con cui l'import di
# un estratto conto e l'utente ritrovano la categoria, quindi le voci che
# esistono restano quelle e le altre nascono accanto.
#
# Le sottocategorie vuote si creano lo stesso: costano niente e dicono dove
# mettere le cose quando capiteranno.
ALBERO_SPESE: dict[str, list[str]] = {
    "Housing": ["Rent/Mortgage", "Home Insurance", "Maintenance & Repairs", "Furnishing", "Utilities"],
    "Groceries": [],
    "Food & Dining": ["Eating out", "Restaurants", "Coffee Shops", "Food Delivery", "Bars & Alcohol"],
    "Transportation": ["Car", "Gas & Fuel", "Parking", "Public Transit", "Rideshare & Taxi", "Auto Insurance"],
    "Entertainment": ["Leisure", "Cardmarket", "Sports", "Games", "Blog", "Streaming Services", "Movies & Events",
                      "Hobbies"],
    "Health & Wellness": ["Health", "Insurance", "Medical", "Pharmacy", "Dental", "Vision", "Gym & Fitness"],
    "Bills & Utilities": ["Recurrings", "Cellular", "Phone", "Internet", "Subscriptions", "Software & Services"],
    "Shopping": ["Clothes", "Electronics", "Home Goods", "Online Shopping"],
    "Fees & Charges": ["Commissions", "Bank Fees", "ATM Fees", "Interest Charges", "Late Fees"],
    "Gifts & Donations": ["Gifts", "Charity"],
    "Travels": [],
    "Study": [],
    "Personal Care": [],
    "Other Expenses": ["Other", "Da categorizzare"],
}

ALBERO_ENTRATE: dict[str, list[str]] = {
    "Employment": ["Salary", "Bonus", "Commission", "Buoni pasto", "Income"],
    "Self-Employment": ["Freelance", "Business Income"],
    "Investment Income": ["Dividends", "Interest", "Rental Income", "Capital Gains"],
    "Other Income": ["Gifts Received", "Refunds", "Reimbursements", "Tax Refund", "Others"],
}

# Le due associazioni dichiarate: sono le uniche che si sanno, e le uniche che
# questa migrazione scrive. Un bisogno o un piacere dedotto sarebbe un giudizio
# dell'utente messo in bocca all'utente.
DICHIARATE = (("Housing", "needs"), ("Clothes", "wants"))

# L'unica rietichettatura: un'entrata non puo' stare su una categoria di spesa.
# Housing e Utilities hanno contributi e rimborsi per la casa, e Da
# categorizzare e' il segnaposto dove sono finiti i movimenti che nessuno ha
# ancora guardato. Tutto il resto delle entrate resta dov'e'.
SPOSTAMENTI = (("Housing", "Rental Income"), ("Utilities", "Rental Income"),
               ("Da categorizzare", "Other Income"))


def _categoria_per_nome(conn, user_id: int, nome: str) -> int | None:
    """L'id della categoria con quel nome, a qualunque livello stia.

    La radice vince sul figlio omonimo, e fra pari vince la riga nata prima:
    cosi' due giri della migrazione trovano la stessa riga e non ne creano una
    seconda. Il nome si confronta senza badare alle maiuscole, come dappertutto.
    """
    return conn.execute(text(
        "SELECT id FROM categories WHERE user_id = :u AND lower(name) = lower(:n) "
        "ORDER BY (parent_id IS NOT NULL), id LIMIT 1"), {"u": user_id, "n": nome}).scalar()


def _due_alberi(conn) -> None:
    """Rimette le categorie nei due alberi di spese ed entrate.

    Tre passi, in quest'ordine, per ogni persona: si disfano i bisogni-padri
    (erano due dimensioni schiacciate su un asse solo), si piantano gli alberi,
    e si spostano le entrate rimaste su categorie di spesa. Gira a ogni avvio,
    quindi ogni passo guarda prima cosa c'e' gia'.
    """
    if "user_id" not in _colonne_di(conn, "categories"):
        return
    for (user_id,) in conn.execute(text("SELECT DISTINCT user_id FROM categories")).all():
        _disciogli_bisogni(conn, user_id)
        for albero, verso in ((ALBERO_SPESE, "expense"), (ALBERO_ENTRATE, "income")):
            _pianta(conn, user_id, albero, verso)
        # Un bisogno o un piacere su una categoria di entrate non vuol dire
        # niente: se una categoria e' finita fra le entrate con un valore
        # addosso, quel valore resta orfano e va tolto.
        conn.execute(text("UPDATE categories SET essenziale = NULL WHERE user_id = :u "
                          "AND scope = 'income' AND essenziale IS NOT NULL"), {"u": user_id})
        _sposta_le_entrate(conn, user_id)


def _disciogli_bisogni(conn, user_id: int) -> None:
    """Disfa i bisogni-padri: le due categorie tornano radici, le radici spariscono.

    Quello che dicevano non si butta: era l'unica cosa che si sapeva di quelle
    due categorie, e diventa il valore dichiarato di Housing e Clothes. Se una
    delle due lo dichiara gia' per conto suo - l'utente l'ha scelto - vince
    quello.

    Le radici Needs e Wants si cancellano solo se sono rimaste vuote: se
    qualcuno ci ha attaccato altro, resta dov'e' invece di sparire con quello
    che conteneva. Una categoria che qualcuno usa non si cancella mai.
    """
    for nome, gruppo in DICHIARATE:
        id_categoria = _categoria_per_nome(conn, user_id, nome)
        if id_categoria is None:
            continue
        riga = conn.execute(text("SELECT parent_id FROM categories WHERE id = :i"),
                            {"i": id_categoria}).one()
        conn.execute(text("UPDATE categories SET essenziale = coalesce(essenziale, :g) WHERE id = :i"),
                     {"g": gruppo, "i": id_categoria})
        padre = (conn.execute(text("SELECT id, name FROM categories WHERE id = :i"), {"i": riga[0]})
                 .one_or_none() if riga[0] else None)
        if padre is not None and padre[1].strip().casefold() in ("needs", "wants"):
            conn.execute(text("UPDATE categories SET parent_id = NULL WHERE id = :i"), {"i": id_categoria})
    for radice in ("Needs", "Wants"):
        id_radice = _categoria_per_nome(conn, user_id, radice)
        if id_radice is None or _ha_un_padre(conn, id_radice) is not None or _in_uso(conn, id_radice):
            continue
        conn.execute(text("DELETE FROM categories WHERE id = :i"), {"i": id_radice})


def _in_uso(conn, category_id: int) -> bool:
    """Se qualcosa pende da quella categoria: figli, movimenti, budget, regole."""
    for tabella, colonna in (("categories", "parent_id"), ("transactions", "category_id"),
                             ("budget_plans", "category_id"), ("categorization_rules", "category_id")):
        if colonna in _colonne_di(conn, tabella):
            if conn.execute(text(f"SELECT 1 FROM {tabella} WHERE {colonna} = :i LIMIT 1"),
                            {"i": category_id}).scalar():
                return True
    return False


def _ha_figli(conn, category_id: int) -> bool:
    """Se quella categoria ha figli suoi: con un padre in piu' sarebbe un terzo livello."""
    return bool(conn.execute(text("SELECT 1 FROM categories WHERE parent_id = :i LIMIT 1"),
                             {"i": category_id}).scalar())


def _pianta(conn, user_id: int, albero: dict[str, list[str]], verso: str) -> None:
    """Crea le voci mancanti di un albero e aggancia quelle che ci sono gia'.

    Una categoria dell'utente che ha lo stesso nome di una voce dell'albero
    **e' quella voce**: non se ne crea una seconda, e guadagna soltanto il
    padre e il verso. La gerarchia scritta a mano dall'utente non si tocca:
    una voce che sta sotto un altro padre resta dove sta, perche' spostarla
    sarebbe riscrivere una scelta che non e' nostra.
    """
    for radice, figli in albero.items():
        id_radice = _categoria_per_nome(conn, user_id, radice)
        if id_radice is None:
            id_radice = conn.execute(text(
                "INSERT INTO categories (user_id, parent_id, name, position, active, scope) "
                "VALUES (:u, NULL, :n, 0, true, :v) RETURNING id"),
                {"u": user_id, "n": radice, "v": verso}).scalar()
        else:
            conn.execute(text("UPDATE categories SET scope = :v WHERE id = :i"), {"v": verso, "i": id_radice})
        for posizione, figlio in enumerate(figli, start=1):
            id_figlio = _categoria_per_nome(conn, user_id, figlio)
            if id_figlio is None:
                conn.execute(text(
                    "INSERT INTO categories (user_id, parent_id, name, position, active, scope) "
                    "VALUES (:u, :p, :n, :o, true, :v)"),
                    {"u": user_id, "p": id_radice, "n": figlio, "o": posizione, "v": verso})
                continue
            if id_figlio == id_radice:
                continue
            conn.execute(text("UPDATE categories SET scope = :v WHERE id = :i"), {"v": verso, "i": id_figlio})
            # Solo chi non ha figli propri si sposta: sotto di se' avrebbe un
            # terzo livello, e l'albero ne ha due. Se ha figli resta dov'e':
            # un ramo dell'utente vale piu' di una voce dell'elenco. I
            # movimenti invece non fermano niente: una radice tiene i suoi e
            # puo' avere figli, e' la regola che questa specifica ripristina.
            if _ha_figli(conn, id_figlio) or _ha_un_padre(conn, id_figlio) == id_radice:
                continue
            conn.execute(text("UPDATE categories SET parent_id = :p WHERE id = :i"),
                         {"p": id_radice, "i": id_figlio})


def _ha_un_padre(conn, category_id: int) -> int | None:
    return conn.execute(text("SELECT parent_id FROM categories WHERE id = :i"),
                        {"i": category_id}).scalar()


def _sposta_le_entrate(conn, user_id: int) -> None:
    """Le entrate che stavano su una categoria di spesa vanno dove si dice qui.

    Sono quattordici movimenti in tutto, piu' quelli del segnaposto: e' l'unica
    cosa che cambia categoria in questa migrazione. Il resto delle entrate resta
    dov'e', anche dove sembrerebbe fuori posto - smistarle sarebbe indovinare
    cosa l'utente voleva dire.
    """
    for partenza, arrivo in SPOSTAMENTI:
        id_partenza = _categoria_per_nome(conn, user_id, partenza)
        id_arrivo = _categoria_per_nome(conn, user_id, arrivo)
        if id_partenza is None or id_arrivo is None or id_partenza == id_arrivo:
            continue
        conn.execute(text("UPDATE transactions SET category_id = :a WHERE user_id = :u "
                          "AND transaction_type = 'Income' AND category_id = :p"),
                     {"a": id_arrivo, "p": id_partenza, "u": user_id})


def _cambio_del_giorno(conn, valuta: str, giorno) -> Decimal | None:
    """Quante unita' della valuta vale 1 euro a quella data, lette dal listino.

    E' ``_rate_on`` di ``core_routes`` scritto in SQL: l'ultimo prezzo noto non
    successivo al giorno, che e' il cambio con cui l'app converte quel movimento
    nei suoi totali. Qui la riga che serve la sceglie il database, perche' la
    migrazione non passa dall'ORM e non ha una serie da caricare in memoria.
    ``None`` quando il cambio non c'e': chi chiama salta la riga invece di
    contare un franco come un euro.
    """
    codice = str(valuta or BASE_CURRENCY).strip().upper()
    if codice == BASE_CURRENCY:
        return Decimal(1)
    for symbol, inverted in fx_symbols(codice):
        prezzo = conn.execute(text(
            "SELECT price FROM market_prices WHERE symbol = :symbol AND price > 0 "
            "AND observed_on <= :giorno ORDER BY observed_on DESC LIMIT 1"),
            {"symbol": symbol, "giorno": giorno}).scalar()
        if prezzo is not None and Decimal(str(prezzo)) > 0:
            cambio = Decimal(str(prezzo))
            # Le cripto si quotano al contrario ("euro per bitcoin"): il numero
            # da usare e' l'inverso, come in `fx_rates_by_month`.
            return (Decimal(1) / cambio) if inverted else cambio
    return None


def _colonne_di(conn, tabella: str) -> set[str]:
    """Le colonne di una tabella, o niente se la tabella non c'e'.

    Serve alle migrazioni che devono leggere colonne poi rimosse: su un
    database creato dopo la rimozione quelle colonne non esistono, e il passo
    che le legge si salta invece di far fallire tutto l'avvio.
    """
    return ({colonna["name"] for colonna in inspect(conn).get_columns(tabella)}
            if inspect(conn).has_table(tabella) else set())


def _vincolo_budget_per_valuta(conn) -> None:
    """Un piano per categoria, periodo, tipo **e valuta**: il vincolo di prima
    con la valuta dentro.

    Il vincolo vecchio non si affianca, si toglie: vieta di avere insieme il
    piano in euro e quello in franchi della stessa categoria, che e' tutto
    quello che questa modifica serve a fare. Se ne sopravvivesse uno,
    l'inserimento della seconda valuta morirebbe con un 409 che dall'interfaccia
    non si capisce.

    Si riconosce **per colonne e non per nome**, e non e' pignoleria: su un
    database nato con ``create_all`` il vincolo si chiama
    ``budget_plans_user_id_period_budget_type_category_id_key``, su uno migrato
    ``budget_plans_period_budget_type_category_id_key``, e la migrazione che
    l'aveva messo controllava solo il secondo nome. Su un database possono
    quindi coesistere tutti e due, e cercarne uno solo lascerebbe vivo l'altro.

    Su SQLite non si fa niente: i vincoli non si alterano, e i test creano le
    tabelle con ``create_all``, che il nome nuovo ce l'ha gia'.
    """
    if conn.dialect.name != "postgresql":
        return
    if "currency" not in _colonne_di(conn, "budget_plans"):
        return
    nuovo = "budget_plans_period_type_category_currency_key"
    vecchie = {"user_id", "period", "budget_type", "category_id"}
    vincoli = inspect(conn).get_unique_constraints("budget_plans")
    for vincolo in vincoli:
        if {colonna.strip('"') for colonna in vincolo["column_names"]} == vecchie:
            conn.execute(text(f'ALTER TABLE budget_plans DROP CONSTRAINT IF EXISTS "{vincolo["name"]}"'))
    if nuovo not in {vincolo["name"] for vincolo in vincoli}:
        conn.execute(text(f"ALTER TABLE budget_plans ADD CONSTRAINT {nuovo} "
                          "UNIQUE (user_id, period, budget_type, category_id, currency)"))


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

    # Un nome che esiste gia' non si duplica, a qualunque livello stia: e' anche
    # il modo in cui questo blocco resta innocuo se gira una seconda volta. La
    # ricerca guarda anche i figli perche' le tre categorie dichiarate qui sotto
    # sono gia' rami al secondo giro: cercando solo fra le radici ne
    # ricomparirebbe una copia, e lo spostamento sotto il padre sbatterebbe
    # contro il vincolo di unicita' del ramo.
    for user_id, nome in conn.execute(text(" UNION ".join(letture))).all():
        if conn.execute(text("SELECT 1 FROM categories WHERE user_id = :u AND lower(name) = lower(:n)"),
                        {"u": user_id, "n": nome}).scalar():
            continue
        conn.execute(text("INSERT INTO categories (user_id, parent_id, name, position, active) "
                          "VALUES (:u, NULL, :n, 0, true)"), {"u": user_id, "n": nome})

    # I riferimenti: dal nome all'id, solo dove l'id non c'e' gia'. La categoria
    # si cerca per nome, quindi un movimento scritto "Alimentari" continua a
    # puntare alla stessa riga anche dopo che l'ha rinominata qualcun altro.
    #
    # Non si cerca solo fra le radici: le tre associazioni dichiarate qui sotto
    # spostano delle categorie sotto un padre, e da li' in poi una ricerca fra
    # le sole radici non le troverebbe piu' - un secondo giro lascerebbe
    # scollegati proprio i movimenti di quelle tre. Vince l'id piu' basso, che
    # e' la riga nata per prima: cosi' il risultato e' lo stesso a ogni giro e
    # non dipende da quale riga il database legge per prima.
    for tabella in ("transactions", "budget_plans", "categorization_rules"):
        colonne = _colonne_di(conn, tabella)
        if "category" not in colonne or "category_id" not in colonne:
            continue
        conn.execute(text(
            f"UPDATE {tabella} AS t SET category_id = ("
            "SELECT c.id FROM categories c WHERE c.user_id = t.user_id "
            "AND lower(c.name) = lower(trim(t.category)) ORDER BY c.id LIMIT 1) "
            "WHERE t.category_id IS NULL AND EXISTS ("
            "SELECT 1 FROM categories c WHERE c.user_id = t.user_id "
            "AND lower(c.name) = lower(trim(t.category)))"))
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
    # colonna nuova: se ne occupa ``_vincolo_budget_per_valuta``, che gira a
    # ogni avvio da ``tracked_changes``. Non si rimette qui: questa funzione
    # passa solo quando il database ha ancora le colonne di testo, e su un
    # database gia' migrato il vincolo non verrebbe mai toccato.


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
    # Le tappe di un obiettivo sono di chi ha scritto l'obiettivo: senza
    # l'isolamento le tappe di una persona comparirebbero sotto gli obiettivi
    # di un'altra, che hanno lo stesso nome e si leggerebbero come sue.
    "goal_milestones",
    # Lo storico degli import e' la storia di chi ha importato: senza
    # l'isolamento, il ripristino di una persona comparirebbe nell'elenco di
    # un'altra. Stava fra le condivise perche' nessuno lo leggeva: appena
    # diventa una pagina, "condiviso" non vuol dire piu' niente.
    "import_batches",
    # I modelli di mappatura sono di chi li ha salvati: senza l'isolamento, il
    # modello di una persona comparirebbe nell'elenco di un'altra e le sue
    # colonne finirebbero in un import che non e' il suo.
    "import_templates",
]

# Tabelle condivise di proposito: una quotazione e il profilo di uno strumento
# sono gli stessi per tutti, e scaricarli una volta sola e' un vantaggio.
CONDIVISE = ["market_prices", "instrument_profiles"]

# I vincoli di unicita' che devono valere per utente e non per tutti: senza
# questo, due persone non potrebbero avere entrambe un conto "Banca".
VINCOLI = [
    ("accounts", "accounts_source_group_name_key", ["user_id", "source_group", "name"]),
    ("lookup_options", "lookup_options_option_group_value_key", ["user_id", "option_group", "value"]),
]
# Il terzo vincolo era quello dei budget, uno per categoria e periodo: nominava
# la colonna di testo ``category``, che l'ultimo passo dell'albero toglie. Il
# vincolo equivalente su ``category_id`` e valuta lo mette
# ``_vincolo_budget_per_valuta``, e su un database nato col modello attuale
# l'ha gia' messo ``create_all``.

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
