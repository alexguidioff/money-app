"""Stable read routes used by the Money interface.

They deliberately keep the dashboard available even while optional import or
automation features are being extended independently.
"""
import calendar
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, distinct, extract, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlalchemy.orm import Session

from .calculation_engine import (account_balances_at, account_balances_series, account_reconciliation,
                                 investment_positions, normalized_name, savings_rate, source_effect)
from .categorization import MAX_REGOLE, categorie_ammesse
from .database import get_session
from .models import (Account, AppSetting, BudgetPlan, CategorizationRule,
                     Goal, InvestmentInstrument, InvestmentTransaction,
                     InvestmentTransactionDetail,
                     InstrumentProfile as InstrumentProfileModel,
                     AccountValuation, LiabilityTransactionDetail,
                     LookupOption, MarketPrice, Note, Transaction, TransactionLedgerLink)

router = APIRouter()
# I template delle ricorrenze vivono nella stessa tabella dei movimenti ma non
# sono denaro realmente entrato o uscito: vanno esclusi da ogni aggregato.
from .transaction_rules import REAL_MOVEMENT, BUDGET_MOVEMENT, INCOMPLETE_MOVEMENT, blank, missing_fields
MONTHS = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
MONTHS_LONG = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def num(value: Decimal | int | float | None) -> float:
    return round(float(value or 0), 2)


def transaction_json(row: Transaction, session: Session | None = None, *,
                     linked: list[dict[str, Any]] | None = None,
                     liability: dict[str, Any] | None = None) -> dict[str, Any]:
    """Un movimento come lo vuole l'interfaccia.

    ``linked`` serve a chi ne serializza tanti: le operazioni di portafoglio
    collegate si caricano una volta sola per l'intero elenco e si passano qui
    gia' pronte. Senza, ogni riga si andrebbe a cercare le proprie, che con
    quattromila movimenti sono quattromila interrogazioni.
    """
    amount = num(row.amount)
    signed = amount if row.transaction_type == "Income" else -amount
    # Quattro campi in meno rispetto a prima, tutti ricavabili da quelli che
    # restano: `categoryRaw` era la copia di `category`, `rawAmount` il valore
    # assoluto di `amount`, `date` una data gia' scritta in italiano - che
    # l'interfaccia ora compone da `effectiveOn` nella lingua giusta - e `type`
    # la minuscola di `transactionType`, presa da una tabellina di cinque voci.
    # Quest'ultimo pesava poco ma poteva mentire: il suo ripiego muto avrebbe
    # detto "transfer" per qualunque tipo di movimento aggiunto in futuro.
    # Spostare denaro fra conti non ha una categoria. Nei dati importati il
    # posto vuoto e' segnato con un trattino basso: e' un segnaposto del foglio
    # di calcolo, non una categoria, e mostrarlo fa sembrare che ce ne sia una.
    categoria = "" if (row.category or "").strip() in {"", "_", "-"} else row.category
    return {
        "id": str(row.id), "description": row.details or categoria,
        "category": categoria,
        "incomplete": bool(missing_fields(row)), "missingFields": missing_fields(row),
        "countsInBudget": row.counts_in_budget, "refundOfId": row.refund_of_id,
        "incompleteAccepted": row.incomplete_accepted,
        "occurredOn": row.occurred_on.isoformat(), "effectiveOn": row.effective_on.isoformat(),
        "amount": signed,
        "transactionType": row.transaction_type, "accountName": row.account_name,
        "destinationName": row.destination_name, "goal": row.goal, "details": row.details,
        "linkedLedger": linked if linked is not None
                        else (_linked_ledger_for_transactions(session, [row.id]).get(row.id, []) if session is not None else []),
        "liabilitySplit": liability if liability is not None
                          else (_liability_details_for_transactions(session, [row.id]).get(row.id) if session is not None else None),
    }


def _linked_ledger_for_transactions(session: Session, tx_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Carica tutte le righe del ledger collegate a un set di transazioni.

    Una sola query (con outer-join sui dettagli) invece di N round-trip: serve
    sia per la lista sia per la modifica di un movimento, cosi' la UI sa subito
    quali operazioni di portafoglio sono gia' state agganciate e puo' offrire
    di scollegarle o aggiungerne altre.
    """
    if not tx_ids:
        return {}
    rows = session.execute(
        select(TransactionLedgerLink, InvestmentTransaction, InvestmentTransactionDetail)
        .join(InvestmentTransaction, TransactionLedgerLink.ledger_id == InvestmentTransaction.id)
        .outerjoin(InvestmentTransactionDetail, InvestmentTransactionDetail.transaction_id == InvestmentTransaction.id)
        .where(TransactionLedgerLink.transaction_id.in_(tx_ids))
        .order_by(InvestmentTransaction.occurred_on.desc(), InvestmentTransaction.id.desc())
    ).all()
    by_tx: dict[int, list[dict[str, Any]]] = {tx_id: [] for tx_id in tx_ids}
    for link, itx, detail in rows:
        by_tx.setdefault(link.transaction_id, []).append({
            "linkId": link.id,
            "id": itx.id,
            "occurredOn": itx.occurred_on.isoformat(),
            "name": itx.name,
            "transactionType": itx.transaction_type,
            "amount": num(itx.amount),
            "units": num(itx.units) if itx.units is not None else None,
            "price": num(itx.price) if itx.price is not None else None,
            "currency": itx.currency or "EUR",
            "fee": float(detail.fee) if detail else 0,
            "notes": detail.notes if detail else None,
        })
    return by_tx


def _liability_details_for_transactions(session: Session, tx_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not tx_ids:
        return {}
    result = {}
    for row in session.scalars(select(LiabilityTransactionDetail).where(
            LiabilityTransactionDetail.transaction_id.in_(tx_ids))).all():
        result[row.transaction_id] = {
            "id": row.id, "kind": row.kind, "principal": num(row.principal_amount),
            "interest": num(row.interest_amount), "classified": row.is_classified,
        }
    return result


def period_total(session: Session, year: int, month: int, tx_type: str) -> float:
    if tx_type in {"Income", "Expenses"}:
        return round(sum(budget_actual(session, year, month, tx_type).values()), 2)
    return num(session.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        extract("year", Transaction.effective_on) == year,
        extract("month", Transaction.effective_on) == month,
        Transaction.transaction_type == tx_type,
        BUDGET_MOVEMENT,
    )))


def period_total_range(session: Session, year: int, month: int | None, tx_type: str) -> float:
    """Come period_total, ma month=None aggrega sull'intero anno (Total Year)."""
    if tx_type in {"Income", "Expenses"}:
        months = [month] if month is not None else range(1, 13)
        return round(sum(sum(budget_actual(session, year, value, tx_type).values()) for value in months), 2)
    conditions = [extract("year", Transaction.effective_on) == year, Transaction.transaction_type == tx_type, BUDGET_MOVEMENT]
    if month is not None:
        conditions.append(extract("month", Transaction.effective_on) == month)
    return num(session.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(*conditions)))


def derived_savings(session: Session, year: int, month: int | None) -> float:
    """Il risparmio di un periodo: quello che e' entrato meno quello che e' uscito.

    Non e' la somma dei movimenti di tipo Savings. Quelli erano la ribattitura a
    mano di questo stesso numero a fine mese, e ogni volta che la mano sbagliava
    di qualche euro restava un residuo non allocato che non voleva dire nulla.
    Il budget dei risparmi resta invece pianificato a mano: li' il confronto e'
    fra quello che volevi mettere da parte e quello che hai messo da parte.
    """
    income = period_total_range(session, year, month, "Income")
    expenses = period_total_range(session, year, month, "Expenses")
    return round(income - expenses, 2)


# Quanti movimenti puo' toccare una modifica di massa. Senza un tetto,
# "seleziona filtrati" su un filtro largo scaricava tutti gli id dell'archivio
# - migliaia - e la modifica li rispediva tutti nel corpo della richiesta.
MAX_SELEZIONE_MASSA = 500

SAVINGS_DEFAULT_CATEGORY = "Savings"


def savings_category(session: Session) -> str:
    """La categoria su cui confluisce il risparmio calcolato.

    Il risparmio non ha una categoria propria - e' quello che resta, non una
    destinazione - ma il budget e' organizzato per categorie, quindi ne serve
    una su cui appoggiarlo per poter confrontare pianificato ed effettivo.
    """
    value = session.scalar(select(AppSetting.value).where(AppSetting.key == "savings_default_category"))
    return (value or SAVINGS_DEFAULT_CATEGORY).strip()


def resolve_compare_period(year: int, month: int | None, compare_to: str | None) -> tuple[int, int | None] | None:
    """Calcola (year, month) del periodo di confronto.

    - compare_to="prior_year": stesso periodo (mese o anno intero) dell'anno precedente.
    - compare_to="prior_period": per un mese, il mese precedente; per l'anno intero,
      l'anno precedente (non esiste un "periodo intero precedente" diverso dall'anno prima).
    - Nessun compare_to: nessun confronto (None).
    """
    if compare_to is None:
        return None
    if compare_to == "prior_year":
        return (year - 1, month)
    if compare_to == "prior_period":
        if month is None:
            return (year - 1, None)
        if month == 1:
            return (year - 1, 12)
        return (year, month - 1)
    return None


def period_days(session: Session, year: int, month: int | None) -> dict[str, Any]:
    """Days in Period / Days passed in Period / Days passed %, come nel foglio
    Calculations (righe 23-25): per un mese e' l'ultimo giorno del mese; per
    l'anno intero e' 365/366. Days passed = 0 per periodi futuri, pieno per
    periodi passati, parziale per il periodo corrente.
    """
    today = date.today()
    if month is None:
        days_in_period = (date(year, 12, 31) - date(year - 1, 12, 31)).days
        if year == today.year:
            days_passed = (today - date(year - 1, 12, 31)).days
        elif year < today.year:
            days_passed = days_in_period
        else:
            days_passed = 0
    else:
        days_in_period = calendar.monthrange(year, month)[1]
        if year == today.year:
            if month == today.month:
                days_passed = today.day
            elif month < today.month:
                days_passed = days_in_period
            else:
                days_passed = 0
        elif year < today.year:
            days_passed = days_in_period
        else:
            days_passed = 0
    return {
        "daysInPeriod": days_in_period,
        "daysPassed": days_passed,
        "completion": round(days_passed / days_in_period, 4) if days_in_period else 0,
    }




def nomi_categorie(session: Session) -> dict[str, str]:
    """Le categorie come le ha scritte l'utente, per chiave minuscola.

    Gli aggregati di `budget_actual_year` normalizzano in minuscolo per sommare
    "Spesa" e "spesa" insieme; per mostrarle servono i nomi originali.
    """
    return {nome.strip().lower(): nome.strip()
            for nome in session.scalars(select(Transaction.category).distinct()) if nome}


def _period_category_breakdown(session: Session, year: int, month: int | None, budget_type: str,
                              actual_rows: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Categorie pianificate/tracciate per un budget_type (Expenses/Income/Savings)
    nel periodo dato (mese o anno intero), case-insensitive sulla categoria.

    `actual_rows` permette di passare gli aggregati gia' calcolati da
    `budget_actual_year` (o `derived_savings`): in year mode il chiamante ha
    spesso gia' i yearly aggregates per costruire il `monthly`, e ri-fare la
    groupBy per categoria sarebbe una query in piu' per tipo.
    """
    if month is not None:
        plans = session.scalars(select(BudgetPlan).where(BudgetPlan.period == date(year, month, 1), BudgetPlan.budget_type == budget_type)).all()
        planned_by_category: dict[str, float] = {plan.category: num(plan.amount) for plan in plans}
        category_groups = {plan.category: plan.category_group for plan in plans}
    else:
        plans = session.scalars(select(BudgetPlan).where(extract("year", BudgetPlan.period) == year, BudgetPlan.budget_type == budget_type)).all()
        planned_by_category = defaultdict(float)
        category_groups = {}
        for plan in plans:
            planned_by_category[plan.category] += num(plan.amount)
            category_groups[plan.category] = plan.category_group
    if actual_rows is None:
        if budget_type == "Savings":
            actual_rows = {savings_category(session): derived_savings(session, year, month)}
        elif month is not None:
            actual_rows = budget_actual(session, year, month, budget_type)
        else:
            per_month = budget_actual_year(session, year, budget_type)
            actual_rows = {category: round(sum(rows.get(category, 0) for rows in per_month.values()), 2)
                           for category in {name for rows in per_month.values() for name in rows}}
    actual_by_category = {category.strip().lower(): num(amount) for category, amount in actual_rows.items()}
    categories = [{"name": category, "amount": actual_by_category.get(category.strip().lower(), 0), "budget": budget, "categoryGroup": category_groups.get(category)} for category, budget in planned_by_category.items()]
    # La spesa in una categoria senza piano e' spesa lo stesso. Partire solo dal
    # piano la faceva sparire: un anno senza budget (il 2023) mostrava una
    # ripartizione vuota con 9.799 EUR spesi, e le categorie aggiunte dopo aver
    # scritto il budget non comparivano fra le piu' pesanti.
    pianificate = {category.strip().lower() for category in planned_by_category}
    extra = {chiave: importo for chiave, importo in actual_by_category.items() if chiave not in pianificate and importo}
    if extra:
        nomi = nomi_categorie(session)
        categories += [{"name": nomi.get(chiave, chiave), "amount": importo, "budget": 0, "categoryGroup": None}
                       for chiave, importo in extra.items()]
    categories.sort(key=lambda item: (item["budget"], item["amount"]), reverse=True)
    return categories


def _summary_core(session: Session, year: int, month: int | None) -> dict[str, Any]:
    """Aggregati di un periodo (mese o anno intero): entrate/spese/risparmi,
    budget pianificato/effettivo per categoria (Expenses), giorni del periodo.

    Le entrate e le spese si pescano da `budget_actual_year` (una query per tipo
    per l'anno intero, con i rimborsi gia' netti): la firma originale chiamava
    `period_total_range` due volte e poi `derived_savings` che le richiamava
    ancora due, per un totale di quattro interrogazioni che davano lo stesso
    numero due volte.
    """
    mesi = [month] if month is not None else range(1, 13)
    speso_anno = {tipo: budget_actual_year(session, year, tipo) for tipo in ("Income", "Expenses")}
    def _totale(tipo: str) -> float:
        return round(sum(sum(speso_anno[tipo][m].values()) for m in mesi), 2)
    income, expenses = _totale("Income"), _totale("Expenses")
    savings = round(income - expenses, 2)
    categories = _period_category_breakdown(session, year, month, "Expenses")
    planned_total = sum(item["budget"] for item in categories)
    actual_total = sum(item["amount"] for item in categories)
    days = period_days(session, year, month)
    rate = savings_rate(income, expenses)
    return {
        "income": income, "expenses": expenses, "savings": savings,
        "plannedExpenses": planned_total, "actualExpenses": actual_total,
        "budgetUsed": round(actual_total / planned_total * 100) if planned_total else 0,
        "categories": categories,
        "control": _stato_controllo(
            round(actual_total / planned_total * 100, 1) if planned_total else 0.0,
            round(days["completion"] * 100, 1),
            sum(1 for voce in categories
                if voce["budget"] > 0 and voce["amount"] > voce["budget"]),
        ),
        "daysInPeriod": days["daysInPeriod"], "daysPassed": days["daysPassed"], "periodCompletion": days["completion"],
        "savingsRate": float(rate) if rate is not None else None,
    }


# Quanto sopra il ritmo del tempo si puo' stare prima di chiamarla corsa: cinque
# punti percentuali. Sta qui, in un posto solo, per poterla cambiare in un posto solo.
MARGINE_RITMO = 5


def _stato_controllo(speso_percento: float, tempo_percento: float, sforate: int) -> dict[str, Any]:
    """Rispondere a "sono in controllo?" prima che uno debba leggere sei card.

    Tre ingredienti che l'app calcola gia': quanto del budget e' andato, quanto
    del periodo e' passato, quante categorie hanno sforato. Nessuna previsione,
    nessun modello: e' il confronto fra due percentuali e un conteggio.
    """
    if speso_percento > 100:
        livello = "over"
    elif speso_percento > tempo_percento + MARGINE_RITMO or sforate >= 3:
        livello = "watch"
    else:
        livello = "ok"
    return {"level": livello, "spentPercent": round(speso_percento, 1),
            "timePercent": round(tempo_percento, 1), "overBudgetCategories": sforate}


def _period_label(year: int, month: int | None) -> str:
    return f"{MONTHS_LONG[month - 1]} {year}" if month is not None else f"Anno {year}"


def _period_ref(year: int, month: int | None) -> dict[str, int | None]:
    """Il periodo in numeri, da comporre nell'interfaccia.

    ``period`` resta accanto per compatibilita', ma e' italiano: mostrarlo in
    una pagina in inglese e' l'errore che AI.md dice di non ripetere, e infatti
    l'abbiamo ripetuto. Chi disegna una schermata usa questi due campi.
    """
    return {"periodYear": year, "periodMonth": month}


def operazioni_per_conto(session: Session) -> dict[int, set[int]]:
    """Quali operazioni del ledger appartengono a quale conto broker.

    Il movimento collegato e' per forza un `Investment` - lo impone
    l'endpoint dei collegamenti - e un Investment ha un conto broker su uno dei
    due lati. Quindi il conto non si deduce piu' dal verso dell'operazione: si
    legge, ed e' il lato marcato broker.

    Prima si guardava il verso (comprando i soldi arrivano sul conto titoli,
    vendendo ne escono) perche' non c'era modo di sapere quale dei due conti
    fosse il broker. Ora c'e', e il verso serve solo a quello che e' sempre
    stato: distinguere un versamento da un prelievo.
    """
    righe = session.execute(
        select(InvestmentTransaction.id, Transaction.account_name, Transaction.destination_name)
        .join(TransactionLedgerLink, TransactionLedgerLink.ledger_id == InvestmentTransaction.id)
        .join(Transaction, Transaction.id == TransactionLedgerLink.transaction_id)
        .where(Transaction.transaction_type == "Investment")).all()
    if not righe:
        return {}
    broker = {normalized_name(conto.name): conto.id
              for conto in session.scalars(select(Account).where(Account.is_broker.is_(True))).all()}
    per_conto: dict[int, set[int]] = defaultdict(set)
    for riga in righe:
        for nome in (riga.account_name, riga.destination_name):
            conto_id = broker.get(normalized_name(nome))
            if conto_id is not None:
                per_conto[conto_id].add(riga.id)
    return dict(per_conto)


def conti_con_valore_di_mercato(session: Session) -> set[int]:
    """I conti che hanno almeno un'operazione del ledger attribuita."""
    return set(operazioni_per_conto(session))


def rivalutazioni_per_conto(session: Session) -> dict[int, list[tuple[date, Decimal]]]:
    """Quanto sono cresciute, mese per mese, le operazioni attribuite a ogni conto.

    E' un aggiustamento, non un valore: il saldo del conto e' gia' quanto ci hai
    versato, e qui si aggiunge solo quello che quei titoli hanno guadagnato o
    perso. Cosi' un conto vale il suo costo piu' il guadagno delle SUE
    operazioni, e collegarne due su un conto nuovo non gli regala una fetta del
    portafoglio di un altro.

    Le posizioni chiuse contano come le aperte: una venduta in guadagno ha
    market zero ma versato negativo (l'incasso ha superato il costo), e la
    differenza fra i due e' il guadagno realizzato - che e' rimasto sul conto e
    quindi va contato.

    Finche' non sono attribuite tutte, il conto mostra il costo piu' il guadagno
    di quelle collegate: sottostima, ma non inventa. Attribuite tutte, il costo
    torna a essere il versato totale e il conto vale esattamente il mercato.
    """
    per_conto: dict[int, list[tuple[date, Decimal]]] = {}
    for conto_id, operazioni in operazioni_per_conto(session).items():
        linea = portfolio_timeline(session, operazioni)
        per_conto[conto_id] = [(date.fromisoformat(punto["period"]),
                                Decimal(str(punto["marketValue"])) - Decimal(str(punto["investedCapital"])))
                               for punto in linea]
    return per_conto


def valutazioni_per_conto(session: Session, linea_portafoglio: list[dict[str, Any]] | None = None
                          ) -> dict[int, list[tuple[date, Decimal]]]:
    """Le stime scritte a mano, per conto, in ordine di data.

    Sostituiscono il saldo: una casa non vale la somma dei bonifici che ci sono
    passati sopra. I conti investimento non passano di qui - quelli il saldo ce
    l'hanno giusto (e' il versato) e gli manca solo il guadagno, che e' un
    aggiustamento e sta in `rivalutazioni_per_conto`.

    Una query sola: i conti da valutare a mano sono pochi e le loro stime ancora
    di meno, ma la serie del patrimonio le chiede dodici volte di fila.
    """
    per_conto: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
    a_mano = {conto.id for conto in session.scalars(select(Account)).all()
              if conto.needs_manual_valuation}
    for riga in session.execute(select(AccountValuation.account_id, AccountValuation.observed_on,
                                       AccountValuation.value)
                                .order_by(AccountValuation.observed_on)).all():
        # Spegnere il flag deve smettere di usare le stime, non solo di
        # mostrarle: il filtro sta qui perche' qui si decide quanto vale un
        # conto, e altrove non se ne deve piu' parlare.
        if riga.account_id in a_mano:
            per_conto[riga.account_id].append((riga.observed_on, riga.value))
    return per_conto


def _net_worth_breakdown(session: Session, year: int, month: int | None,
                         movimenti: list[Any] | None = None,
                         linea_portafoglio: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Patrimonio netto del periodo.

    I conti investimento entrano al valore di mercato: il loro saldo e' un
    totale di bonifici, cioe' il capitale versato, e non sa niente di guadagni o
    perdite non realizzati. La sostituzione avviene dentro
    `valutazioni_per_conto`, che e' l'unico posto che decide quanto vale un
    conto che non vale la somma dei suoi movimenti - prima era un `if` ripetuto
    qui e in altre quattro funzioni.

    market_value/invested_capital vengono dallo snapshot mensile piu' recente
    con period <= fine del mese/anno richiesto (cosi' un mese senza import
    ancora eseguito riusa comunque l'ultimo dato disponibile).
    """
    cutoff = date(year + 1, 1, 1) if month is None else (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1))
    # I saldi si ricostruiscono alla data del periodo, non si leggono da
    # `current_balance`. Quello e' il saldo di oggi e non sa niente del mese
    # scelto: chiedendo luglio si sommavano gli investimenti di luglio ai conti
    # di oggi, e il patrimonio della Panoramica non tornava con quello della
    # pagina Patrimonio, che i saldi li calcola alla data giusta.
    conti = [a for a in session.scalars(select(Account)).all() if a.counts_in_net_worth]
    linea = linea_portafoglio if linea_portafoglio is not None else portfolio_timeline(session)
    # `movimenti` si passa quando il chiamante ne ha bisogno per due periodi:
    # caricarli due volte costa piu' del calcolo che ci si fa sopra.
    saldi = account_balances_at(conti, movimenti if movimenti is not None else movimenti_per_saldi(session),
                                cutoff - timedelta(days=1), valutazioni_per_conto(session),
                                rivalutazioni_per_conto(session))
    # `totals["liability"]` e' il debito con il segno gia' girato, cioe' un
    # numero positivo: va sottratto, non sommato. Sommandolo un mutuo faceva
    # salire il patrimonio invece di abbassarlo, e la Panoramica dava 80.000 in
    # piu' della pagina Patrimonio - il doppio del debito. Con debito zero le
    # due cifre coincidevano, ed e' per questo che non si vedeva.
    other_balance = saldi["totals"]["bank"] + saldi["totals"]["asset"] - saldi["totals"]["liability"]
    snapshot = portfolio_state_at(linea, cutoff - timedelta(days=1))
    market_value = snapshot["marketValue"] if snapshot else 0.0
    invested_capital = snapshot["investedCapital"] if snapshot else 0.0
    # Il mercato e' gia' dentro `other_balance`, sul conto investimenti: qui si
    # riporta solo per dire di quanto, non si somma un'altra volta.
    total = round(other_balance, 2)
    liquid_accounts = {account.id for account in conti if account.is_liquid}
    liquid = sum(item["balance"] for item in saldi["accounts"]
                 if item["account_id"] in liquid_accounts and item["group"] != "liability")
    liquid -= saldi["totals"]["liability"]
    gain = round(market_value - invested_capital, 2)
    return {
        "total": total,
        "liquid": round(liquid, 2),
        "marketValue": market_value,
        "investedCapital": invested_capital,
        "gain": gain,
        "gainPercent": round(gain / invested_capital * 100, 2) if invested_capital else None,
        "otherBalance": round(other_balance, 2),
        "snapshotPeriod": snapshot["period"] if snapshot else None,
    }


@router.get("/api/summary")
def summary(
    year: int = Query(ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12, description="Omesso o assente = anno intero"),
    compare_to: str | None = Query(None, description="prior_period (mese/anno precedente) oppure prior_year (stesso periodo anno prima)"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    core = _summary_core(session, year, month)
    movimenti_saldi = movimenti_per_saldi(session)
    linea_portafoglio = portfolio_timeline(session)
    net_worth_info = _net_worth_breakdown(session, year, month, movimenti_saldi, linea_portafoglio)

    comparison = None
    net_worth_comparison = None
    compare_period = resolve_compare_period(year, month, compare_to)
    if compare_period is not None:
        compare_year, compare_month = compare_period
        compare_core = _summary_core(session, compare_year, compare_month)
        comparison = {
            "period": _period_label(compare_year, compare_month),
            **_period_ref(compare_year, compare_month),
            "income": compare_core["income"], "expenses": compare_core["expenses"], "savings": compare_core["savings"],
            "incomeDelta": round(core["income"] - compare_core["income"], 2),
            "expensesDelta": round(core["expenses"] - compare_core["expenses"], 2),
            "savingsDelta": round(core["savings"] - compare_core["savings"], 2),
        }
        compare_net_worth = _net_worth_breakdown(session, compare_year, compare_month, movimenti_saldi, linea_portafoglio)
        net_worth_comparison = {
            "period": _period_label(compare_year, compare_month),
            **_period_ref(compare_year, compare_month),
            "total": compare_net_worth["total"],
            "totalDelta": round(net_worth_info["total"] - compare_net_worth["total"], 2),
        }

    oggi = date.today()
    stima = stima_fine_periodo(session, year, month, oggi) if month is not None else None
    # Copertura obiettivi: quello che hai messo da parte davvero, contro quello
    # che i tuoi obiettivi chiedono ogni mese. Senza categorie assegnate ai goal
    # e' l'unico confronto che i dati sostengono.
    ritmo_obiettivi = goals(session)["monthlyNeededTotal"]
    copertura = {"savedThisPeriod": core["savings"], "monthlyNeeded": ritmo_obiettivi,
                 "coverage": round(core["savings"] / ritmo_obiettivi * 100, 1) if ritmo_obiettivi else None}
    return {
        "projection": stima,
        "goalCoverage": copertura,
        "income": core["income"], "expenses": core["expenses"], "savings": core["savings"],
        "netWorthDetail": net_worth_info,
        "netWorthComparison": net_worth_comparison,
        "budgetUsed": core["budgetUsed"], "control": core["control"],
        "plannedExpenses": core["plannedExpenses"], "actualExpenses": core["actualExpenses"],
        "daysInPeriod": core["daysInPeriod"], "daysPassed": core["daysPassed"], "periodCompletion": core["periodCompletion"],
        "savingsRate": core["savingsRate"],
        "comparison": comparison,
    }


PIE_COLORS = ["#6d8ff4", "#f0a35b", "#5ab7a6", "#d77b91", "#9479d1", "#c9c9c9"]


def _pie_breakdown(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """Top 5 categorie per importo tracciato + 'Other' con il resto,
    come nel 'Category Breakdown' di Budget Dashboard (colonne AF:AN)."""
    tracked = [c for c in categories if c["amount"]]
    tracked.sort(key=lambda item: item["amount"], reverse=True)
    top5 = tracked[:5]
    other_total = sum(item["amount"] for item in tracked[5:])
    items = [{"name": item["name"], "value": item["amount"]} for item in top5]
    if other_total:
        # "Altre categorie" per non collidere con una categoria reale chiamata "Other".
        items.append({"name": "Altre categorie", "value": round(other_total, 2)})
    for index, item in enumerate(items):
        item["color"] = PIE_COLORS[index % len(PIE_COLORS)]
    return {"items": items, "total": round(sum(item["value"] for item in items), 2)}


def _section_breakdown(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """Righe Tracked/Budget/% Compl./Remaining/Excess per una sezione
    (Income/Expenses/Savings), come le colonne X:AD di Budget Dashboard."""
    rows = []
    for item in categories:
        tracked, budget = item["amount"], item["budget"]
        completion = round(tracked / budget, 4) if budget else None
        remaining = round(budget - tracked, 2) if budget - tracked > 0 else 0
        excess = round(tracked - budget, 2) if budget - tracked < 0 else 0
        rows.append({"name": item["name"], "tracked": tracked, "budget": budget, "completion": completion, "remaining": remaining, "excess": excess})
    return {"categories": rows, "plannedTotal": round(sum(item["budget"] for item in categories), 2), "actualTotal": round(sum(item["amount"] for item in categories), 2)}


@router.get("/api/summary-breakdown")
def summary_breakdown(
    year: int = Query(ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12, description="Omesso o assente = anno intero"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Dettaglio Entrate/Spese/Risparmio del periodo per la Panoramica:
    - sections: righe Tracked/Budget/%/Remaining/Excess per categoria (Budget Dashboard X:AD)
    - pie: top 5 categorie + Other per tipo (Budget Dashboard AF:AN, Category Breakdown)
    """
    types = ("Income", "Expenses", "Savings")
    # In year mode, un aggregato annuale per tipo (con self-join rimborsi)
    # copre sia la groupBy per categoria (sommando sui 12 mesi) sia i 12
    # `period_total` del blocco `monthly`. Tre round-trip a testa era il
    # collo di bottiglia principale: 36 chiamate per il monthly + 3 groupBy
    # per il breakdown = 39 query, ora 2.
    speso_anno: dict[str, dict[int, dict[str, float]]] = {}
    if month is None:
        speso_anno["Income"] = budget_actual_year(session, year, "Income")
        speso_anno["Expenses"] = budget_actual_year(session, year, "Expenses")

    def _actual_per_cat(budget_type: str) -> dict[str, float]:
        if budget_type == "Savings":
            entrate = sum(sum(valori.values()) for valori in speso_anno["Income"].values())
            spese = sum(sum(valori.values()) for valori in speso_anno["Expenses"].values())
            return {savings_category(session): round(entrate - spese, 2)}
        aggregato: dict[str, float] = defaultdict(float)
        for valori in speso_anno[budget_type].values():
            for cat, val in valori.items():
                aggregato[cat] += val
        return {c: round(v, 2) for c, v in aggregato.items()}

    if month is None:
        actuals = {t: _actual_per_cat(t) for t in types}
        breakdowns = {t: _period_category_breakdown(session, year, month, t, actual_rows=actuals[t]) for t in types}
    else:
        breakdowns = {t: _period_category_breakdown(session, year, month, t) for t in types}
    sections = {t.lower(): _section_breakdown(breakdowns[t]) for t in types}
    pie = {t.lower(): _pie_breakdown(breakdowns[t]) for t in types}

    # Il "budget contro tracciato mese per mese" dell'anno sta in Andamento annuale:
    # ripeterlo qui lo mostrava due volte nella stessa pagina.
    return {"period": _period_label(year, month), "sections": sections, "pie": pie}


def _invested_by_month(session: Session, year: int) -> list[dict[str, Any]]:
    """Denaro nuovo entrato negli strumenti, mese per mese: acquisti meno vendite.

    Al netto, perche' un ribilanciamento vende uno strumento per comprarne un
    altro e non e' denaro nuovo: contando solo gli acquisti ogni ribilanciamento
    sembrerebbe un versamento.
    """
    monthly = [0.0] * 12
    rows = session.scalars(
        select(InvestmentTransaction).where(extract("year", InvestmentTransaction.occurred_on) == year)
    ).all()
    for row in rows:
        if row.transaction_type not in ("Buy", "Sell"):
            continue
        amount = num(row.amount)
        monthly[row.occurred_on.month - 1] += amount if row.transaction_type == "Buy" else -amount
    return [{"month": MONTHS[i], "amount": round(monthly[i], 2)} for i in range(12)]


@router.get("/api/analysis")
def analysis(
    year: int = Query(ge=2000, le=2100),
    category_type: str = Query("Expenses", description="Income, Expenses o Savings, per Category Analysis"),
    category: str | None = Query(None, description="Categoria per Category Analysis; se omessa nessuna transazione viene restituita"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Vista 'Andamento annuale': sempre sull'anno intero.
    - monthlyBudget: per Income/Expenses/Savings, 12 mesi x {inBudget, remaining, excess},
      con isCurrentMonth per evidenziare il mese in corso (Budget Dashboard AF52:AN72).
    - topExpenseCategories: le 10 categorie di spesa maggiori dell'anno, per un treemap
      (Budget Trends, 'Top 10 impactful Exp. Category').
    - savingsByMonth: risparmio netto per ciascuno dei 12 mesi (Budget Trends, 'Savings by month').
    - categoryTransactions: le 10 transazioni di importo piu' alto per category_type/category
      nell'anno (Budget Trends, 'Category Analysis' + 'Top 10 Transactions').
    """
    types = ("Income", "Expenses", "Savings")
    today = date.today()

    # Una sola query per tipo per tutto l'anno (grazie a `budget_actual_year`,
    # che gestisce anche i rimborsi in un'unica sotto-query) invece di dodici
    # chiamate di `period_total` o `derived_savings` per ciascuno. Sull'analisi
    # annuale fa la differenza fra una pagina che si apre e una che pensa.
    speso_per_tipo: dict[str, dict[int, float]] = {t: {} for t in types}
    for t in types:
        per_mese_categorie = budget_actual_year(session, year, t)
        for mese, valori in per_mese_categorie.items():
            speso_per_tipo[t][mese] = round(sum(valori.values()), 2)

    monthly_budget: dict[str, list[dict[str, Any]]] = {}
    for t in types:
        plans = session.scalars(select(BudgetPlan).where(extract("year", BudgetPlan.period) == year, BudgetPlan.budget_type == t)).all()
        planned_by_month: dict[int, float] = defaultdict(float)
        for plan in plans:
            planned_by_month[plan.period.month] += num(plan.amount)
        # Per Income/Expenses il tracked viene direttamente dall'aggregazione
        # annuale; per Savings, che `budget_actual_year` deriva come entrate
        # meno spese, e' lo stesso numero. Niente piu' loop sui 12 mesi.
        rows = []
        for m in range(1, 13):
            tracked = speso_per_tipo[t].get(m, 0.0)
            budget = round(planned_by_month.get(m, 0), 2)
            delta = round(budget - tracked, 2)
            rows.append({
                "month": MONTHS[m - 1],
                "inBudget": round(min(budget, tracked), 2),
                "remaining": delta if delta > 0 else 0,
                "excess": round(-delta, 2) if delta < 0 else 0,
                "isCurrentMonth": year == today.year and m == today.month,
            })
        monthly_budget[t.lower()] = rows

    expense_categories = _period_category_breakdown(session, year, None, "Expenses")
    tracked_only = sorted((c for c in expense_categories if c["amount"] > 0), key=lambda c: c["amount"], reverse=True)
    top_expense_categories = [{"name": c["name"], "value": c["amount"], "color": PIE_COLORS[i % len(PIE_COLORS)]} for i, c in enumerate(tracked_only[:10])]

    savings_by_month = [{"month": MONTHS[m - 1], "amount": speso_per_tipo["Savings"].get(m, 0.0)} for m in range(1, 13)]

    category_transactions: list[dict[str, Any]] = []
    if category:
        rows = session.scalars(select(Transaction).where(
            extract("year", Transaction.effective_on) == year,
            Transaction.transaction_type == category_type,
            func.lower(Transaction.category) == category.strip().lower(),
            BUDGET_MOVEMENT,
        ).order_by(Transaction.amount.desc()).limit(10)).all()
        category_transactions = [{"date": row.effective_on.isoformat(), "amount": num(row.amount), "description": row.details or row.category} for row in rows]

    # Le categorie da scegliere sono quelle dell'anno analizzato e del tipo
    # scelto. Il frontend le prendeva dalla Panoramica, cioe' dal mese
    # selezionato li': una categoria senza movimenti in quel mese non c'era.
    category_options = sorted({nome.strip() for nome in session.scalars(select(Transaction.category).where(
        extract("year", Transaction.effective_on) == year, Transaction.transaction_type == category_type,
        BUDGET_MOVEMENT).distinct()) if nome and nome.strip()}, key=str.casefold)

    return {
        "year": year,
        "monthlyBudget": monthly_budget,
        "topExpenseCategories": top_expense_categories,
        "savingsByMonth": savings_by_month,
        "investedByMonth": _invested_by_month(session, year),
        "categoryTransactions": category_transactions,
        "categoryOptions": category_options,
    }


@router.get("/api/transactions")
def transactions(
    limit: int = Query(5000, ge=1, le=10000),
    session: Session = Depends(get_session),
    *,
    offset: int = 0,
    category: str | None = None,
    transaction_type: str | None = None,
    account_name: str | None = None,
    goal: str | None = None,
    search: str | None = None,
    year: int | None = None,
    month: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    incomplete: bool = False,
    ids_only: bool = False,
    budget_only: bool = False,
) -> dict[str, Any]:
    """Una pagina di movimenti, filtrata dal database.

    Prima l'elenco intero finiva nel browser - quasi quattromila righe, un
    megabyte e mezzo di JSON a ogni apertura - e la pagina Movimenti filtrava in
    locale. Filtrare dove stanno i dati costa una query e restituisce cento
    righe: il resto non serve a nessuno finche' non lo si scorre.
    """
    # Difese scritte a mano invece che con Query(ge=...): cosi' la funzione
    # resta chiamabile anche da dentro l'app, non solo via HTTP.
    limit = max(1, min(int(limit), 10000))
    offset = max(0, int(offset))
    query = select(Transaction).where(Transaction.is_recurring_template.is_(False))
    count_query = select(func.count(Transaction.id)).where(Transaction.is_recurring_template.is_(False))
    if incomplete:
        query = query.where(INCOMPLETE_MOVEMENT)
        count_query = count_query.where(INCOMPLETE_MOVEMENT)
    if budget_only:
        query = query.where(BUDGET_MOVEMENT)
        count_query = count_query.where(BUDGET_MOVEMENT)
    if category:
        query = query.where(func.lower(Transaction.category) == category.strip().lower())
        count_query = count_query.where(func.lower(Transaction.category) == category.strip().lower())
    if transaction_type:
        query = query.where(Transaction.transaction_type == transaction_type)
        count_query = count_query.where(Transaction.transaction_type == transaction_type)
    if account_name:
        # Un trasferimento riguarda due conti: filtrando per "Banca" ci si
        # aspetta di vedere anche quelli arrivati su Banca, non solo quelli
        # partiti. E' quello che faceva l'interfaccia quando filtrava da sola.
        nome = account_name.strip().lower()
        conto_clause = (func.lower(func.coalesce(Transaction.account_name, "")) == nome) | (
            func.lower(func.coalesce(Transaction.destination_name, "")) == nome)
        query = query.where(conto_clause)
        count_query = count_query.where(conto_clause)
    if goal:
        # "-" chiede i movimenti senza goal: servono per capire cosa manca a un
        # obiettivo, ed e' l'unico modo di cercarli visto che il campo e' vuoto.
        goal_clause = (blank(Transaction.goal) if goal == "-"
                       else func.lower(func.coalesce(Transaction.goal, "")) == goal.strip().lower())
        query = query.where(goal_clause)
        count_query = count_query.where(goal_clause)
    if year is not None:
        query = query.where(extract("year", Transaction.effective_on) == year)
        count_query = count_query.where(extract("year", Transaction.effective_on) == year)
    if month is not None:
        query = query.where(extract("month", Transaction.effective_on) == month)
        count_query = count_query.where(extract("month", Transaction.effective_on) == month)
    if search:
        needle = f"%{search.strip().lower()}%"
        # Le stesse colonne su cui cercava l'interfaccia quando l'elenco era
        # tutto in memoria, piu' la data: chi cerca "2026-07" cerca un mese.
        search_clause = (
            (func.lower(func.coalesce(Transaction.details, "")).like(needle))
            | (func.lower(Transaction.category).like(needle))
            | (func.lower(func.coalesce(Transaction.account_name, "")).like(needle))
            | (func.lower(func.coalesce(Transaction.destination_name, "")).like(needle))
            | (func.cast(Transaction.effective_on, String).like(needle))
        )
        query = query.where(search_clause)
        count_query = count_query.where(search_clause)
    if date_from:
        parsed_from = date.fromisoformat(date_from)
        query = query.where(Transaction.effective_on >= parsed_from)
        count_query = count_query.where(Transaction.effective_on >= parsed_from)
    if date_to:
        parsed_to = date.fromisoformat(date_to)
        query = query.where(Transaction.effective_on <= parsed_to)
        count_query = count_query.where(Transaction.effective_on <= parsed_to)
    if ids_only:
        # `total` dice quanti ne ha trovati davvero: l'interfaccia deve poter
        # avvisare che la selezione e' stata troncata, non farlo di nascosto.
        ids = list(session.scalars(query.with_only_columns(Transaction.id).limit(MAX_SELEZIONE_MASSA)))
        return {"ids": ids, "total": session.scalar(count_query) or 0}
    rows = session.scalars(
        query.order_by(Transaction.effective_on.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    ).all()
    ids_righe = [row.id for row in rows]
    collegati = _linked_ledger_for_transactions(session, ids_righe)
    rate = _liability_details_for_transactions(session, ids_righe)
    # Gli anni con almeno un movimento: servono al menu a tendina, e non
    # dipendono dai filtri attivi - altrimenti filtrando per il 2025 sparirebbe
    # dall'elenco il 2026, cioe' il modo per tornare indietro.
    anni = [str(int(riga[0])) for riga in session.execute(
        select(distinct(extract("year", Transaction.effective_on)))
        .where(REAL_MOVEMENT).order_by(extract("year", Transaction.effective_on).desc())
    ).all() if riga[0] is not None]
    # Come gli anni: l'elenco non dipende dai filtri attivi, altrimenti filtrando
    # per un goal sparirebbero gli altri dalla tendina.
    obiettivi = [riga[0].strip() for riga in session.execute(
        select(distinct(Transaction.goal)).where(REAL_MOVEMENT, ~blank(Transaction.goal))
        .order_by(Transaction.goal)).all()]
    refunds = dict(session.execute(select(Transaction.refund_of_id, Transaction.id).where(Transaction.refund_of_id.in_([row.id for row in rows]))).all())
    return {"items": [{**transaction_json(row, linked=collegati.get(row.id, []), liability=rate.get(row.id)), "refundedById": refunds.get(row.id)} for row in rows],
            "total": session.scalar(count_query) or 0,
            "offset": offset, "limit": limit, "years": anni, "goals": obiettivi}


def movimenti_per_saldi(session: Session) -> list[Any]:
    """I movimenti nella forma minima che serve ai calcoli sui saldi.

    Solo le colonne che il motore legge davvero: le Row di SQLAlchemy si
    interrogano per attributo esattamente come gli oggetti ORM, ma non devono
    essere costruite una per una.
    """
    return session.execute(select(
        Transaction.effective_on, Transaction.occurred_on, Transaction.transaction_type,
        Transaction.amount, Transaction.account_name, Transaction.destination_name,
    ).where(REAL_MOVEMENT)).all()


@router.get("/api/accounts")
def accounts(at: str | None = Query(None, description="Saldi a questa data (YYYY-MM-DD); assente = oggi"),
             session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.scalars(select(Account).order_by(Account.source_group, Account.name)).all()
    all_transactions = movimenti_per_saldi(session)
    reconciliation = account_reconciliation(rows, all_transactions)
    stime: dict[int, list[Any]] = defaultdict(list)
    for valutazione in session.scalars(select(AccountValuation).order_by(AccountValuation.observed_on.desc())).all():
        stime[valutazione.account_id].append(valutazione)
    con_mercato = conti_con_valore_di_mercato(session)
    # Quanto vale un conto lo decide una funzione sola, la stessa che usa la
    # pagina Patrimonio. Prima questa pagina se lo calcolava per conto suo -
    # somma dei movimenti e basta - e le due pagine potevano divergere per
    # quattro motivi diversi: la data, le valutazioni a mano, il valore di
    # mercato degli investimenti e il segno dei debiti. Due numeri per la stessa
    # cosa e nessun arbitro fra i due: qui l'arbitro e' `account_balances_at`.
    try:
        quando = date.fromisoformat(at) if at else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="Data non valida: usare YYYY-MM-DD")
    saldi = account_balances_at(rows, all_transactions, quando,
                                valutazioni_per_conto(session), rivalutazioni_per_conto(session))
    valore = {riga["account_id"]: riga["balance"] for riga in saldi["accounts"]}
    return {"items": [{
        "id": row.id, "name": row.name, "group": row.source_group,
        # `value` e' quanto vale alla data chiesta: e' il numero della pagina
        # Patrimonio, coi debiti positivi come li' e le valutazioni applicate.
        "value": num(valore[row.id]),
        "startingBalance": num(row.starting_balance),
        # Il costo: quanto dicono i movimenti, senza valutazioni. Per un conto
        # broker e' il versato; per gli altri coincide con `value` al presente.
        # Il saldo "dichiarato" non si espone piu': era la cifra importata dal
        # foglio, nessuno la aggiorna e non si puo' correggere dall'app.
        "calculatedBalance": num(entry["calculated"]),
        "countsInNetWorth": row.counts_in_net_worth, "isActive": row.is_active,
        "isLiquid": row.is_liquid, "isBroker": row.is_broker, "notes": row.notes,
        "needsManualValuation": row.needs_manual_valuation,
        "valuedByLedger": row.id in con_mercato,
        "valuations": [{"id": v.id, "observedOn": v.observed_on.isoformat(),
                        "value": num(v.value), "notes": v.notes} for v in stime.get(row.id, [])],
    } for row, entry in zip(rows, reconciliation)], "at": quando.isoformat()}


@router.get("/api/settings")
def settings(session: Session = Depends(get_session)) -> dict[str, Any]:
    setting_rows = session.scalars(select(AppSetting)).all()
    options: dict[str, list[str]] = defaultdict(list)
    for row in session.scalars(select(LookupOption).order_by(LookupOption.option_group, LookupOption.position)).all(): options[row.option_group].append(row.value)
    # Le chiavi devono esserci sempre, anche vuote: un account appena creato non
    # ha ancora conti, e l'interfaccia ci itera sopra senza chiedere permesso.
    for gruppo in ("colors", "years"):
        options.setdefault(gruppo, [])
    options["categories"] = [item[0] for item in session.execute(select(Transaction.category).distinct().where(REAL_MOVEMENT).order_by(Transaction.category)).all() if item[0]]
    # Le categorie appartengono a un tipo: una spesa non puo' essere "Stipendio".
    # L'elenco unisce quelle gia' usate nei movimenti e quelle pianificate a
    # budget, altrimenti una categoria appena creata nel budget non sarebbe
    # selezionabile su nessun movimento e quindi non potrebbe mai essere usata.
    by_type: dict[str, set[str]] = {kind: set() for kind in ("Income", "Expenses", "Savings")}
    for kind, category in session.execute(select(Transaction.transaction_type, Transaction.category).distinct().where(REAL_MOVEMENT)).all():
        if category and kind in by_type:
            by_type[kind].add(category.strip())
    for kind, category in session.execute(select(BudgetPlan.budget_type, BudgetPlan.category).distinct()).all():
        if category and kind in by_type:
            by_type[kind].add(category.strip())
    # Il vocabolario di partenza di ogni account: senza, chi comincia da zero
    # non troverebbe nessuna categoria da scegliere sul primo movimento.
    for kind, gruppo in (("Expenses", "categories_expenses"), ("Income", "categories_income"),
                         ("Savings", "categories_savings")):
        by_type[kind].update(options.get(gruppo, []))
    # I trasferimenti spostano denaro fra conti: non c'e' niente da categorizzare.
    # Spostare denaro fra conti non e' una categoria da scegliere: ne' un
    # giroconto ne' un versamento su un broker hanno qualcosa da categorizzare.
    categories_by_type = ({kind: sorted(values, key=str.casefold) for kind, values in by_type.items()}
                          | {"Transfers": [], "Investment": [], "Debt": []})
    # Panoramica non deve offrire l'elenco anni "infinito" copiato dal dropdown Excel
    # (options["years"], usato altrove per pianificazione futura): si ferma all'ultimo
    # anno con almeno una transazione o una voce di budget.
    max_transaction_year = session.scalar(select(func.max(extract("year", Transaction.effective_on))).where(REAL_MOVEMENT))
    max_budget_year = session.scalar(select(func.max(extract("year", BudgetPlan.period))))
    max_data_year = int(max(max_transaction_year or 0, max_budget_year or 0)) or date.today().year
    # Il primo anno lo dicono i dati, non un'impostazione: era una scelta da
    # fare a mano che serviva solo a poter sbagliare, e infatti nascondeva gli
    # anni importati piu' vecchi di quello scritto li' dentro.
    min_transaction_year = session.scalar(select(func.min(extract("year", Transaction.effective_on))).where(REAL_MOVEMENT))
    min_budget_year = session.scalar(select(func.min(extract("year", BudgetPlan.period))))
    anni_noti = [int(a) for a in (min_transaction_year, min_budget_year) if a]
    min_year = min(anni_noti) if anni_noti else max_data_year
    min_year = min(min_year, max_data_year)
    options["overviewYears"] = [str(year) for year in range(min_year, max_data_year + 1)]
    # Il budget serve soprattutto prima che l'anno inizi. Offrire solo gli anni
    # che hanno gia' righe rendeva impossibile creare il primo piano futuro.
    ultimo_anno_piano = max(max_data_year, date.today().year + 5)
    anni_piano = sorted({int(year) for year in options["years"] if str(year).isdigit()}
                        | set(range(min_year, ultimo_anno_piano + 1)))
    options["years"] = [str(year) for year in anni_piano]
    budget_years_by_type: dict[str, list[str]] = {}
    for budget_kind in ("Expenses", "Income", "Savings"):
        rows = session.execute(select(distinct(extract("year", BudgetPlan.period))).where(BudgetPlan.budget_type == budget_kind).order_by(extract("year", BudgetPlan.period))).all()
        kind_years = sorted({int(row[0]) for row in rows if row[0] is not None})
        budget_years_by_type[budget_kind] = [str(year) for year in sorted(set(kind_years) | set(anni_piano))]
    return {"settings": {row.key: row.value for row in setting_rows}, "labels": {row.key: row.label for row in setting_rows}, "options": options, "categoriesByType": categories_by_type, "budgetYearsByType": budget_years_by_type}


def budget_actual(session: Session, year: int, month: int, budget_type: str = "Expenses") -> dict[str, float]:
    if budget_type == "Savings":
        # Il risparmio effettivo e' calcolato, non sommato dai movimenti.
        return {savings_category(session).lower(): derived_savings(session, year, month)}
    # Aggregazione case-insensitive sulla categoria: Excel confronta il testo
    # ignorando maiuscole/minuscole (operatore =), quindi facciamo lo stesso qui
    # per non perdere spesa se un futuro import porta un casing diverso da
    # quello canonico del budget.
    rows = session.scalars(select(Transaction).where(
        extract("year", Transaction.effective_on) == year,
        extract("month", Transaction.effective_on) == month,
        Transaction.transaction_type == budget_type, BUDGET_MOVEMENT)).all()
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.category:
            totals[row.category.strip().lower()] += float(row.amount)
    if budget_type in {"Income", "Expenses"} and rows:
        refunds = session.execute(select(Transaction.refund_of_id, Transaction.amount).where(
            Transaction.refund_of_id.in_([row.id for row in rows]), REAL_MOVEMENT)).all()
        for original_id, amount in refunds:
            original = next(row for row in rows if row.id == original_id)
            totals[original.category.strip().lower()] -= float(amount)
    return {key: round(value, 2) for key, value in totals.items()}


# Bisogni e piaceri, e il residuo che passa al mese dopo.
# ---------------------------------------------------------------------------

# "Other" non e' una terza categoria di spesa: e' dove finisce cio' che non hai
# ancora classificato. Tenerla visibile serve proprio a farla svuotare.
CATEGORY_GROUPS = ("Needs", "Wants", "Other")


def category_groups(session: Session) -> dict[str, str]:
    """Il gruppo (bisogno/piacere) di ogni categoria, indicizzato senza maiuscole.

    Il gruppo e' scritto sulle righe di budget, una per mese, ma descrive la
    categoria e non il mese: la spesa non e' un bisogno a gennaio e un piacere
    a febbraio. Quando le righe non concordano vince la piu' recente, che e'
    l'ultima volta che qualcuno ha detto la sua.
    """
    righe = session.execute(
        select(BudgetPlan.category, BudgetPlan.category_group)
        .where(BudgetPlan.category_group.is_not(None))
        .order_by(BudgetPlan.period)
    ).all()
    return {categoria.strip().lower(): gruppo for categoria, gruppo in righe if categoria}


def _needs_wants(session: Session, year: int, month: int | None) -> list[dict[str, Any]]:
    """Ripartizione bisogni/piaceri del periodo, pianificata ed effettiva.

    Guarda tutta la spesa, non solo quella a budget: una categoria su cui hai
    speso senza averla pianificata e' esattamente quella che vuoi vedere.
    """
    gruppi = category_groups(session)
    mesi = [month] if month is not None else range(1, 13)
    speso: dict[str, float] = defaultdict(float)
    for mese in mesi:
        for categoria, valore in budget_actual(session, year, mese, "Expenses").items():
            speso[categoria] += valore
    pianificato: dict[str, float] = defaultdict(float)
    for piano in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == date(year, month, 1) if month is not None else extract("year", BudgetPlan.period) == year,
            BudgetPlan.budget_type == "Expenses")).all():
        pianificato[piano.category.strip().lower()] += num(piano.amount)

    totali = {gruppo: {"planned": 0.0, "actual": 0.0} for gruppo in CATEGORY_GROUPS}
    for chiave, valore in speso.items():
        totali[gruppi.get(chiave, "Other")]["actual"] += valore
    for chiave, valore in pianificato.items():
        totali[gruppi.get(chiave, "Other")]["planned"] += valore

    totale_speso = sum(riga["actual"] for riga in totali.values())
    totale_pianificato = sum(riga["planned"] for riga in totali.values())
    return [{
        "group": gruppo,
        "planned": round(totali[gruppo]["planned"], 2),
        "actual": round(totali[gruppo]["actual"], 2),
        "plannedShare": round(totali[gruppo]["planned"] / totale_pianificato * 100, 1) if totale_pianificato else 0,
        "actualShare": round(totali[gruppo]["actual"] / totale_speso * 100, 1) if totale_speso else 0,
    } for gruppo in CATEGORY_GROUPS]


def _budget_balance(session: Session, year: int, month: int | None = None) -> dict[str, Any]:
    """Il periodo quadra? Entrate pianificate meno spese e risparmi pianificati.

    E' la domanda che distingue un budget da una lista di importi: se resta
    denaro non assegnato nessuno sa dove andra' a finire, e se ne manca il
    piano e' gia' sbagliato prima di cominciare. Senza mese la domanda vale
    sull'anno intero, che e' come si pianifica dalla griglia annuale.
    """
    righe = session.execute(
        select(BudgetPlan.budget_type, func.sum(BudgetPlan.amount))
        .where(BudgetPlan.period == date(year, month, 1) if month is not None
               else extract("year", BudgetPlan.period) == year)
        .group_by(BudgetPlan.budget_type)
    ).all()
    per_tipo = {tipo: num(totale) for tipo, totale in righe}
    entrate = per_tipo.get("Income", 0.0)
    spese = per_tipo.get("Expenses", 0.0)
    risparmi = per_tipo.get("Savings", 0.0)
    return {
        "income": entrate, "expenses": spese,
        # Il risparmio e' quello che avanza, non un terzo numero indipendente:
        # si ricalcola qui invece di sommare le righe Savings, cosi' la card
        # dice la verita' anche se una riga non fosse ancora stata allineata.
        # `or 0.0` evita il "-0,00" che in virgola mobile capita e sembra un bug.
        "savings": round(entrate - spese, 2) or 0.0,
        "storedSavings": risparmi,
        # Senza entrate pianificate non c'e' niente da dedurre: e' un budget a
        # meta', e dirlo e' piu' utile che mostrare un rosso enorme.
        "hasIncomePlan": entrate > 0,
    }


def sync_savings_plan(session: Session, period: date) -> Decimal:
    """Riporta il risparmio pianificato di un mese a entrate meno spese pianificate.

    Il risparmio effettivo l'app lo calcola gia' cosi' (``derived_savings``):
    tenerlo scritto a mano dall'altra parte significava poter pianificare un
    risparmio che non discende da nient'altro, e accorgersene solo guardando la
    quadratura. Ora e' un numero che risulta, non un numero che si digita.

    Puo' venire negativo, ed e' giusto che si veda: vuol dire che il piano
    spende piu' di quanto prevede di incassare.
    """
    totali = {tipo: valore for tipo, valore in session.execute(
        select(BudgetPlan.budget_type, func.sum(BudgetPlan.amount))
        .where(BudgetPlan.period == period, BudgetPlan.budget_type.in_(("Income", "Expenses")))
        .group_by(BudgetPlan.budget_type)).all()}
    atteso = Decimal(totali.get("Income") or 0) - Decimal(totali.get("Expenses") or 0)
    riga = session.scalar(select(BudgetPlan).where(
        BudgetPlan.period == period, BudgetPlan.budget_type == "Savings"))
    if riga is None:
        # Un mese senza niente pianificato non ha un risparmio da dedurre: la
        # riga si crea quando c'e' un piano da cui dedurlo.
        if not totali:
            return Decimal(0)
        session.add(BudgetPlan(period=period, budget_type="Savings",
                               category=savings_category(session), amount=atteso))
    else:
        riga.amount = atteso
    return atteso


def previous_month_leftover(session: Session, year: int, month: int,
                            budget_type: str = "Expenses") -> dict[str, float]:
    """Quanto era avanzato (o mancato) nel mese precedente, categoria per categoria.

    E' un dato che si legge, non un dato che entra nei conti: il budget del mese
    resta quello pianificato. Un riporto che si somma da solo al disponibile
    rende impossibile capire da dove viene la cifra che si sta guardando, ed e'
    esattamente il tipo di magia che poi si paga rileggendo un numero sbagliato.

    Solo il mese prima, non tutti quelli passati: "ti erano avanzati 100 euro a
    luglio" si capisce, la somma algebrica di otto mesi no.
    """
    # ponytail: dentro l'anno solare, come il resto del budget. Gennaio non
    # guarda dicembre dell'anno prima: il piano ricomincia.
    if budget_type != "Expenses" or month <= 1:
        return {}
    precedente = date(year, month - 1, 1)
    pianificato: dict[str, float] = defaultdict(float)
    for piano in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == precedente, BudgetPlan.budget_type == budget_type)).all():
        pianificato[piano.category.strip().lower()] += num(piano.amount)
    effettivo: dict[str, float] = defaultdict(float)
    for categoria, totale in session.execute(select(Transaction.category, func.sum(Transaction.amount)).where(
            extract("year", Transaction.effective_on) == year,
            extract("month", Transaction.effective_on) == month - 1,
            Transaction.transaction_type == budget_type,
            BUDGET_MOVEMENT).group_by(Transaction.category)).all():
        if categoria:
            effettivo[categoria.strip().lower()] += float(totale or 0)
    # I rimborsi nettono dall'importo della categoria dell'originale nello stesso
    # mese dell'originale (non del rimborso: uno puo' rimborsare a gennaio una
    # spesa di novembre, e il netting va applicato dove il budget era stato
    # registrato). Senza questo, un rimborso di 200 su una spesa di 500 fa'
    # sembrare di essere sforati di 200 anche se il budget era ok.
    if budget_type in {"Income", "Expenses"}:
        Originale = aliased(Transaction, name="originale")
        rimborsi = session.execute(select(
            Originale.category, Transaction.amount,
        ).join(Originale, Transaction.refund_of_id == Originale.id).where(
            REAL_MOVEMENT,
            Transaction.refund_of_id.is_not(None),
            Originale.counts_in_budget.is_(True),
            Originale.transaction_type == budget_type,
            extract("year", Originale.effective_on) == year,
            extract("month", Originale.effective_on) == month - 1,
        )).all()
        for categoria_originale, importo in rimborsi:
            if categoria_originale:
                effettivo[categoria_originale.strip().lower()] -= float(importo or 0)
    avanzi = {chiave: round(valore - effettivo.get(chiave, 0.0), 2) for chiave, valore in pianificato.items()}
    return {chiave: valore for chiave, valore in avanzi.items() if valore}


def _mensili_per_categoria(session: Session, year: int, month: int, budget_type: str,
                           months_back: int) -> tuple[list[tuple[int, int]], dict[str, dict[tuple[int, int], float]]]:
    """Totali mensili per categoria nei mesi che precedono quello indicato.

    Serve ai suggerimenti del budget e alla stima di fine mese: sono la stessa
    lettura dello storico, e averne una copia per uso significa poterle far
    dire due cose diverse.
    """
    finestra = [((year * 12 + month - 1) - passo) for passo in range(1, months_back + 1)]
    periodi = [(indice // 12, indice % 12 + 1) for indice in finestra]
    per_categoria: dict[str, dict[tuple[int, int], float]] = defaultdict(dict)
    if budget_type == "Savings":
        categoria = savings_category(session)
        for anno, mese in periodi:
            per_categoria[categoria][(anno, mese)] = derived_savings(session, anno, mese)
        return periodi, per_categoria
    # La finestra e' contigua: basta un intervallo di date, invece di leggere
    # tutta la storia e scartarla in Python.
    inizio = date(*min(periodi), 1)
    ultimo_anno, ultimo_mese = max(periodi)
    fine = date(ultimo_anno + 1, 1, 1) if ultimo_mese == 12 else date(ultimo_anno, ultimo_mese + 1, 1)
    righe = session.execute(select(
        extract("year", Transaction.effective_on), extract("month", Transaction.effective_on),
        Transaction.category, func.sum(Transaction.amount),
    ).where(
        Transaction.effective_on >= inizio,
        Transaction.effective_on < fine,
        Transaction.transaction_type == budget_type,
        BUDGET_MOVEMENT,
    ).group_by(
        extract("year", Transaction.effective_on), extract("month", Transaction.effective_on),
        Transaction.category,
    )).all()
    for anno, mese, categoria, totale in righe:
        if categoria:
            per_categoria[categoria.strip()][(int(anno), int(mese))] = float(totale or 0)
    return periodi, per_categoria


def _mediana(valori: list[float]) -> float:
    ordinati = sorted(valori)
    meta = len(ordinati) // 2
    if not ordinati:
        return 0.0
    return ordinati[meta] if len(ordinati) % 2 else (ordinati[meta - 1] + ordinati[meta]) / 2


def stima_fine_periodo(session: Session, year: int, month: int, oggi: date) -> dict[str, Any] | None:
    """Dove va a finire il mese, senza moltiplicare per i giorni passati.

    Per ogni categoria si prende **il maggiore fra quello che hai gia' speso e
    quello che di solito spendi in un mese**. Proiettare la spesa sui giorni
    trascorsi darebbe numeri assurdi: l'affitto pagato il primo del mese
    diventerebbe trentamila euro a fine mese.

    E' un pavimento, non una previsione: puo' solo essere rivista in su, e su
    una categoria che quest'anno corre piu' del solito resta ottimista finche'
    non supera la propria mediana. Il pregio e' che non dice mai una sciocchezza.
    """
    inizio = date(year, month, 1)
    mese_corrente = date(oggi.year, oggi.month, 1)
    pianificato_mese = num(session.scalar(select(func.coalesce(func.sum(BudgetPlan.amount), 0)).where(
        BudgetPlan.period == inizio, BudgetPlan.budget_type == "Expenses")))
    speso_mese = budget_actual(session, year, month, "Expenses")
    if inizio < mese_corrente:
        # Un mese chiuso non si stima, si legge: la card resta per non far
        # ballare la griglia, ma dice che non c'e' niente da prevedere.
        return {"state": "closed", "estimate": None, "planned": pianificato_mese,
                "spentSoFar": round(sum(speso_mese.values()), 2)}
    # La finestra storica parte sempre da oggi, anche guardando un mese futuro:
    # ancorarla al mese scelto ci infilerebbe dentro mesi ancora vuoti, e le
    # mediane verrebbero schiacciate da una fila di zeri.
    periodi, storico = _mensili_per_categoria(session, oggi.year, oggi.month, "Expenses", 6)
    mediane = {nome.strip().lower(): _mediana([valori.get(periodo, 0.0) for periodo in periodi])
               for nome, valori in storico.items()}
    stima = sum(max(speso_mese.get(chiave, 0.0), mediane.get(chiave, 0.0))
                for chiave in set(speso_mese) | set(mediane))
    return {"state": "current" if inizio == mese_corrente else "future",
            "estimate": round(stima, 2), "planned": pianificato_mese,
            "spentSoFar": round(sum(speso_mese.values()), 2)}


@router.get("/api/budget-suggestions")
def budget_suggestions(year: int, month: int, budget_type: str = "Expenses", months_back: int = 6,
                       session: Session = Depends(get_session)) -> dict[str, Any]:
    """Quanto e' costata davvero ogni categoria nei mesi prima di questo.

    Serve a compilare il budget guardando i propri numeri invece che a memoria.
    La mediana, non la media: il mese in cui hai comprato la lavatrice non deve
    diventare il tuo budget di tutti i mesi. I mesi senza spesa contano come
    zero - in un budget mensile "non ho speso niente" e' un'informazione - ma
    quanti sono si vede, cosi' un suggerimento costruito su due mesi non passa
    per solido.
    """
    months_back = max(1, min(int(months_back), 24))
    periodi, per_categoria = _mensili_per_categoria(session, year, month, budget_type, months_back)

    items = []
    for categoria, valori in per_categoria.items():
        mensili = [round(valori.get(periodo, 0.0), 2) for periodo in periodi]
        con_spesa = [valore for valore in mensili if valore]
        if not con_spesa:
            continue
        ordinati = sorted(mensili)
        meta = len(ordinati) // 2
        mediana = ordinati[meta] if len(ordinati) % 2 else (ordinati[meta - 1] + ordinati[meta]) / 2
        items.append({
            "category": categoria,
            "median": round(mediana, 2),
            "average": round(sum(mensili) / len(mensili), 2),
            "max": max(mensili),
            "monthsWithSpending": len(con_spesa),
            "monthsConsidered": len(mensili),
        })
    items.sort(key=lambda item: item["median"], reverse=True)
    return {"period": f"{year}-{month:02d}", "monthsBack": months_back, "budgetType": budget_type, "items": items}


def _totali_mensili(session: Session, year: int, budget_type: str) -> dict[int, float]:
    """Totale per mese di un tipo di movimento, in una sola interrogazione."""
    if budget_type in {"Income", "Expenses"}:
        return {month: round(sum(budget_actual(session, year, month, budget_type).values()), 2) for month in range(1, 13)}
    if budget_type == "Savings":
        # Il risparmio non si somma dai movimenti - quelli non esistono piu' -
        # ma si ricava da entrate meno spese, come ovunque altrove. Senza
        # questo ramo si cadeva nella query generica qui sotto, che per i
        # risparmi non trova nulla e restituisce dodici mesi a zero.
        entrate = _totali_mensili(session, year, "Income")
        spese = _totali_mensili(session, year, "Expenses")
        return {mese: round(entrate.get(mese, 0.0) - spese.get(mese, 0.0), 2) for mese in range(1, 13)}
    righe = session.execute(select(
        extract("month", Transaction.effective_on), func.sum(Transaction.amount),
    ).where(
        extract("year", Transaction.effective_on) == year,
        Transaction.transaction_type == budget_type,
        BUDGET_MOVEMENT,
    ).group_by(extract("month", Transaction.effective_on))).all()
    return {int(mese): float(totale or 0) for mese, totale in righe}


def budget_actual_year(session: Session, year: int, budget_type: str = "Expenses") -> dict[int, dict[str, float]]:
    """Lo speso di un anno intero, mese per mese e categoria per categoria.

    Una interrogazione al posto di una per cella: la griglia annuale ne chiedeva
    dodici per ogni categoria, e l'andamento la ricostruiva per ogni anno. Con
    trenta categorie e tre anni erano piu' di mille interrogazioni per disegnare
    una pagina, ed e' il motivo per cui l'app sembrava pensarci su.
    """
    if budget_type == "Savings":
        categoria = savings_category(session).strip().lower()
        mensili = _totali_mensili(session, year, "Savings")
        return {mese: {categoria: mensili.get(mese, 0.0)} for mese in range(1, 13)}
    per_mese: dict[int, dict[str, float]] = {mese: defaultdict(float) for mese in range(1, 13)}
    # Una query per le transazioni "budgeable" dell'anno, aggregate per mese e
    # categoria. La category va normalizzata come in `budget_actual` (Excel
    # paragona il testo case-insensitive, e i budget possono avere casing misto).
    righe = session.execute(select(
        extract("month", Transaction.effective_on), Transaction.category, Transaction.amount,
    ).where(
        extract("year", Transaction.effective_on) == year,
        Transaction.transaction_type == budget_type,
        BUDGET_MOVEMENT,
    )).all()
    for mese, categoria, importo in righe:
        if categoria:
            per_mese[int(mese)][categoria.strip().lower()] += float(importo or 0)
    # I rimborsi nettono dall'importo della categoria dell'originale nello stesso
    # mese dell'originale (non del rimborso: uno puo' rimborsare a gennaio una
    # spesa di novembre, e il netting va applicato dove il budget era stato
    # registrato). Una sola query con self-join: l'originale deve essere
    # budgeable (altrimenti non era nel `per_mese`); il rimborso e' solo
    # REAL_MOVEMENT perche' in creazione gli si toglie `counts_in_budget`.
    if budget_type in {"Income", "Expenses"}:
        Originale = aliased(Transaction, name="originale")
        rimborsi = session.execute(select(
            extract("month", Originale.effective_on), Originale.category, Transaction.amount,
        ).join(Originale, Transaction.refund_of_id == Originale.id).where(
            REAL_MOVEMENT,
            Transaction.refund_of_id.is_not(None),
            Originale.counts_in_budget.is_(True),
            Originale.transaction_type == budget_type,
            extract("year", Originale.effective_on) == year,
        )).all()
        for mese, categoria_originale, importo in rimborsi:
            if categoria_originale:
                per_mese[int(mese)][categoria_originale.strip().lower()] -= float(importo or 0)
    return {mese: {chiave: round(valore, 2) for chiave, valore in valori.items()}
            for mese, valori in per_mese.items()}


@router.get("/api/budgets")
def budgets(year: int, month: int, budget_type: str = "Expenses", session: Session = Depends(get_session)) -> dict[str, Any]:
    plans = session.scalars(select(BudgetPlan).where(BudgetPlan.period == date(year, month, 1), BudgetPlan.budget_type == budget_type).order_by(BudgetPlan.category)).all(); actual = budget_actual(session, year, month, budget_type)
    gruppi = category_groups(session)
    avanzi = previous_month_leftover(session, year, month, budget_type)
    items = [{"id": plan.id, "category": plan.category, "categoryLabel": plan.category,
              "categoryGroup": plan.category_group or gruppi.get(plan.category.strip().lower()),
              "amount": num(plan.amount), "actual": actual.get(plan.category.strip().lower(), 0),
              # Solo informativo: non entra in nessun totale.
              "previousLeftover": avanzi.get(plan.category.strip().lower(), 0.0)}
             for plan in plans]
    actual_total = sum(item["actual"] for item in items)
    if budget_type == "Savings":
        # Calcolato, non sommato dalle righe: senza un piano per quel mese la
        # giuntura non produce righe e il risparmio effettivo, che invece c'e',
        # veniva mostrato a zero.
        actual_total = derived_savings(session, year, month)
    return {"items": items, "plannedTotal": sum(item["amount"] for item in items),
            "actualTotal": actual_total,
            # La quadratura del solo mese: la card del piano mensile mostrava
            # quella dell'anno fino a quel mese, sotto il titolo "il mese".
            "balance": _budget_balance(session, year, month)}


@router.get("/api/calculations")
def calculations(year: int, month: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    income, expenses = (period_total(session, year, month, kind) for kind in ("Income", "Expenses"))
    savings = derived_savings(session, year, month)
    today = date.today()
    days_in_period = calendar.monthrange(year, month)[1]
    if year == today.year:
        if month == today.month:
            days_passed = today.day
        elif month < today.month:
            days_passed = days_in_period
        else:
            days_passed = 0
    elif year < today.year:
        days_passed = days_in_period
    else:
        days_passed = 0
    # `trackingBalance` resta per compatibilita' ma vale sempre zero: il
    # risparmio *e'* entrate meno spese, quindi la sottrazione si annulla.
    # Quello che ha un senso da mostrare e' il risparmio stesso.
    return {"period": f"{year}-{month:02d}", "income": income, "expenses": expenses, "savings": savings,
            "trackingBalance": round(income - expenses - savings, 2),
            "savingsRate": round(savings / income, 4) if income else None,
            "daysInPeriod": days_in_period, "daysPassed": days_passed, "budgetDelta": {}, "accountDifferences": 0}


@router.get("/api/budget-annual")
def budget_annual(year: int, budget_type: str = "Expenses", session: Session = Depends(get_session)) -> dict[str, Any]:
    plans = session.scalars(select(BudgetPlan).where(extract("year", BudgetPlan.period) == year, BudgetPlan.budget_type == budget_type)).all(); by_category: dict[str, list[BudgetPlan]] = defaultdict(list)
    for plan in plans: by_category[plan.category].append(plan)
    items = []
    speso = budget_actual_year(session, year, budget_type)
    for category, rows in by_category.items():
        chiave = category.strip().lower()
        indexed = {row.period.month: row for row in rows}; months = []
        for month in range(1, 13):
            row = indexed.get(month); actual = speso.get(month, {}).get(chiave, 0); months.append({"month": month, "id": row.id if row else None, "amount": num(row.amount) if row else 0, "actual": actual})
        items.append({"category": category, "categoryLabel": category, "categoryGroup": rows[0].category_group, "months": months, "plannedTotal": sum(value["amount"] for value in months), "actualTotal": sum(value["actual"] for value in months)})
    # I `monthTotals` devono riflettere lo speso reale del mese, non solo quello
    # delle categorie che hanno un BudgetPlan: per un anno senza piano serve
    # comunque vedere la linea dei movimenti reali, altrimenti le tendenze
    # mostrano zero su tutti gli anni precedenti al primo budget digitato.
    actual_per_mese = _totali_mensili(session, year, budget_type)
    totals = [{"month": month, "label": MONTHS[month - 1],
               "planned": sum(item["months"][month - 1]["amount"] for item in items),
               "actual": round(actual_per_mese.get(month, 0.0), 2)}
              for month in range(1, 13)]
    return {"year": year, "items": items, "monthTotals": totals, "balance": _budget_balance(session, year)}


@router.get("/api/budget-dashboard")
def budget_dashboard(year: int, month: int | None = None, budget_type: str = "Expenses", session: Session = Depends(get_session)) -> dict[str, Any]:
    if month is None:
        annual = budget_annual(year, budget_type, session)
        source = [{"category": item["category"], "categoryLabel": item["categoryLabel"],
                   "categoryGroup": item["categoryGroup"], "amount": item["plannedTotal"],
                   "previousLeftover": 0.0, "actual": item["actualTotal"]}
                  for item in annual["items"]]
        months = annual["monthTotals"]
    else:
        data = budgets(year, month, budget_type, session)
        source = data["items"]
        months = budget_annual(year, budget_type, session)["monthTotals"]
    categories = [{"category": item["category"], "categoryLabel": item["categoryLabel"],
                   "categoryGroup": item["categoryGroup"], "planned": item["amount"],
                   "previousLeftover": item["previousLeftover"], "actual": item["actual"],
                   "variance": item["amount"] - item["actual"],
                   "usage": round(item["actual"] / item["amount"] * 100, 1) if item["amount"] else None}
                  for item in source]
    planned = sum(item["planned"] for item in categories)
    actual = sum(item["actual"] for item in categories)
    if budget_type == "Savings":
        # Il risparmio e' calcolato, non sommato dalle righe di piano: nei
        # periodi non ancora pianificati non ci sono righe da sommare, e
        # l'effettivo - che invece esiste - risultava zero.
        actual = derived_savings(session, year, month)
    return {"period": _period_label(year, month), **_period_ref(year, month),
            "plannedTotal": planned, "actualTotal": actual,
            "remaining": round(planned - actual, 2),
            "usage": round(actual / planned * 100, 1) if planned else 0,
            "overBudgetCategories": sum(1 for item in categories if item["variance"] < 0),
            "categories": categories,
            "balance": _budget_balance(session, year, month),
            "groups": _needs_wants(session, year, month) if budget_type == "Expenses" else [],
            "months": months,
            "topTransactions": transactions(10, session, budget_only=True)["items"]}


@router.get("/api/budget-trends/available-years")
def budget_trends_available_years(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Anni che hanno almeno una transazione reale o un piano di budget.

    Mostrare altrimenti una lista fino al 2050 (o simili) significherebbe
    offrire anni in cui non c'e' niente da confrontare: la tendenza resterebbe
    piatta a zero e l'utente non capirebbe perche' gliel'abbiamo proposta.
    """
    anni_tx = {int(y) for y in session.execute(select(distinct(extract("year", Transaction.effective_on))).where(REAL_MOVEMENT)).scalars() if y}
    anni_piano = {int(y) for y in session.execute(select(distinct(extract("year", BudgetPlan.period))).where(BudgetPlan.budget_type == "Expenses")).scalars() if y}
    anni = sorted(anni_tx | anni_piano)
    return {"years": anni}


@router.get("/api/budget-trends")
def budget_trends(years: str | None = None, start_year: int = 2024, end_year: int = 2027, budget_type: str = "Expenses", session: Session = Depends(get_session)) -> dict[str, Any]:
    # `years` (lista CSV) ha la precedenza: l'utente sceglie esattamente quali
    # confrontare. Il vecchio start_year/end_year resta per retro-compatibilita'
    # (qualche link condiviso o un client esterno) ma viene abbandonato dal FE.
    if years:
        try:
            anno_list = sorted({int(y) for y in years.split(",") if str(y).strip().isdigit()})
        except ValueError:
            anno_list = []
        if not anno_list:
            anno_list = list(range(start_year, end_year + 1))
    else:
        anno_list = list(range(start_year, end_year + 1))
    years_out = []
    for year in anno_list:
        months = budget_annual(year, budget_type, session)["monthTotals"]; years_out.append({"year": year, "plannedTotal": sum(row["planned"] for row in months), "actualTotal": sum(row["actual"] for row in months), "months": months})
    comparison = []
    if years_out:
        for month_index in range(12):
            row: dict[str, Any] = {"month": years_out[0]["months"][month_index]["label"]}
            for year_entry in years_out:
                row[str(year_entry["year"])] = year_entry["months"][month_index]["actual"]
            comparison.append(row)
    return {"years": years_out, "comparison": comparison, "categories": []}


GOAL_KINDS = ("contributions", "net_worth", "portfolio")

# Sotto questo scarto fra avanzamento e tempo trascorso il goal e' "in leggero
# ritardo"; sotto il doppio, "in ritardo". Sono due soglie scelte, non due
# verita': stanno qui, in un posto solo, per poterle cambiare in un posto solo.
SCARTO_LIEVE = -0.10


def _mesi_fra(inizio: date, fine: date) -> int:
    """Mesi pieni fra due date, mai meno di uno.

    Meno di uno vorrebbe dire dividere per zero, e "ti serve infinito al mese"
    non e' una risposta utile a nessuno.
    """
    return max((fine.year - inizio.year) * 12 + (fine.month - inizio.month), 1)


def _ritmo_goal(corrente: float, target: float, target_date: date | None,
                completato: bool, oggi: date) -> dict[str, Any]:
    """Quanto serve al mese per arrivare in tempo.

    Senza scadenza non si inventa un ritmo: un goal senza data e' un desiderio,
    e mostrargli accanto un numero al mese lo farebbe sembrare un piano.
    """
    mancante = round(max(target - corrente, 0), 2)
    if completato or target_date is None or mancante <= 0:
        return {"monthlyNeeded": None, "weeklyNeeded": None, "monthsLeft": None,
                "overdue": bool(target_date and target_date < oggi and not completato)}
    if target_date < oggi:
        # Scaduto: si dice quanto manca, non un ritmo che non esiste piu'.
        return {"monthlyNeeded": None, "weeklyNeeded": None, "monthsLeft": 0, "overdue": True}
    mesi = _mesi_fra(oggi, target_date)
    al_mese = round(mancante / mesi, 2)
    return {"monthlyNeeded": al_mese, "weeklyNeeded": round(al_mese * 12 / 52, 2),
            "monthsLeft": mesi, "overdue": False}


def _stato_goal(corrente: float, iniziale: float, target: float,
                start_date: date | None, target_date: date | None,
                completato: bool, oggi: date) -> dict[str, Any]:
    """In linea o in ritardo: quanto e' stato fatto contro quanto tempo e' passato."""
    if completato:
        return {"status": "completed", "timeProgress": None, "gap": None}
    if start_date is None or target_date is None or target_date <= start_date or target <= iniziale:
        # Senza due date e un traguardo piu' in la' del punto di partenza non
        # c'e' niente da confrontare: meglio nessuno stato che uno inventato.
        return {"status": None, "timeProgress": None, "gap": None}
    avanzamento = (corrente - iniziale) / (target - iniziale)
    tempo = (oggi - start_date).days / (target_date - start_date).days
    tempo = min(max(tempo, 0.0), 1.0)
    scarto = avanzamento - tempo
    stato = "on_track" if scarto >= 0 else ("slightly_behind" if scarto >= SCARTO_LIEVE else "behind")
    return {"status": stato, "timeProgress": round(tempo * 100, 1), "gap": round(scarto * 100, 1)}


def _movimenti_goal(session: Session, goal: Goal) -> list[tuple[date, Decimal]]:
    """I movimenti taggati col goal, con data e importo gia' col verso giusto.

    Se il goal dichiara un `target_account` - il salvadanaio verso cui accumula -
    un movimento che ci arriva conta in positivo e uno che ne parte in negativo.
    Serve perche' un goal `contributions` misura quanto ci hai messo dentro:
    sommare l'importo e basta faceva salire "quanto ho investito" ogni volta che
    disinvestivi.

    Senza `target_account` non si indovina niente: si somma come si e' sempre
    fatto. Il conto lo dichiara chi crea il goal, non il codice guardando i dati.

    Totale e storico mensile leggono queste stesse righe: due somme con regole
    diverse darebbero un grafico che contraddice il numero sopra.
    """
    righe = session.execute(select(
        Transaction.effective_on, Transaction.transaction_type, Transaction.account_name,
        Transaction.destination_name, Transaction.amount,
    ).where(Transaction.goal == goal.name, REAL_MOVEMENT)).all()
    salvadanaio = normalized_name(goal.target_account)
    firmati: list[tuple[date, Decimal]] = []
    for riga in righe:
        if salvadanaio and normalized_name(riga.account_name) == salvadanaio \
                and normalized_name(riga.destination_name) != salvadanaio:
            # Esce dal salvadanaio: stesso verso che il movimento ha sul saldo
            # del conto, cosi' il goal non puo' raccontare l'opposto dell'estratto.
            importo = source_effect(riga.transaction_type, riga.amount)
        else:
            importo = Decimal(str(riga.amount))
        firmati.append((riga.effective_on, importo))
    return firmati


class BasiStorico(NamedTuple):
    """Le serie che non dipendono dal singolo goal, calcolate una volta sola."""
    linea: list[dict[str, Any]]
    conti: list[Account]
    movimenti: list[Any]
    valutazioni: dict[int, Any]
    rivalutazioni: dict[int, list[tuple[date, Decimal]]]


def basi_storico(session: Session) -> BasiStorico:
    linea = portfolio_timeline(session)
    return BasiStorico(linea,
                       [c for c in session.scalars(select(Account)).all() if c.counts_in_net_worth],
                       movimenti_per_saldi(session), valutazioni_per_conto(session),
                       rivalutazioni_per_conto(session))


def _valore_corrente_goal(session: Session, goal: Goal, linked: float, oggi: date,
                          basi: BasiStorico | None = None,
                          patrimonio: float | None = None) -> float:
    """Il valore corrente di un goal, dalla fonte che il suo tipo dichiara.

    Con `net_worth` e `portfolio` il valore arriva **solo** dalla fonte
    calcolata: sommarci anche `starting_amount` conterebbe due volte gli stessi
    soldi, perche' quella cifra e' gia' dentro il patrimonio.
    """
    if goal.kind == "net_worth":
        if patrimonio is not None:
            return patrimonio
        return num(_net_worth_breakdown(session, oggi.year, oggi.month)["total"])
    if goal.kind == "portfolio":
        linea = basi.linea if basi is not None else portfolio_timeline(session)
        istantanea = portfolio_state_at(linea, oggi)
        return num(istantanea["marketValue"] if istantanea else 0)
    # Due float gia' arrotondati sommati riportano la coda binaria (…25999999998):
    # arrotondare qui evita che finisca al client.
    return num(num(goal.starting_amount) + linked)


@router.get("/api/goals")
def goals(session: Session = Depends(get_session)) -> dict[str, Any]:
    items = []
    today = date.today()
    elenco = session.scalars(select(Goal).order_by(Goal.name)).all()
    # Calcolate una volta per tutta la richiesta, e solo se qualcuno le usa:
    # sono le stesse per ogni goal patrimoniale.
    basi = basi_storico(session) if any(g.kind in ("net_worth", "portfolio") for g in elenco) else None
    # Stesso mese per tutti: una volta sola, e riusando le serie gia' pronte.
    patrimonio_oggi = (num(_net_worth_breakdown(session, today.year, today.month,
                                                basi.movimenti, basi.linea)["total"])
                       if basi is not None and any(g.kind == "net_worth" for g in elenco) else None)
    for goal in elenco:
        firmati = _movimenti_goal(session, goal)
        quanti, linked = len(firmati), num(sum((importo for _, importo in firmati), Decimal("0")))
        current = _valore_corrente_goal(session, goal, linked, today, basi, patrimonio_oggi)
        target = num(goal.target_amount); iniziale = num(goal.starting_amount)
        completato = bool(goal.completed_at)
        history = _goal_history(session, goal, today, firmati, basi)
        items.append({"id": goal.id, "name": goal.name, "kind": goal.kind,
                      "targetAccount": goal.target_account,
                      "startingAmount": iniziale, "targetAmount": target,
                      "startDate": goal.start_date.isoformat() if goal.start_date else None,
                      "targetDate": goal.target_date.isoformat() if goal.target_date else None,
                      "completedAt": goal.completed_at.isoformat() if goal.completed_at else None,
                      "linkedAmount": linked, "linkedMovements": quanti, "currentAmount": current,
                      "remainingAmount": round(max(target - current, 0), 2),
                      "progress": round(min(current / target, 1) * 100, 1) if target else 0,
                      "completed": completato, "history": history,
                      **_ritmo_goal(current, target, goal.target_date, completato, today),
                      **_stato_goal(current, iniziale, target, goal.start_date, goal.target_date, completato, today)})
    # Quanto chiedono al mese tutti i goal attivi, contro quello che il piano
    # mette da parte: la stessa quadratura del budget, applicata qui.
    richiesto = round(sum(item["monthlyNeeded"] or 0 for item in items if not item["completed"]), 2)
    piano = _budget_balance(session, today.year, today.month)
    return {"items": items,
            "active": sum(not item["completed"] for item in items),
            "completed": sum(item["completed"] for item in items),
            "targetTotal": sum(item["targetAmount"] for item in items),
            "currentTotal": sum(item["currentAmount"] for item in items),
            "monthlyNeededTotal": richiesto,
            "plannedSavings": piano["savings"],
            # Senza entrate pianificate nel mese corrente il confronto non si
            # puo' fare: dirlo e' piu' utile che mostrare uno zero.
            "hasPlannedSavings": piano["hasIncomePlan"],
            "monthlyGap": round(piano["savings"] - richiesto, 2)}


def _storico_patrimoniale(session: Session, goal: Goal, today: date,
                          basi: BasiStorico | None = None) -> list[dict[str, Any]]:
    """La serie mensile di un goal `net_worth` o `portfolio`.

    Il portafoglio ha gia' la sua linea mensile; il patrimonio si compone dei
    saldi a fine mese piu' quella linea, con le stesse funzioni della pagina
    Patrimonio.
    """
    inizio = (goal.start_date or today).replace(day=1)
    if inizio > today.replace(day=1):
        return []
    mesi: list[tuple[int, int]] = []
    anno, mese = inizio.year, inizio.month
    while (anno, mese) <= (today.year, today.month):
        mesi.append((anno, mese))
        anno, mese = (anno + 1, 1) if mese == 12 else (anno, mese + 1)
    tagli = [date(a, m, calendar.monthrange(a, m)[1]) for a, m in mesi]
    if basi is None:
        basi = basi_storico(session)
    mercato = [num(stato["marketValue"]) if (stato := portfolio_state_at(basi.linea, taglio)) else 0.0
               for taglio in tagli]
    if goal.kind == "portfolio":
        valori = mercato
    else:
        # Il mercato e' gia' dentro i saldi, sul conto investimenti: sommarlo
        # un'altra volta raddoppierebbe il portafoglio.
        saldi = account_balances_series(basi.conti, basi.movimenti, tagli,
                                        basi.valutazioni, basi.rivalutazioni)
        valori = [round(s["totals"]["bank"] + s["totals"]["asset"] - s["totals"]["liability"], 2)
                  for s in saldi]
    return [{"label": f"{MONTHS[m - 1]} {str(a)[-2:]}", "amount": valore}
            for (a, m), valore in zip(mesi, valori)]


def _goal_history(session: Session, goal: Goal, today: date,
                  firmati: list[tuple[date, Decimal]] | None = None,
                  basi: BasiStorico | None = None) -> list[dict[str, Any]]:
    """Cumulativo mensile del `currentAmount` di un goal da start_date a oggi.

    Per i goal patrimoniali la curva esiste gia' altrove e si riusa: due
    implementazioni della stessa serie divergono al primo mese storto, e il
    grafico del goal finirebbe per raccontare qualcosa di diverso dalla pagina
    Patrimonio.
    """
    if goal.kind in ("net_worth", "portfolio"):
        return _storico_patrimoniale(session, goal, today, basi)
    if firmati is None:
        firmati = _movimenti_goal(session, goal)
    earliest_tx = min((quando for quando, _ in firmati), default=None)
    if goal.start_date:
        start = goal.start_date
    elif earliest_tx:
        start = earliest_tx
    else:
        return []
    start = start.replace(day=1)
    end = today.replace(day=1)
    if end < start:
        return []
    months: list[tuple[int, int]] = []
    cursor_year, cursor_month = start.year, start.month
    while (cursor_year, cursor_month) <= (end.year, end.month):
        months.append((cursor_year, cursor_month))
        cursor_month += 1
        if cursor_month > 12:
            cursor_month = 1
            cursor_year += 1
    by_month: dict[tuple[int, int], float] = defaultdict(float)
    for quando, importo in firmati:
        by_month[(quando.year, quando.month)] += num(importo)
    history: list[dict[str, Any]] = []
    cumulative = float(goal.starting_amount)
    for year, month in months:
        cumulative += by_month.get((year, month), 0)
        history.append({"label": f"{MONTHS[month - 1]} {str(year)[-2:]}", "amount": round(cumulative, 2)})
    return history


@router.get("/api/net-worth")
def net_worth(year: int, month: int = Query(0, ge=0, le=12, description="0 = anno intero (cumulato al 31/12)"),
              months: int = Query(12, ge=1, le=120, description="Ampiezza della serie del trend (12/24/36)"),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    """Patrimonio netto calcolato live (no Excel) per la pagina Patrimonio.

    - totals: bank + asset - liability + financial, dove `financial` e' la quota
      dei conti investimento scorporata da `asset` per darle una linea sua.
    - breakdown: lista piatta di account con saldo al cutoff.
    - trend: serie mensile degli ultimi `months` mesi che terminano nel periodo
      richiesto. Per i mesi futuri (es. si chiede 2026 e months=12 includendo
      l'anno dopo) il saldo account e' vuoto ma gli investments usano
      l'ultimo valore noto della serie, e netWorth segue la stessa logica.
    """
    # Gli accantonamenti (conti con counts_in_net_worth = false) segnano una
    # destinazione, non denaro in piu': entrano nel dettaglio ma non nei totali.
    accounts = [a for a in session.scalars(select(Account)).all() if a.counts_in_net_worth]
    all_transactions = movimenti_per_saldi(session)

    if month == 0:
        requested_label = f"{year}"
        end_year, end_month = year, 12
    else:
        requested_label = f"{year}-{month:02d}"
        end_year, end_month = year, month
    end_cutoff = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])

    # Costruisco la lista dei mesi: esattamente `months` elementi che terminano
    # con (end_year, end_month). Esempio: end=2026-08, months=12 -> Set 2025 ... Ago 2026.
    point_year, point_month = end_year, end_month
    for _ in range(months - 1):
        if point_month == 1:
            point_year -= 1
            point_month = 12
        else:
            point_month -= 1
    month_points: list[tuple[int, int, date]] = []
    for _ in range(months):
        last_day = calendar.monthrange(point_year, point_month)[1]
        month_points.append((point_year, point_month, date(point_year, point_month, last_day)))
        if point_month == 12:
            point_year += 1
            point_month = 1
        else:
            point_month += 1

    trend: list[dict[str, Any]] = []
    data_period: date | None = None
    liquid_account_ids = {account.id for account in accounts if account.is_liquid}
    # I conti investimento sono attivita' a tutti gli effetti, ma la pagina li
    # mostra su una linea sua: e' l'unica parte del patrimonio che si muove da
    # sola, senza che tu abbia versato o speso niente. Quindi si scorpora da
    # `asset` per la vista, non si toglie dal patrimonio.
    investimenti_ids = conti_con_valore_di_mercato(session) & {a.id for a in accounts}

    def _spacchetta(balances: dict[str, Any]) -> dict[str, float]:
        investimenti = sum(item["balance"] for item in balances["accounts"]
                           if item["account_id"] in investimenti_ids)
        return {"bank": balances["totals"]["bank"],
                "asset": balances["totals"]["asset"] - investimenti,
                "liability": balances["totals"]["liability"],
                "financial": investimenti}

    def liquid_at(balances: dict[str, Any]) -> float:
        return round(sum(item["balance"] for item in balances["accounts"]
                         if item["account_id"] in liquid_account_ids and item["group"] != "liability")
                     - balances["totals"]["liability"], 2)

    last_totals: dict[str, float] = {"bank": 0.0, "asset": 0.0, "liability": 0.0, "financial": 0.0, "liquid": 0.0, "netWorth": 0.0}
    # Una passata sola per tutti i mesi del trend, invece di una per mese.
    stime = valutazioni_per_conto(session)
    guadagni = rivalutazioni_per_conto(session)
    saldi_mensili = account_balances_series(accounts, all_transactions, [punto[2] for punto in month_points], stime, guadagni)
    for (point_year, point_month, cutoff), balances in zip(month_points, saldi_mensili):
        totals_month = _spacchetta(balances)
        net_worth = totals_month["bank"] + totals_month["asset"] - totals_month["liability"] + totals_month["financial"]
        trend.append({"label": f"{MONTHS[point_month - 1]} {str(point_year)[-2:]}", "period": f"{point_year}-{point_month:02d}",
                      "bank": round(totals_month["bank"], 2), "asset": round(totals_month["asset"], 2),
                      "liability": round(-totals_month["liability"], 2), "financial": round(totals_month["financial"], 2),
                      "liquid": liquid_at(balances),
                      "netWorth": round(net_worth, 2)})
        if (point_year, point_month) == (end_year, end_month):
            last_totals = {**totals_month, "liquid": liquid_at(balances), "netWorth": round(net_worth, 2)}
            data_period = date(point_year, point_month, 1)

    # Se il mese richiesto non era nel trend (per via di months troppo corto),
    # ricalcolo i totali per il cutoff richiesto.
    if not any(row["period"] == f"{end_year}-{end_month:02d}" for row in trend):
        balances = account_balances_at(accounts, all_transactions, end_cutoff, stime, guadagni)
        totals_month = _spacchetta(balances)
        last_totals = {**totals_month, "liquid": liquid_at(balances), "netWorth": round(totals_month["bank"] + totals_month["asset"] - totals_month["liability"] + totals_month["financial"], 2)}
        data_period = end_cutoff.replace(day=1)

    # Lo stesso patrimonio letto in altre valute. Ogni mese usa il cambio del
    # mese: e' quello che rende la variazione in dollari diversa da quella in
    # euro, perche' ci si somma il movimento del cambio.
    currencies: list[dict[str, Any]] = []
    for code in display_currencies(session):
        points, inverted = fx_rates_by_month(session, code)
        if not points:
            currencies.append({"code": code, "available": False, "inverted": False, "total": None,
                               "change": None, "changePercent": None, "rate": None})
            continue
        converted = []
        for row, (_, _, cutoff) in zip(trend, month_points):
            rate = _rate_on(points, cutoff)
            # Oltre al valore convertito viaggia il cambio del mese: cosi'
            # l'interfaccia puo' rileggere nella stessa valuta anche le altre
            # linee del grafico senza rispedirle tutte moltiplicate.
            converted.append({"period": row["period"], "label": row["label"],
                              "rate": float(rate) if rate else None,
                              "netWorth": round(float(Decimal(str(row["netWorth"])) * rate), 2) if rate else None})
        latest = next((item for item in reversed(converted) if item["netWorth"] is not None), None)
        previous = None
        if latest is not None:
            index = converted.index(latest)
            previous = next((item for item in reversed(converted[:index]) if item["netWorth"] is not None), None)
        change = round(latest["netWorth"] - previous["netWorth"], 2) if latest and previous else None
        rate_now = _rate_on(points, end_cutoff)
        currencies.append({
            "code": code,
            "available": True,
            # true = il cambio si legge al contrario (euro per unita'), come le cripto
            "inverted": inverted,
            "total": latest["netWorth"] if latest else None,
            "change": change,
            "changePercent": round(change / previous["netWorth"] * 100, 2) if change is not None and previous and previous["netWorth"] else None,
            "rate": float(Decimal(1) / rate_now if inverted else rate_now) if rate_now else None,
        })

    data_period_label = data_period.strftime("%Y-%m") if data_period else None
    return {"requestedPeriod": requested_label, "dataPeriod": data_period_label,
            "totals": last_totals,
            "currencies": currencies}


# Il patrimonio si legge anche in altre valute, come il foglio Asset & Liability:
# EUR piu' le divise scelte, ognuna al cambio del mese a cui si riferisce, non a
# quello di oggi. Con il cambio corrente la storia verrebbe riscritta ogni volta.
NET_WORTH_CURRENCIES_KEY = "net_worth_currencies"
NET_WORTH_CURRENCIES_DEFAULT = "USD,CHF,BTC"


def display_currencies(session: Session) -> list[str]:
    raw = session.scalar(select(AppSetting.value).where(AppSetting.key == NET_WORTH_CURRENCIES_KEY))
    codes = [code.strip().upper() for code in (raw or NET_WORTH_CURRENCIES_DEFAULT).split(",") if code.strip()]
    return [code for code in dict.fromkeys(codes) if code != BASE_CURRENCY]


def fx_symbols(currency: str) -> list[tuple[str, bool]]:
    """I simboli da provare per una valuta, con il verso della conversione.

    Le divise si quotano come ``EURUSD=X`` (dollari per euro, si moltiplica).
    Le cripto no: esiste ``BTC-EUR`` (euro per bitcoin), quindi si divide.
    """
    code = currency.strip().upper()
    return [(f"{BASE_CURRENCY}{code}=X", False), (f"{code}-{BASE_CURRENCY}", True)]


def fx_rates_by_month(session: Session, currency: str) -> tuple[list[tuple[date, Decimal]], bool]:
    """Storico dei cambi: (data, unita' per 1 EUR) crescente, e il verso.

    Il verso serve a mostrarlo: "1 € = 1,16 $" si legge, "1 € = 0,0000147 BTC"
    no. Per le cripto il numero sensato e' quello inverso.
    """
    for symbol, inverted in fx_symbols(currency):
        rows = session.scalars(
            select(MarketPrice).where(MarketPrice.symbol == symbol).order_by(MarketPrice.observed_on)
        ).all()
        points = [(row.observed_on, Decimal(str(row.price))) for row in rows if row.price and Decimal(str(row.price)) > 0]
        if points:
            return [(day, (Decimal(1) / rate) if inverted else rate) for day, rate in points], inverted
    return [], False


def _rate_on(points: list[tuple[date, Decimal]], cutoff: date) -> Decimal | None:
    """L'ultimo cambio noto non successivo al cutoff."""
    chosen = None
    for day, rate in points:
        if day <= cutoff:
            chosen = rate
        else:
            break
    return chosen


# Quotazioni piu' vecchie di questo margine non vengono usate: meglio il prezzo
# dell'ultima operazione che un valore di mercato silenziosamente obsoleto.
QUOTE_MAX_AGE_DAYS = 8
# Etichetta neutra: la traduce l'interfaccia nella lingua scelta.
UNAVAILABLE = "__unavailable__"
BASE_CURRENCY = "EUR"
# I codici che l'interfaccia sa tradurre; qualunque altra cosa e' "unexpected".
KNOWN_SOURCE_CODES = {"rate_limited", "handshake_failed", "unreachable", "no_data",
                      "invalid_symbol", "query_too_short", "unexpected"}


def _fx_to_base(session: Session, currency: str, on_or_before: date) -> Decimal | None:
    """Quante unita' di ``currency`` valgono 1 EUR, dalla cache quotazioni.

    Yahoo espone il cambio come EURUSD=X (dollari per euro), quindi per portare
    un prezzo in valuta estera a euro si divide per questo tasso.
    """
    if not currency or currency.strip().upper() == BASE_CURRENCY:
        return Decimal(1)
    symbol = f"{BASE_CURRENCY}{currency.strip().upper()}=X"
    row = session.scalar(
        select(MarketPrice)
        .where(MarketPrice.symbol == symbol, MarketPrice.observed_on <= on_or_before)
        .order_by(MarketPrice.observed_on.desc())
        .limit(1)
    )
    if row is None or not row.price or Decimal(str(row.price)) <= 0:
        return None
    return Decimal(str(row.price))


def quoted_prices_by_instrument(session: Session) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
    """Prezzo di mercato in euro per ogni strumento con un ticker configurato.

    Ritorna anche l'elenco dei problemi (quotazione assente, troppo vecchia o
    cambio mancante) perche' l'interfaccia possa dirlo invece di mostrare in
    silenzio un valore preso dall'ultima operazione.
    """
    prices: dict[str, Decimal] = {}
    issues: list[dict[str, Any]] = []
    today = date.today()
    for instrument in session.scalars(select(InvestmentInstrument)).all():
        symbol = (instrument.provider_symbol or "").strip()
        if not symbol:
            continue
        row = session.scalar(
            select(MarketPrice)
            .where(MarketPrice.symbol == symbol.upper())
            .order_by(MarketPrice.observed_on.desc())
            .limit(1)
        )
        if row is None:
            issues.append({"instrument": instrument.name, "symbol": symbol, "reason": "no_quote"})
            continue
        age = (today - row.observed_on).days
        if age > QUOTE_MAX_AGE_DAYS:
            issues.append({"instrument": instrument.name, "symbol": symbol, "reason": "stale", "observedOn": row.observed_on.isoformat(), "ageDays": age})
            continue
        price = Decimal(str(row.price))
        quote_currency = (row.currency or instrument.currency or BASE_CURRENCY).strip().upper()
        if quote_currency != BASE_CURRENCY:
            rate = _fx_to_base(session, quote_currency, row.observed_on)
            if rate is None:
                issues.append({"instrument": instrument.name, "symbol": symbol, "reason": "no_fx", "currency": quote_currency})
                continue
            price = price / rate
        prices[instrument.name] = price
    return prices, issues


def _price_series(session: Session) -> dict[str, list[tuple[date, Decimal, str | None]]]:
    """Prezzi in cache raggruppati per simbolo, in ordine di data."""
    series: dict[str, list[tuple[date, Decimal, str | None]]] = defaultdict(list)
    for row in session.scalars(select(MarketPrice).order_by(MarketPrice.observed_on)).all():
        series[row.symbol.upper()].append((row.observed_on, Decimal(str(row.price)), row.currency))
    return series


def _price_on(series: list[tuple[date, Decimal, str | None]], moment: date) -> tuple[Decimal, str | None] | None:
    """Ultima quotazione nota non successiva alla data richiesta."""
    chosen = None
    for observed_on, price, currency in series:
        if observed_on > moment:
            break
        chosen = (price, currency)
    return chosen


def portfolio_timeline(session: Session, solo: set[int] | None = None) -> list[dict[str, Any]]:
    """Valore di mercato e capitale investito, mese per mese, dal solo ledger.

    Con `solo` si limita a un sottoinsieme di operazioni: serve a valorizzare un
    singolo conto con le sue, invece di dare a tutti una fetta del totale.

    Sostituisce lo storico che prima arrivava dal foglio Excel: le quote le
    conosce il ledger, i prezzi storici stanno in cache. Un mese senza prezzo
    per uno strumento usa l'ultima quotazione nota precedente; se non ce n'e'
    nessuna si ripiega sul prezzo dell'ultima operazione, cosi' la serie non
    presenta buchi ne' salti a zero.
    """
    rows = sorted(
        (riga for riga in session.scalars(select(InvestmentTransaction)).all()
         if solo is None or riga.id in solo),
        key=lambda item: (item.occurred_on or date.min, item.id or 0),
    )
    if not rows:
        return []
    tickers = {
        instrument.name.strip().lower(): (instrument.provider_symbol or "").strip().upper()
        for instrument in session.scalars(select(InvestmentInstrument)).all()
        if (instrument.provider_symbol or "").strip()
    }
    series = _price_series(session)

    holdings: dict[str, dict[str, Any]] = {}
    timeline: list[dict[str, Any]] = []
    today = date.today()
    cursor = date(rows[0].occurred_on.year, rows[0].occurred_on.month, 1)
    index = 0

    while cursor <= today:
        month_end = (date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)) - timedelta(days=1)
        while index < len(rows) and rows[index].occurred_on <= month_end:
            row = rows[index]
            index += 1
            units = abs(Decimal(str(row.units or 0)))
            amount = abs(Decimal(str(row.amount or 0)))
            if units == 0:
                continue
            key = (row.name or "").strip().lower()
            holding = holdings.setdefault(key, {"units": Decimal("0"), "invested": Decimal("0"), "last_price": Decimal("0")})
            if (row.transaction_type or "").strip().lower() in {"buy", "acquisto"}:
                holding["units"] += units
                holding["invested"] += amount
            else:
                # Versato netto: la vendita sottrae l'incasso per intero.
                sold = min(units, holding["units"])
                holding["invested"] -= amount
                holding["units"] -= sold
            if row.price:
                holding["last_price"] = Decimal(str(row.price))

        market_value = Decimal("0")
        # Il capitale investito e' il versato netto complessivo: comprende anche
        # gli strumenti gia' venduti, perche' l'incasso di una vendita riduce
        # quanto hai immobilizzato. Contare solo le posizioni aperte gonfierebbe
        # il totale di tutte le plusvalenze gia' realizzate.
        invested = sum((holding["invested"] for holding in holdings.values()), Decimal("0"))
        for key, holding in holdings.items():
            if holding["units"] <= 0:
                continue
            symbol = tickers.get(key)
            quote = _price_on(series.get(symbol, []), month_end) if symbol else None
            if quote is None:
                market_value += holding["units"] * holding["last_price"]
                continue
            price, currency = quote
            if currency and currency.upper() != BASE_CURRENCY:
                fx = _price_on(series.get(f"{BASE_CURRENCY}{currency.upper()}=X", []), month_end)
                if fx and fx[0] > 0:
                    price = price / fx[0]
                else:
                    market_value += holding["units"] * holding["last_price"]
                    continue
            market_value += holding["units"] * price

        timeline.append({
            "period": cursor.isoformat(),
            "marketValue": num(market_value),
            "investedCapital": num(invested),
        })
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)

    return timeline


def portfolio_state_at(timeline: list[dict[str, Any]], cutoff: date) -> dict[str, Any] | None:
    """Ultimo punto della serie non successivo alla data richiesta."""
    chosen = None
    for point in timeline:
        if date.fromisoformat(point["period"]) > cutoff:
            break
        chosen = point
    return chosen


def _open_positions(session: Session, include_closed: bool = False) -> list[dict[str, Any]]:
    """Posizioni con units > 0, con market value dal prezzo quotato in cache
    se disponibile, altrimenti dall'ultimo prezzo di transazione (fallback
    dichiarato via has_quote=False). Fee per transazione non ancora tracciate
    a livello di riga nel dettaglio investimenti: 0 di default.
    """
    rows = session.scalars(select(InvestmentTransaction)).all()
    fees_by_tx = {row.id: 0 for row in rows}
    prices, _ = quoted_prices_by_instrument(session)
    tickers = {
        row.name: row.provider_symbol
        for row in session.scalars(select(InvestmentInstrument)).all()
        if (row.provider_symbol or "").strip()
    }
    positions = investment_positions(rows, fees_by_transaction=fees_by_tx, prices_by_name=prices, tickers_by_name=tickers)
    return positions if include_closed else [p for p in positions if p["units"] > 0]


def _instrument_lookup(session: Session) -> dict[str, InvestmentInstrument]:
    return {row.name.strip().lower(): row for row in session.scalars(select(InvestmentInstrument)).all()}


def _contributions_by_month(session: Session) -> list[dict[str, Any]]:
    """Denaro nuovo entrato negli strumenti, mese per mese: acquisti meno vendite.

    Al netto, perche' un ribilanciamento vende uno strumento per comprarne un
    altro e non e' denaro nuovo: contando solo gli acquisti ogni ribilanciamento
    sembrerebbe un versamento.
    """
    monthly: dict[str, float] = defaultdict(float)
    for row in session.scalars(select(InvestmentTransaction)).all():
        if row.transaction_type not in ("Buy", "Sell") or not row.occurred_on:
            continue
        key = f"{row.occurred_on.year}-{row.occurred_on.month:02d}"
        monthly[key] += num(row.amount) if row.transaction_type == "Buy" else -num(row.amount)
    return [{"period": key, "label": f"{MONTHS[int(key[5:]) - 1]} {key[2:4]}", "amount": round(value, 2)}
            for key, value in sorted(monthly.items())]


@router.get("/api/investments/instrument-history")
def instrument_history(name: str = Query(min_length=1), session: Session = Depends(get_session)) -> dict[str, Any]:
    """Prezzo storico di uno strumento con sopra le proprie operazioni.

    I prezzi sono quelli gia' in cache: dieci anni di chiusure mensili scaricate
    per valorizzare il portafoglio, che finora non erano mai state mostrate.
    """
    instrument = session.scalar(select(InvestmentInstrument).where(func.lower(InvestmentInstrument.name) == name.strip().lower()))
    symbol = (instrument.provider_symbol or "").strip().upper() if instrument else ""
    prices = []
    if symbol:
        prices = [{"date": row.observed_on.isoformat(), "price": float(row.price), "currency": row.currency}
                  for row in session.scalars(
                      select(MarketPrice).where(MarketPrice.symbol == symbol).order_by(MarketPrice.observed_on)
                  ).all()]
    trades = [{
        "date": row.occurred_on.isoformat(),
        "type": row.transaction_type,
        "units": num(row.units),
        "amount": num(row.amount),
        "price": num(row.price) or (num(row.amount) / num(row.units) if row.units else 0),
    } for row in session.scalars(
        select(InvestmentTransaction)
        .where(func.lower(InvestmentTransaction.name) == name.strip().lower())
        .order_by(InvestmentTransaction.occurred_on)
    ).all()]
    return {"name": name, "symbol": symbol, "currency": (prices[-1]["currency"] if prices else None),
            "prices": prices, "trades": trades}


@router.get("/api/investments/dashboard")
def investments_dashboard(session: Session = Depends(get_session)) -> dict[str, Any]:
    # Valore e storico dal ledger piu' le quotazioni: nessuna dipendenza da
    # uno storico compilato a mano nel foglio Excel.
    history = portfolio_timeline(session)
    latest = history[-1] if history else None
    market, invested = (latest["marketValue"], latest["investedCapital"]) if latest else (0, 0); gain = market-invested
    positions = _open_positions(session, include_closed=True)
    instruments = _instrument_lookup(session)
    contributions = _contributions_by_month(session)
    quoted = sum(1 for p in positions if p["has_quote"] and p["units"] > 0)
    position_items = []
    for p in positions:
        instrument = instruments.get(p["name"].strip().lower())
        position_items.append({
            "name": p["name"],
            # Una posizione chiusa non e' sparita: e' un risultato acquisito.
            "isOpen": p["units"] > 0,
            "units": num(p["units"]),
            "costBasis": num(p["cost_basis"]),
            "netContributed": num(p["net_contributed"]),
            "price": num(p["price"]),
            "hasQuote": p["has_quote"],
            "marketValue": num(p["market_value"]),
            "realizedGain": num(p["realized_gain"]),
            "unrealizedGain": num(p["unrealized_gain"]),
            "totalGain": num(p["total_gain"]),
            "returnRate": float(p["return_rate"]) if p["return_rate"] is not None else None,
            "currency": p["currency"],
            "instrumentId": instrument.id if instrument else None,
            "providerSymbol": instrument.provider_symbol if instrument else None,
            "assetClass": instrument.asset_class if instrument else "Da classificare",
            "area": instrument.area if instrument else "Globale",
            "sector": instrument.sector if instrument else "Altro",
            "targetWeight": float(instrument.target_weight) if instrument and instrument.target_weight is not None else None,
        })
    return {"snapshot": {"period": latest["period"] if latest else None, "marketValue": market, "investedCapital": invested, "gain": gain, "returnRate": round(gain/invested*100, 2) if invested else 0}, "ledger": {"marketValue": market, "costBasis": invested, "gain": gain, "quotedPositions": quoted, "activePositions": len(positions)}, "positions": position_items, "history": [{"period": row["period"], "label": f"{MONTHS[date.fromisoformat(row['period']).month-1]} {date.fromisoformat(row['period']).year}", "marketValue": row["marketValue"], "investedCapital": row["investedCapital"], "gain": round(row["marketValue"] - row["investedCapital"], 2),
        # Rendimento in percentuale: distingue "sta rendendo" da "ho versato di piu'".
        "returnRate": round((row["marketValue"] / row["investedCapital"] - 1) * 100, 2) if row["investedCapital"] else None}
        for row in history], "contributions": contributions}


@router.get("/api/investments/ledger")
def investments_ledger(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.scalars(select(InvestmentTransaction).order_by(InvestmentTransaction.occurred_on.desc(), InvestmentTransaction.id.desc())).all()
    dettagli = {row.transaction_id: row for row in session.scalars(select(InvestmentTransactionDetail)).all()}
    # Calcolo runningUnits: somma cumulativa delle units per strumento, in ordine cronologico ascendente.
    # Buy aggiunge, Sell sottrae. Il valore esposto è il saldo di quello strumento fino a quella riga (inclusa).
    ascending = sorted(rows, key=lambda row: (row.occurred_on, row.id))
    # Quali operazioni risultano gia' agganciate a un movimento dei conti. Non
    # e' un dettaglio estetico: e' il collegamento che dice a quale conto
    # attribuire il guadagno di questa operazione, e finche' manca il conto
    # mostra il costo senza la rivalutazione. Vederlo riga per riga e' l'unico
    # modo di sapere cosa resta da fare.
    collegate = {riga[0] for riga in session.execute(
        select(distinct(TransactionLedgerLink.ledger_id))).all()}
    running_by_name: dict[str, Decimal] = {}
    running_at_row: dict[int, Decimal] = {}
    for row in ascending:
        units = Decimal(str(num(row.units)))
        # Normalizza al valore assoluto: le unità per una Vendita sono salvate come
        # negative nel DB. Per la posizione cumulativa, vogliamo sempre sottrarre
        # in caso di Sell, sommare in caso di Buy, indipendentemente dal segno.
        abs_units = units.copy_abs()
        delta = abs_units if row.transaction_type == "Buy" else -abs_units
        running_by_name[row.name] = running_by_name.get(row.name, Decimal("0")) + delta
        running_at_row[row.id] = running_by_name[row.name]
    return {"items": [{"id": row.id, "occurredOn": row.occurred_on.isoformat(), "name": row.name, "transactionType": row.transaction_type, "amount": num(row.amount), "signedAmount": num(row.amount) * (-1 if row.transaction_type == "Buy" else 1), "units": num(row.units), "price": num(row.price), "currency": row.currency or "EUR", "fee": num(detail.fee) if (detail := dettagli.get(row.id)) else 0, "notes": detail.notes if detail else None, "runningUnits": float(running_at_row.get(row.id, Decimal("0"))), "linked": row.id in collegate} for row in rows]}


@router.get("/api/investments/allocation")
def investments_allocation(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Allocazione ricavata dalla fonte dati, non da classificazioni manuali.

    Ogni posizione aperta viene distribuita secondo la composizione dello
    strumento (settori, azioni/obbligazioni/liquidita', titoli sottostanti)
    scaricata da Yahoo e messa in cache. Cio' che non ha ticker o profilo
    finisce in una quota dichiarata "non disponibile": meglio dirlo che
    spalmare valori inventati sulle percentuali.
    """
    positions = _open_positions(session)
    tickers = {
        row.name.strip().lower(): (row.provider_symbol or "").strip().upper()
        for row in session.scalars(select(InvestmentInstrument)).all()
        if (row.provider_symbol or "").strip()
    }
    profiles: dict[str, dict[str, Any]] = {}
    fetched_dates: list[datetime] = []
    errors: dict[str, str] = {}
    for row in session.scalars(select(InstrumentProfileModel)).all():
        if row.payload:
            try:
                profiles[row.symbol] = json.loads(row.payload)
            except json.JSONDecodeError:
                pass
            if row.fetched_at:
                fetched_dates.append(row.fetched_at)
        if row.last_error:
            # Nel campo puo' esserci il residuo di una versione vecchia, che ci
            # scriveva la frase d'errore invece del codice. L'interfaccia
            # traduce i codici, quindi tutto cio' che non riconosce va ricondotto
            # a "imprevisto" invece di uscire come testo grezzo.
            errors[row.symbol] = row.last_error if row.last_error in KNOWN_SOURCE_CODES else "unexpected"

    total = sum(p["market_value"] for p in positions)
    sectors: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    asset_types: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    holdings: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    currencies: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    covered = Decimal("0")
    missing: list[dict[str, Any]] = []

    asset_type_labels = {"stock": "Stocks", "bond": "Bonds", "cash": "Cash", "other": "Other"}

    for position in positions:
        value = position["market_value"]
        symbol = tickers.get(position["name"].strip().lower())
        currencies[position["currency"] or "EUR"] += value
        profile = profiles.get(symbol) if symbol else None
        if not profile:
            missing.append({
                "instrument": position["name"],
                "value": num(value),
                "reason": "no_ticker" if not symbol else ("error" if symbol in errors else "no_profile"),
                "code": errors.get(symbol or "", None),
            })
            sectors[UNAVAILABLE] += value
            asset_types[UNAVAILABLE] += value
            continue

        covered += value
        profile_sectors = profile.get("sectors") or {}
        weight_sum = sum(profile_sectors.values())
        if weight_sum > 0:
            for label, weight in profile_sectors.items():
                sectors[label] += value * Decimal(str(weight / weight_sum))
        else:
            sectors[UNAVAILABLE] += value

        mix = profile.get("assetMix") or {}
        mix_sum = sum(v for v in mix.values() if v)
        if mix_sum > 0:
            for key, weight in mix.items():
                if weight:
                    asset_types[asset_type_labels.get(key, key)] += value * Decimal(str(weight / mix_sum))
        else:
            # Titolo singolo: e' interamente della propria natura.
            asset_types["Stocks" if profile.get("sectors") else UNAVAILABLE] += value

        for item in profile.get("holdings") or []:
            weight = item.get("weight")
            name = item.get("name") or item.get("symbol")
            if weight and name:
                holdings[name] += value * Decimal(str(weight))

    def to_items(buckets: dict[str, Decimal], limit: int | None = None) -> list[dict[str, Any]]:
        rows = sorted(buckets.items(), key=lambda item: item[1], reverse=True)
        if limit:
            rows = rows[:limit]
        return [{
            "label": label,
            "value": num(value),
            "weight": round(float(value / total) * 100, 1) if total else 0,
        } for label, value in rows]

    return {
        "total": num(total),
        "allocations": {
            # La ripartizione piu' elementare, e l'unica che non dipende da una
            # fonte esterna: quanto pesa ciascuno strumento che possiedi.
            "instrument": to_items({p["name"]: p["market_value"] for p in positions}),
            "sector": to_items(sectors),
            "assetType": to_items(asset_types),
            "holdings": to_items(holdings, limit=15),
            "currency": to_items(currencies),
        },
        "coverage": {
            "covered": num(covered),
            "uncovered": num(total - covered),
            "coveredPercent": round(float(covered / total) * 100, 1) if total else 0,
            "missing": sorted(missing, key=lambda item: -item["value"]),
            "lastFetch": max(fetched_dates).isoformat() if fetched_dates else None,
            # Solo i simboli in uso: la cache puo' conservare il record di un
            # ticker cambiato tempo fa, e segnalarlo come problema attuale
            # manderebbe a cercare un guasto che non c'e'.
            "sourceErrors": [{"symbol": k, "code": v} for k, v in sorted(errors.items())
                             if k in set(tickers.values())],
        },
    }


@router.get("/api/notes")
def notes(session: Session = Depends(get_session)) -> dict[str, Any]: return {"items": [{"id": row.id, "section": row.section, "title": row.title, "body": row.body or "", "status": row.status, "updatedAt": row.updated_at.isoformat() if row.updated_at else None} for row in session.scalars(select(Note).order_by(Note.section, Note.title)).all()]}






# ---------------------------------------------------------------------------
# Regole di categorizzazione
# ---------------------------------------------------------------------------
# Le regole riempiono la categoria nell'anteprima dell'import, dove si vedono e
# si possono cambiare riga per riga. Non toccano mai i movimenti gia'
# registrati: riscrivere una categoria storica cambierebbe budget e report gia'
# chiusi, ed e' una decisione che si prende a parte.


class RulePayload(BaseModel):
    pattern: str
    is_regex: bool = False
    category: str
    # I tipi che hanno una categoria. Uno spostamento fra conti non ce l'ha, e
    # gli altri tipi li rifiuta gia' il modello prima di arrivare qui.
    transaction_type: Literal["Expenses", "Income"] | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    active: bool = True


class RuleOrderPayload(BaseModel):
    ids: list[int]


def _regola_json(riga: CategorizationRule) -> dict[str, Any]:
    return {"id": riga.id, "position": riga.position, "pattern": riga.pattern, "isRegex": riga.is_regex,
            "category": riga.category, "transactionType": riga.transaction_type,
            "minAmount": num(riga.min_amount) if riga.min_amount is not None else None,
            "maxAmount": num(riga.max_amount) if riga.max_amount is not None else None,
            "active": riga.active}


def _importo(valore: float | None) -> Decimal | None:
    return None if valore is None else Decimal(str(valore)).quantize(Decimal("0.01"))


def _valida_regola(payload: RulePayload, session: Session, *, esistente: CategorizationRule | None = None) -> None:
    """Rifiuta una regola che l'anteprima non saprebbe applicare.

    Sono i controlli di un confine: una regex che non si compila fermerebbe
    l'import di un estratto conto intero, e un pattern vuoto combacerebbe con
    ogni riga, cioe' categorizzerebbe tutto allo stesso modo senza che nessuno
    se ne accorga.
    """
    pattern = payload.pattern.strip()
    if not pattern or len(pattern) > 255:
        raise HTTPException(status_code=422, detail="rulePatternRequired")
    if payload.is_regex:
        try:
            re.compile(pattern)
        except re.error as error:
            raise HTTPException(status_code=422, detail="ruleRegexInvalid") from error
    if payload.category.strip() not in categorie_ammesse(session):
        raise HTTPException(status_code=422, detail="ruleCategoryUnknown")
    minimo, massimo = _importo(payload.min_amount), _importo(payload.max_amount)
    if minimo is not None and massimo is not None and minimo > massimo:
        raise HTTPException(status_code=422, detail="ruleAmountRange")
    if esistente is None:
        quante = session.scalar(select(func.count(CategorizationRule.id))) or 0
        if quante >= MAX_REGOLE:
            raise HTTPException(status_code=422, detail="ruleLimitReached")


@router.get("/api/categorization-rules")
def categorization_rules(session: Session = Depends(get_session)) -> dict[str, Any]:
    righe = session.scalars(select(CategorizationRule)
                            .order_by(CategorizationRule.position, CategorizationRule.id)).all()
    return {"items": [_regola_json(riga) for riga in righe]}


@router.post("/api/categorization-rules", status_code=201)
def create_categorization_rule(payload: RulePayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Crea una regola in coda alle altre: l'ordine e' la priorita'."""
    _valida_regola(payload, session)
    ultima = session.scalar(select(func.max(CategorizationRule.position))) or 0
    riga = CategorizationRule(position=ultima + 1, pattern=payload.pattern.strip(), is_regex=payload.is_regex,
                              category=payload.category.strip(), transaction_type=payload.transaction_type,
                              min_amount=_importo(payload.min_amount), max_amount=_importo(payload.max_amount),
                              active=payload.active)
    session.add(riga)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="ruleDuplicate") from error
    return _regola_json(riga)


# Prima di "/{rule_id}": il percorso con l'identificativo e' un intero, e una
# rotta registrata per prima se la prenderebbe lui.
@router.put("/api/categorization-rules/order")
def reorder_categorization_rules(payload: RuleOrderPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Riscrive la posizione delle regole nell'ordine ricevuto."""
    righe = {riga.id: riga for riga in session.scalars(select(CategorizationRule)).all()}
    for posizione, rule_id in enumerate(payload.ids):
        if rule_id in righe:
            righe[rule_id].position = posizione
    session.commit()
    return categorization_rules(session)


@router.put("/api/categorization-rules/{rule_id}")
def update_categorization_rule(rule_id: int, payload: RulePayload,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = session.get(CategorizationRule, rule_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="ruleNotFound")
    _valida_regola(payload, session, esistente=riga)
    riga.pattern, riga.is_regex = payload.pattern.strip(), payload.is_regex
    riga.category, riga.transaction_type = payload.category.strip(), payload.transaction_type
    riga.min_amount, riga.max_amount = _importo(payload.min_amount), _importo(payload.max_amount)
    riga.active = payload.active
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="ruleDuplicate") from error
    return _regola_json(riga)


@router.delete("/api/categorization-rules/{rule_id}")
def delete_categorization_rule(rule_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = session.get(CategorizationRule, rule_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="ruleNotFound")
    session.delete(riga)
    session.commit()
    return {"success": True}


def register_core_routes(app: FastAPI) -> None:
    app.include_router(router)


def monthly_points(end_year: int, end_month: int, months: int) -> list[tuple[int, int, date]]:
    """I ``months`` mesi che terminano con quello indicato, con la data di fine."""
    anno, mese = end_year, end_month
    for _ in range(months - 1):
        anno, mese = (anno - 1, 12) if mese == 1 else (anno, mese - 1)
    punti: list[tuple[int, int, date]] = []
    for _ in range(months):
        punti.append((anno, mese, date(anno, mese, calendar.monthrange(anno, mese)[1])))
        anno, mese = (anno + 1, 1) if mese == 12 else (anno, mese + 1)
    return punti


def net_worth_series(session: Session, end_year: int, end_month: int, months: int) -> list[dict[str, Any]]:
    """Patrimonio, entrate, uscite e risparmio mese per mese.

    Usa gli stessi ingredienti della pagina Patrimonio - saldi dei conti al
    cutoff piu' valore di mercato del portafoglio - perche' due strade diverse
    per lo stesso numero prima o poi divergono, e ce ne accorgeremmo tardi.
    """
    conti = [a for a in session.scalars(select(Account)).all() if a.counts_in_net_worth]
    movimenti = movimenti_per_saldi(session)
    stime = valutazioni_per_conto(session)
    guadagni = rivalutazioni_per_conto(session)

    serie = []
    for anno, mese, cutoff in monthly_points(end_year, end_month, months):
        # Il mercato e' gia' dentro i saldi, sul conto investimenti: sommarlo
        # qui lo conterebbe due volte.
        saldi = account_balances_at(conti, movimenti, cutoff, stime, guadagni)["totals"]
        entrate = period_total(session, anno, mese, "Income")
        uscite = period_total(session, anno, mese, "Expenses")
        serie.append({
            "period": f"{anno}-{mese:02d}",
            "label": f"{MONTHS[mese - 1]} {str(anno)[-2:]}",
            "netWorth": round(saldi["bank"] + saldi["asset"] - saldi["liability"], 2),
            "income": entrate,
            "expenses": uscite,
            "savings": round(entrate - uscite, 2),
        })
    return serie


# --------------------------------------------------------------------------
# Il bilancio nel tempo, a profondita' variabile.
#
# Un endpoint solo per quello che prima erano tre grafici che non si parlavano:
# il trend del patrimonio (sei linee sovrapposte), lo storico per gruppo nella
# pagina Conti (un anno solare, un gruppo alla volta) e il dettaglio conti.
# Avevano periodi diversi, quindi non si potevano nemmeno sovrapporre a occhio.
#
# Qui il periodo e' uno e la risposta ha sempre la stessa forma, a qualunque
# livello: cosi' il componente che la disegna non cambia quando si scende.

COMPONENTI_ATTIVO = ("liquidity", "investments", "other")


def _componente_di(conto: Account, con_mercato: set[int]) -> str:
    """In quale voce del bilancio finisce un conto.

    "Investimenti" non e' un gruppo in cui si mette un conto: e' quello che
    succede a un conto quando i suoi movimenti risultano collegati al ledger.
    """
    if conto.source_group == "liability":
        return "liabilities"
    if conto.id in con_mercato:
        return "investments"
    return "liquidity" if conto.source_group == "bank" else "other"


def _tagli_periodo(da: date, a: date, grain: str) -> list[tuple[str, str, date]]:
    """Le date di fine periodo fra `da` e `a`, comprese.

    Trimestrale non e' un vezzo: sessanta punti mensili su una card larga
    seicento pixel sono rumore, e chi guarda cinque anni non cerca il singolo
    mese.
    """
    punti: list[tuple[str, str, date]] = []
    anno, mese = da.year, da.month
    passo = 3 if grain == "quarter" else 1
    if grain == "quarter":
        mese = mese - (mese - 1) % 3
    while (anno, mese) <= (a.year, a.month):
        fine_anno, fine_mese = anno, mese + passo - 1
        if fine_mese > 12:
            fine_anno, fine_mese = fine_anno + 1, fine_mese - 12
        ultimo = date(fine_anno, fine_mese, calendar.monthrange(fine_anno, fine_mese)[1])
        etichetta = (f"T{(fine_mese - 1) // 3 + 1} {str(fine_anno)[-2:]}" if grain == "quarter"
                     else f"{MONTHS[fine_mese - 1]} {str(fine_anno)[-2:]}")
        punti.append((f"{fine_anno}-{fine_mese:02d}", etichetta, ultimo))
        mese += passo
        if mese > 12:
            anno, mese = anno + 1, mese - 12
    return punti


def _senza_serie_piatte(serie: list[dict[str, Any]], chiavi: list[dict[str, Any]]
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Toglie le linee ferme a zero e ordina le altre per quanto pesano.

    Una linea piatta sull'asse non dice niente e ruba un colore a chi ha
    qualcosa da dire. Quante ne sono state tolte va detto, altrimenti sembra
    che manchino dei dati.
    """
    picco = {chiave["key"]: max((abs(punto["values"].get(chiave["key"], 0.0)) for punto in serie), default=0.0)
             for chiave in chiavi}
    vive = sorted((c for c in chiavi if picco[c["key"]] >= 0.005), key=lambda c: -picco[c["key"]])
    ammesse = {c["key"] for c in vive}
    serie = [{**punto, "values": {k: v for k, v in punto["values"].items() if k in ammesse}}
             for punto in serie]
    return serie, vive, len(chiavi) - len(vive)


@router.get("/api/balance-sheet/series")
def balance_sheet_series(
    da: str = Query(alias="from", description="Primo mese, YYYY-MM"),
    a: str = Query(alias="to", description="Ultimo mese, YYYY-MM"),
    grain: str = Query("month", pattern="^(month|quarter)$"),
    level: str = Query("networth", pattern="^(networth|side|component|instruments)$"),
    side: str | None = Query(None, pattern="^(assets|liabilities)$"),
    component: str | None = Query(None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Il bilancio mese per mese, alla profondita' richiesta.

    Invariante che tiene in piedi tutto: a ogni livello la somma delle voci fa
    il totale del livello sopra. Se si rompe, scendere di un livello fa
    comparire o sparire soldi, ed e' il modo peggiore di sbagliare perche'
    sembra una scoperta invece di un errore.
    """
    try:
        inizio = date(int(da[:4]), int(da[5:7]), 1)
        fine = date(int(a[:4]), int(a[5:7]), 1)
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="Periodo non valido: usare YYYY-MM")
    if fine < inizio:
        raise HTTPException(status_code=422, detail="Il periodo finisce prima di cominciare")
    if level in {"side", "component"} and side not in {"assets", "liabilities"}:
        raise HTTPException(status_code=422, detail="Serve side=assets o side=liabilities")
    if level == "component" and not component:
        raise HTTPException(status_code=422, detail="Serve component")

    punti = _tagli_periodo(inizio, fine, grain)
    linea = portfolio_timeline(session)
    if level == "instruments":
        return _serie_strumenti(punti, linea)

    # Gli accantonamenti segnano dove sono destinati soldi che stanno gia'
    # altrove. Vanno esclusi a OGNI livello: escluderli solo in cima e
    # includerli scendendo farebbe comparire soldi appena si clicca.
    conti = [c for c in session.scalars(select(Account)).all() if c.counts_in_net_worth]
    con_mercato = conti_con_valore_di_mercato(session)
    saldi = account_balances_series(conti, movimenti_per_saldi(session),
                                    [punto[2] for punto in punti],
                                    valutazioni_per_conto(session), rivalutazioni_per_conto(session))
    per_conto = {conto.id: conto for conto in conti}

    if level == "networth":
        chiavi = [{"key": "assets", "label": "assets", "drillTo": "side"},
                  {"key": "liabilities", "label": "liabilities", "drillTo": "side"}]
        serie = []
        for (periodo, etichetta, _), saldo in zip(punti, saldi):
            attivo = sum(r["balance"] for r in saldo["accounts"]
                         if per_conto[r["account_id"]].source_group != "liability")
            # I debiti si disegnano sotto lo zero: arrivano positivi dal motore
            # dei saldi, qui prendono il segno che serve a vederli.
            passivo = saldo["totals"]["liability"]
            serie.append({"period": periodo, "label": etichetta,
                          "values": {"assets": round(attivo, 2), "liabilities": round(-passivo, 2)},
                          "total": round(attivo - passivo, 2)})
        return {"level": level, "breadcrumb": [], "series": serie, "keys": chiavi, "hidden": 0}

    if level == "side":
        if side == "assets":
            chiavi = [{"key": k, "label": k,
                       "drillTo": "instruments" if k == "investments" else "component"}
                      for k in COMPONENTI_ATTIVO]
            selezione = [c for c in conti if c.source_group != "liability"]
            gruppo_di = lambda conto: _componente_di(conto, con_mercato)  # noqa: E731
        else:
            selezione = [c for c in conti if c.source_group == "liability"]
            chiavi = [{"key": f"account_{c.id}", "label": c.name, "drillTo": None} for c in selezione]
            gruppo_di = lambda conto: f"account_{conto.id}"  # noqa: E731
        return _aggrega(punti, saldi, selezione, gruppo_di, chiavi, side, negativo=(side == "liabilities"))

    selezione = [c for c in conti if c.source_group != "liability"
                 and _componente_di(c, con_mercato) == component]
    if not selezione:
        raise HTTPException(status_code=404, detail=f"Nessun conto nella voce '{component}'")
    chiavi = [{"key": f"account_{c.id}", "label": c.name, "drillTo": None} for c in selezione]
    return _aggrega(punti, saldi, selezione, lambda conto: f"account_{conto.id}", chiavi,
                    side, componente=component)


def _aggrega(punti: list[tuple[str, str, date]], saldi: list[dict[str, Any]],
             selezione: list[Account], gruppo_di: Any, chiavi: list[dict[str, Any]],
             side: str | None, negativo: bool = False,
             componente: str | None = None) -> dict[str, Any]:
    """Somma i saldi per voce, un punto per periodo."""
    ammessi = {conto.id: gruppo_di(conto) for conto in selezione}
    serie = []
    for (periodo, etichetta, _), saldo in zip(punti, saldi):
        valori: dict[str, float] = {chiave["key"]: 0.0 for chiave in chiavi}
        for riga in saldo["accounts"]:
            voce = ammessi.get(riga["account_id"])
            if voce is not None:
                valori[voce] += -riga["balance"] if negativo else riga["balance"]
        serie.append({"period": periodo, "label": etichetta,
                      "values": {k: round(v, 2) for k, v in valori.items()},
                      "total": round(sum(valori.values()), 2)})
    serie, vive, nascoste = _senza_serie_piatte(serie, chiavi)
    briciola = [{"level": "networth", "side": None, "label": "networth"}]
    if componente is not None:
        briciola.append({"level": "side", "side": side, "label": side})
    return {"level": "component" if componente else "side", "breadcrumb": briciola,
            "side": side, "component": componente, "series": serie, "keys": vive, "hidden": nascoste}


def _serie_strumenti(punti: list[tuple[str, str, date]], linea: list[dict[str, Any]]) -> dict[str, Any]:
    """Costo versato e valore di mercato del portafoglio, mese per mese.

    E' l'unico livello che non guarda i conti: la distanza fra le due curve e'
    la rivalutazione non realizzata, cioe' la parte di patrimonio cresciuta da
    sola. Non c'e' nessun altro posto nell'app dove si veda.
    """
    serie = []
    for periodo, etichetta, taglio in punti:
        stato = portfolio_state_at(linea, taglio)
        costo = stato["investedCapital"] if stato else 0.0
        mercato = stato["marketValue"] if stato else 0.0
        serie.append({"period": periodo, "label": etichetta,
                      "values": {"cost": round(costo, 2), "market": round(mercato, 2),
                                 "gain": round(mercato - costo, 2)},
                      "total": round(mercato, 2)})
    return {"level": "instruments",
            "breadcrumb": [{"level": "networth", "side": None, "label": "networth"},
                           {"level": "side", "side": "assets", "label": "assets"}],
            "side": "assets", "component": "investments", "series": serie,
            "keys": [{"key": "cost", "label": "cost", "drillTo": None},
                     {"key": "market", "label": "market", "drillTo": None}],
            "hidden": 0}
