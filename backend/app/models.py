from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, current_user_id


class User(Base):
    """Una persona che usa l'app.

    I dati di ognuno restano separati: la separazione non e' affidata al fatto
    che le query ricordino di filtrare, ma alle politiche di riga del database
    (vedi ``current_user_id`` e le policy in ``migrations``).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Chi lo attiva lascia vedere agli altri i propri totali, mai il dettaglio.
    shares_totals: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """Un dispositivo collegato.

    Sta sul server e non dentro un gettone firmato: cancellare la riga scollega
    davvero quel dispositivo, subito. Non ha politiche di riga perche' va letta
    prima di sapere chi sia l'utente - e' proprio la riga che lo dice.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DismissedNotification(Base):
    """Un avviso che qualcuno ha letto e messo via.

    Si conserva solo la chiave: l'avviso in se' non viene salvato, viene
    ricalcolato ogni volta dallo stato dell'app. Cosi' non esistono avvisi
    "vecchi" da tenere allineati con la realta', e uno chiuso non ritorna
    perche' la sua chiave e' gia' qui.
    """

    __tablename__ = "dismissed_notifications"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    key: Mapped[str] = mapped_column(String(255), index=True)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255))
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    transaction_count: Mapped[int] = mapped_column(default=0)
    account_count: Mapped[int] = mapped_column(default=0)
    budget_count: Mapped[int] = mapped_column(default=0)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "source_group", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    source_group: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    current_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    # Un conto puo' tracciare un accantonamento invece che denaro vero: i soldi
    # sono gia' nei conti dove stanno fisicamente, e questo ne segna solo la
    # destinazione. Contarlo nel patrimonio li conterebbe due volte.
    counts_in_net_worth: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Casa, auto, quote non quotate: il loro valore non si deduce da movimenti e
    # prezzi, lo si sa e basta. Solo questi conti mostrano lo storico delle
    # valutazioni e chiedono di essere aggiornati.
    needs_manual_valuation: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    is_liquid: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Un conto broker e' il posto dove stanno i titoli. Serve a dire dove puo'
    # puntare un movimento di tipo Investment: e' un ruolo, e va dichiarato
    # perche' va validato in scrittura, prima che esista qualunque
    # collegamento al ledger. Quanto vale resta dedotto dai collegamenti:
    # marcarlo broker non gli attribuisce niente.
    is_broker: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class AccountValuation(Base):
    """Quanto valeva un conto a una certa data, scritto a mano.

    Serve solo ai conti il cui valore non e' ricavabile da movimenti e prezzi.
    Per tutti gli altri il valore si calcola, e salvarne una copia vorrebbe dire
    avere due numeri e nessun arbitro fra i due.

    Esiste perche' l'alternativa - alzare `starting_balance` quando la casa vale
    di piu' - riscrive la storia: il patrimonio di due anni fa cambierebbe
    insieme a quello di oggi. Una valutazione ha una data, quindi vale da quel
    giorno in poi e non prima.
    """

    __tablename__ = "account_valuations"
    __table_args__ = (UniqueConstraint("user_id", "account_id", "observed_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    account_id: Mapped[int] = mapped_column(index=True)
    observed_on: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    notes: Mapped[str | None] = mapped_column(Text)


class LiabilityProfile(Base):
    """Condizioni contrattuali di un conto passivo; il residuo resta quello del conto."""

    __tablename__ = "liability_profiles"
    __table_args__ = (UniqueConstraint("user_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    account_id: Mapped[int] = mapped_column(index=True)
    debt_type: Mapped[str] = mapped_column(String(40), default="other")
    original_principal: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)
    rate_type: Mapped[str] = mapped_column(String(20), default="fixed")
    payment_frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    payment_structure: Mapped[str] = mapped_column(String(20), default="amortizing")
    start_date: Mapped[date] = mapped_column(Date)
    repayment_start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    planned_drawdowns: Mapped[str | None] = mapped_column(Text)
    # Durante il preammortamento gli interessi si pagano ogni scadenza oppure si
    # sommano al debito. I contratti fanno entrambe le cose e non si deduce da
    # nient'altro: va dichiarato.
    grace_interest: Mapped[str] = mapped_column(String(20), default="paid", server_default=text("'paid'"))
    # Due strumenti diversi sotto lo stesso tetto. Un prestito a rate ha un
    # capitale, un piano e delle scadenze; una linea di credito ha un saldo che
    # galleggia e interessi che si aggiungono al saldo. Applicare il piano di
    # ammortamento a uno scoperto produceva un "residuo teorico" che la banca
    # non ha mai chiesto, e un confronto col piano che non voleva dire niente.
    kind: Mapped[str] = mapped_column(String(20), default="term_loan", server_default=text("'term_loan'"))
    # Solo per le linee, e facoltativo: `None` vuol dire senza limite.
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[str | None] = mapped_column(Text)


class LiabilityTransactionDetail(Base):
    """Classificazione capitale/interessi di un singolo movimento Debt."""

    __tablename__ = "liability_transaction_details"
    __table_args__ = (UniqueConstraint("user_id", "transaction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    liability_account_id: Mapped[int] = mapped_column(index=True)
    transaction_id: Mapped[int] = mapped_column(index=True)
    refund_of_id: Mapped[int | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    interest_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    is_classified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class Category(Base):
    """Una voce dell'albero delle categorie: una radice o un figlio, niente altro.

    L'albero e' ``parent_id`` che punta a questa stessa tabella, come in
    Wealthfolio 3.8 (``taxonomy_categories``). Da li' **non** si copiano
    ``taxonomies`` e ``activity_taxonomy_assignments``: quelle esistono perche'
    la stessa spesa puo' stare in piu' tassonomie e in piu' categorie insieme.
    Qui la tassonomia e' una sola e la scelta e' **una categoria per
    movimento**, quindi una chiave esterna dice la stessa cosa di una tabella
    di assegnazioni con un vincolo di unicita' - con un join in meno in ogni
    somma per categoria. Chi confronta i due modelli e "corregge" verso le
    assegnazioni aggiunge quel join senza aggiungere nessuna informazione.

    Due livelli, non di piu': radice e figlio. Con ventisei categorie un terzo
    livello non serve a niente e raddoppia i casi da gestire in ogni somma.

    Il gruppo bisogni/piaceri non e' un campo: e' il nome della radice. Stava
    scritto sulle righe di budget, una per mese, ma descriveva la categoria -
    la spesa non e' un bisogno a gennaio e un piacere a febbraio.

    Il vincolo di unicita' vale per i figli; per le radici, dove ``parent_id``
    e' NULL, il database considera le righe diverse fra loro e il controllo lo
    fa la rotta, che risponde con un codice invece che con un errore di
    integrita'.
    """

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "parent_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    # L'ordine scelto a mano dall'utente, non alfabetico: l'elenco a tendina
    # deve somigliare a come ragiona lui, non a come ordina il database.
    position: Mapped[int] = mapped_column(default=0)
    # Spenta resta nell'elenco dei movimenti vecchi ma non si offre piu' da
    # scegliere: e' il modo di mettere via una categoria senza riscrivere la
    # storia di chi l'ha usata.
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class BudgetPlan(Base):
    __tablename__ = "budget_plans"
    __table_args__ = (UniqueConstraint("user_id", "period", "budget_type", "category_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    period: Mapped[date] = mapped_column(Date, index=True)
    budget_type: Mapped[str] = mapped_column(String(30), index=True)
    # Il gruppo non c'e' piu': era il nome della radice scritto su ogni riga di
    # budget, e la radice adesso si legge dall'albero.
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    source_row: Mapped[int | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    starting_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[date | None] = mapped_column(Date)
    # Da dove arriva il valore corrente: i movimenti taggati col nome del goal
    # (contributions), il patrimonio netto, o il valore di mercato del
    # portafoglio. Sono domande diverse e danno numeri diversi di proposito.
    kind: Mapped[str] = mapped_column(String(30), default="contributions")
    # Il conto verso cui l'obiettivo accumula, per i goal `contributions`.
    # Dichiararlo e' l'unico modo di sapere se un movimento taggato *aggiunge*
    # o *toglie*: senza, un prelievo dal salvadanaio farebbe salire il goal.
    # Vuoto = si somma e basta, com'era prima.
    target_account: Mapped[str | None] = mapped_column(String(255))


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    source_row: Mapped[int | None] = mapped_column(index=True)
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    effective_on: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(30), index=True)
    # La categoria e' un riferimento, non il suo nome scritto dentro: e' quello
    # che permette di rinominare "Alimentari" senza riscrivere quattromila
    # movimenti. NULL vuol dire "non ne ha" - un giroconto, per esempio.
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    account_type: Mapped[str | None] = mapped_column(String(80))
    account_name: Mapped[str | None] = mapped_column(String(255), index=True)
    destination_type: Mapped[str | None] = mapped_column(String(80))
    destination_name: Mapped[str | None] = mapped_column(String(255))
    goal: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[str | None] = mapped_column(Text)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # Ricorrenza
    recurrence_rule: Mapped[str | None] = mapped_column(String(255))
    recurrence_end_date: Mapped[date | None] = mapped_column(Date)
    is_recurring_template: Mapped[bool] = mapped_column(default=False)
    recurrence_parent_id: Mapped[int | None] = mapped_column(index=True)
    counts_in_budget: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    refund_of_id: Mapped[int | None] = mapped_column(index=True)
    # "So che e' incompleto e lo lascio cosi'". Serve ai movimenti importati da
    # fogli che il dato non ce l'avevano: senza, l'avviso li conta per sempre e
    # un badge che non si spegne mai insegna a ignorare tutti i badge.
    incomplete_accepted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class InvestmentTransaction(Base):
    __tablename__ = "investment_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    source_row: Mapped[int | None] = mapped_column(index=True)
    ticker: Mapped[str | None] = mapped_column(String(80))
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    transaction_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    units: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    currency: Mapped[str | None] = mapped_column(String(12))


class InvestmentTransactionDetail(Base):
    """Optional information not present in the original Ledger columns."""

    __tablename__ = "investment_transaction_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    transaction_id: Mapped[int] = mapped_column(unique=True, index=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class TransactionLedgerLink(Base):
    """Many-to-many link between a bank transaction and one or more ledger
    operations. Used so that creating a Buy/Sell from the same form as the
    bank movement doesn't require the user to enter the data twice, and so
    that historical transactions can be linked to their corresponding ledger
    rows after the fact.
    """

    __tablename__ = "transaction_ledger_links"
    __table_args__ = (UniqueConstraint("transaction_id", "ledger_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    transaction_id: Mapped[int] = mapped_column(index=True)
    ledger_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestmentInstrument(Base):
    """Classification and provider symbol for a portfolio holding."""

    __tablename__ = "investment_instruments"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    provider_symbol: Mapped[str | None] = mapped_column(String(80), index=True)
    isin: Mapped[str | None] = mapped_column(String(80))
    asset_class: Mapped[str] = mapped_column(String(120), default="Da classificare")
    area: Mapped[str] = mapped_column(String(120), default="Globale")
    sector: Mapped[str] = mapped_column(String(120), default="Altro")
    currency: Mapped[str] = mapped_column(String(12), default="EUR")
    target_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))


class MarketPrice(Base):
    """Locally cached price observations fetched from an external provider."""

    __tablename__ = "market_prices"
    __table_args__ = (UniqueConstraint("symbol", "observed_on", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="yahoo")
    observed_on: Mapped[date] = mapped_column(Date, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    currency: Mapped[str | None] = mapped_column(String(12))


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    key: Mapped[str] = mapped_column(String(80), index=True)
    label: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)


class LookupOption(Base):
    __tablename__ = "lookup_options"
    __table_args__ = (UniqueConstraint("user_id", "option_group", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    option_group: Mapped[str] = mapped_column(String(80), index=True)
    position: Mapped[int] = mapped_column(default=0)
    value: Mapped[str] = mapped_column(String(255))


class Note(Base):
    """Searchable notes, initially seeded from the heterogeneous Appunti sheet."""

    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("user_id", "source_cell"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    source_cell: Mapped[str | None] = mapped_column(String(32), index=True)
    section: Mapped[str] = mapped_column(String(120), default="Appunti")
    title: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())




class InstrumentProfile(Base):
    """Composizione di uno strumento (settori, titoli) presa dalla fonte prezzi.

    La fonte e' un endpoint non contrattualizzato: si conserva l'ultimo esito,
    riuscito o fallito, per poter dire nell'interfaccia da quando i dati non si
    aggiornano piu' invece di mostrarli come se fossero freschi.
    """

    __tablename__ = "instrument_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    payload: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class RetirementProfile(Base):
    """Le ipotesi con cui si guarda al pensionamento. Una per persona.

    Qui non ci sono regole previdenziali nazionali, e non devono entrarci:
    aliquote e coefficienti cambiano, e ricostruirli nel codice vuol dire
    sbagliarli entro un anno. L'utente porta gli importi che il suo ente gli
    fornisce gia' - INPS, AVS, Renteninformation - e il software fa i conti
    sopra quei numeri.

    I rendimenti sono **reali**, al netto dell'inflazione: su trent'anni un
    tasso nominale sbaglierebbe l'obiettivo di un terzo.
    """

    __tablename__ = "retirement_profiles"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    birth_year: Mapped[int] = mapped_column(Integer)
    # ISO 3166-1 alpha-2: serve alle note fiscali e ai valori proposti, non a
    # far calcolare al programma la pensione di quel paese.
    country: Mapped[str] = mapped_column(String(2), default="IT")
    target_retirement_age: Mapped[int] = mapped_column(Integer, default=60)
    real_return: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("4"))
    # La larghezza della distribuzione dei rendimenti, non la sua media: serve
    # solo alla simulazione. 15 e' la volatilita' storica di un portafoglio
    # azionario globale; chi tiene molte obbligazioni sta piu' in basso.
    return_volatility: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("15"))
    withdrawal_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("4"))
    withdrawal_tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    # Serve solo alle rendite non indicizzate (tipico delle LPP svizzere), che
    # perdono potere d'acquisto ogni anno. Per paese, perche' chi si sposta fra
    # Italia e Svizzera non vive la stessa inflazione.
    inflation: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("2"))
    # Su quali spese si costruisce l'obiettivo. La differenza fra un anno e
    # l'altro puo' valere centomila euro sul numero finale, quindi la scelta e'
    # dell'utente e va ricordata.
    expense_basis: Mapped[str] = mapped_column(String(20), default="average")
    custom_annual_expenses: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    lean_annual_expenses: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # Come cambia ogni categoria in pensione: JSON [{category, mode, amount}],
    # solo le categorie che cambiano. Vive nel profilo perche' e' un'ipotesi
    # sul pensionamento come le altre, e ne condivide l'isolamento per utente.
    expense_rules: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class IncomeStream(Base):
    """Un reddito che comincia a una certa eta'. Non "la pensione": un elenco.

    Una carriera divisa fra due paesi produce due pensioni pro-rata che partono
    a eta' diverse; il secondo pilastro svizzero e' un **capitale**, non una
    rendita; il TFR pure. Modellare "la pensione statale" al singolare non
    reggerebbe nessuno di questi casi, un elenco di flussi li regge tutti - e
    domani ci sta dentro anche un affitto.
    """

    __tablename__ = "income_streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    name: Mapped[str] = mapped_column(String(120))
    # 'annuity' = rendita annua a vita, 'capital' = importo una tantum che a
    # quella data entra nel patrimonio.
    kind: Mapped[str] = mapped_column(String(20), default="annuity")
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    start_age: Mapped[int] = mapped_column(Integer)
    # Una rendita non indicizzata, in termini reali, si svaluta ogni anno: e'
    # una differenza che su trent'anni pesa piu' dell'importo iniziale.
    indexed: Mapped[bool] = mapped_column(Boolean, default=True)
    country: Mapped[str | None] = mapped_column(String(2))
    # La seconda stima che i simulatori nazionali sanno dare: quanto sarebbe
    # la pensione smettendo di versare oggi. Ritirarsi presto non significa
    # "stessa pensione, presa piu' tardi" - nei sistemi contributivi la
    # abbassa - e senza questo secondo punto non si puo' dire di quanto.
    amount_if_stopping_now: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    notes: Mapped[str | None] = mapped_column(Text)


class CategorizationRule(Base):
    """Una regola che propone la categoria di un movimento in importazione.

    Vive solo nell'anteprima dell'import: riempie una casella che l'utente vede
    e puo' cambiare riga per riga. Nessuna regola scrive mai in database senza
    essere passata sotto gli occhi di chi importa - e' la proprieta' di
    sicurezza di tutta la funzione, e per questo le regole non si applicano ne'
    al salvataggio ne' alla creazione manuale di un movimento.

    Il nome della tabella non e' ``category_rules``: quella viene cancellata a
    ogni avvio da una riga rimasta in ``migrations.tracked_changes``, residuo
    di una versione precedente della funzione. Rinominarla qui la farebbe
    sparire a ogni riavvio senza un errore.
    """

    __tablename__ = "categorization_rules"
    __table_args__ = (UniqueConstraint("user_id", "pattern", "transaction_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    # Vince la prima regola che combacia, in quest'ordine: l'ordine e' una
    # scelta dell'utente e deve restare leggibile guardando l'elenco.
    position: Mapped[int] = mapped_column(default=0)
    pattern: Mapped[str] = mapped_column(String(255))
    # Con is_regex spento il pattern e' testo contenuto nella descrizione; con
    # la spunta accesa e' un'espressione regolare, che si compila una volta
    # sola per import e non una volta per riga.
    is_regex: Mapped[bool] = mapped_column(default=False)
    # Come per i movimenti: la regola punta alla categoria, e rinominarla
    # aggiorna anche le regole che la nominavano.
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    # Nullo vale per entrambi i tipi: la stessa regola serve a una spesa e a
    # un'entrata con la stessa descrizione.
    transaction_type: Mapped[str | None] = mapped_column(String(30))
    # Confrontati con l'importo assoluto della riga, estremi inclusi: il verso
    # lo da' il tipo, non il segno.
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # Spenta resta nell'elenco e non si applica: e' il modo di mettere da parte
    # una regola senza perdere il pattern scritto.
    active: Mapped[bool] = mapped_column(default=True)


class Event(Base):
    """Una cosa che e' successa e che taglia le categorie: un viaggio, un
    trasloco, un periodo vissuto in un'altra citta'.

    Non e' il campo ``goal`` dei movimenti. Un obiettivo e' una cosa verso cui
    vai ("Investire 150.000 euro"), un evento e' una cosa che e' successa: si
    somigliano solo perche' entrambi raccolgono movimenti, e tenere un elenco
    solo riempirebbe gli obiettivi di viaggi.

    Le date servono a proporre i movimenti del periodo, non a filtrarli: un
    acconto pagato tre mesi prima della partenza fa parte del viaggio lo
    stesso, quindi l'appartenenza e' una riga in ``transaction_events`` e non
    una condizione sulla data.
    """

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
    name: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # Chiuso vuol dire finito: resta con i suoi numeri nella card e esce dalla
    # tendina del modulo, dove servono solo gli eventi a cui stai lavorando.
    closed: Mapped[bool] = mapped_column(default=False)


class TransactionEvent(Base):
    """Di quale evento fa parte un movimento. Uno solo per movimento.

    La chiave primaria e' ``transaction_id``: un movimento che starebbe in due
    viaggi e' un movimento da dividere, non da etichettare due volte, e la
    divisione in due movimenti questa app la sa gia' fare.

    Tabella a parte e non una colonna su ``transactions``: quella e' larga
    ventidue colonne ed e' il cuore dell'app, e un concetto che riguarda solo
    alcune spese non deve allargarla. Un evento cancellato porta via le sue
    righe, non i movimenti che gli appartenevano.

    L'aggancio sparisce da solo con il movimento o con l'evento: una riga
    orfana conterebbe un movimento che non c'e' piu' fra quelli dell'evento.
    """

    __tablename__ = "transaction_events"

    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=current_user_id)
