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
from .categorization import MAX_REGOLE, categoria_da_nome, suggest
from .categorie import (GRUPPI, con_i_figli, gruppo_di_categoria, nome_di, nomi as nomi_categorie, padri,
                        radici_con_figli)
from .database import get_session
from .models import (Account, AppSetting, BudgetPlan, CategorizationRule, Category, Event,
                     Goal, GoalMilestone, InvestmentInstrument, InvestmentTransaction,
                     InvestmentTransactionDetail,
                     InstrumentProfile as InstrumentProfileModel,
                     AccountValuation, LiabilityTransactionDetail,
                     LookupOption, MarketPrice, Note, Transaction, TransactionEvent,
                     TransactionLedgerLink)
from .rendimenti import Flusso, Rendimento, Valutazione, catena, da_cento, twr, xirr
from .ribilanciamento import PosizionePeso, riequilibrio

router = APIRouter()
# I template delle ricorrenze vivono nella stessa tabella dei movimenti ma non
# sono denaro realmente entrato o uscito: vanno esclusi da ogni aggregato.
from .transaction_rules import REAL_MOVEMENT, BUDGET_MOVEMENT, INCOMPLETE_MOVEMENT, blank, missing_fields
MONTHS = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
MONTHS_LONG = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def num(value: Decimal | int | float | None) -> float:
    return round(float(value or 0), 2)


_EVENTO_NON_CHIESTO = object()


def transaction_json(row: Transaction, session: Session | None = None, *,
                     linked: list[dict[str, Any]] | None = None,
                     categoria: str | None = None,
                     liability: dict[str, Any] | None = None,
                     evento: dict[str, Any] | None | object = _EVENTO_NON_CHIESTO) -> dict[str, Any]:
    """Un movimento come lo vuole l'interfaccia.

    ``linked`` serve a chi ne serializza tanti: le operazioni di portafoglio
    collegate si caricano una volta sola per l'intero elenco e si passano qui
    gia' pronte. Senza, ogni riga si andrebbe a cercare le proprie, che con
    quattromila movimenti sono quattromila interrogazioni. Come ``evento``, che
    per lo stesso motivo accetta ``None``: "non appartiene a nessun evento" e'
    una risposta, "non me l'hai chiesto" e' un'altra.

    ``categoria`` e' il nome che corrisponde all'id del movimento: chi
    serializza un elenco intero se lo porta dietro dalla mappa caricata una
    volta, gli altri lasciano che se lo faccia dire dal database.
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
    # Spostare denaro fra conti non ha una categoria, e adesso si vede: il posto
    # vuoto e' NULL, non piu' il trattino basso del foglio di calcolo che
    # bisognava togliere in lettura.
    nome = categoria if categoria is not None else nome_di(session, row.category_id)
    return {
        "id": str(row.id), "description": row.details or nome,
        "category": nome, "categoryId": row.category_id,
        "incomplete": bool(missing_fields(row)), "missingFields": missing_fields(row),
        "countsInBudget": row.counts_in_budget, "refundOfId": row.refund_of_id,
        "incompleteAccepted": row.incomplete_accepted,
        "occurredOn": row.occurred_on.isoformat(), "effectiveOn": row.effective_on.isoformat(),
        "amount": signed,
        "transactionType": row.transaction_type, "accountName": row.account_name,
        "destinationName": row.destination_name, "goal": row.goal, "details": row.details,
        "linkedLedger": linked if linked is not None
                        else (_linked_ledger_for_transactions(session, [row.id]).get(row.id, []) if session is not None else []),
        "event": (evento if evento is not _EVENTO_NON_CHIESTO
                  else (_eventi_per_movimenti(session, [row.id]).get(row.id) if session is not None else None)),
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


def savings_category_id(session: Session) -> int | None:
    """L'id di quella categoria, se esiste gia'.

    Non la crea: questa la chiamano anche le letture, e un GET non deve
    scrivere. A crearla e' chi scrive il piano del risparmio, che passa da
    `categoria_da_nome`.
    """
    nome = savings_category(session)
    return session.scalar(select(Category.id).where(Category.parent_id.is_(None),
                                                   func.lower(Category.name) == nome.casefold()))


def categoria_per_nome(nome: str):
    """Gli id delle categorie che portano quel nome, sotto qualunque padre.

    L'interfaccia filtra ancora per nome, e due figli sotto padri diversi
    possono portarlo uguale - "Altro" sotto Alimentari e sotto Trasporti sono
    due cose diverse. Prenderli tutti e' l'unica risposta onesta finche' la
    richiesta non dice l'id: sceglierne uno a caso mostrerebbe meta' dei
    movimenti cercati.
    """
    return select(Category.id).where(func.lower(Category.name) == nome.strip().lower())


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




def _period_category_breakdown(session: Session, year: int, month: int | None, budget_type: str,
                              actual_rows: dict[int | None, float] | None = None) -> list[dict[str, Any]]:
    """Categorie pianificate/tracciate per un budget_type (Expenses/Income/Savings)
    nel periodo dato (mese o anno intero).

    Si ragiona sugli id e i nomi si scrivono alla fine: il nome e' un'etichetta,
    e due categorie possono portare lo stesso nome sotto due radici diverse.
    Sommare per nome le avrebbe unite in una riga sola.

    `actual_rows` permette di passare gli aggregati gia' calcolati da
    `budget_actual_year` (o `derived_savings`): in year mode il chiamante ha
    spesso gia' i yearly aggregates per costruire il `monthly`, e ri-fare la
    groupBy per categoria sarebbe una query in piu' per tipo.
    """
    if month is not None:
        plans = session.scalars(select(BudgetPlan).where(BudgetPlan.period == date(year, month, 1), BudgetPlan.budget_type == budget_type)).all()
        planned_by_category: dict[int | None, float] = {plan.category_id: num(plan.amount) for plan in plans}
    else:
        plans = session.scalars(select(BudgetPlan).where(extract("year", BudgetPlan.period) == year, BudgetPlan.budget_type == budget_type)).all()
        planned_by_category = defaultdict(float)
        for plan in plans:
            planned_by_category[plan.category_id] += num(plan.amount)
    if actual_rows is None:
        if budget_type == "Savings":
            actual_rows = {savings_category_id(session): derived_savings(session, year, month)}
        elif month is not None:
            actual_rows = budget_actual(session, year, month, budget_type)
        else:
            per_month = budget_actual_year(session, year, budget_type)
            actual_rows = {category: round(sum(rows.get(category, 0) for rows in per_month.values()), 2)
                           for category in {name for rows in per_month.values() for name in rows}}
    actual_by_category = {category: num(amount) for category, amount in actual_rows.items()}
    nomi_cat = nomi_categorie(session)
    gruppi = gruppo_di_categoria(session)
    # Ogni riga porta due numeri: il suo e quello del sottoalbero. Quello che si
    # legge accanto al nome e' il secondo - "Alimentari" dice anche quanto hanno
    # speso i suoi figli - mentre i valori propri restano su `amount` e `budget`,
    # ed e' su quelli che si somma un totale: il totale di un padre contiene gia'
    # i figli, e sommarli tutti conterebbe due volte la stessa spesa.
    con_figli_pianificato = con_i_figli(session, planned_by_category)
    con_figli_effettivo = con_i_figli(session, actual_by_category)
    genitori = padri(session)

    def _riga(category: int | None, budget: float) -> dict[str, Any]:
        return {"name": nomi_cat.get(category, "") if category is not None else savings_category(session),
                "categoryId": category, "parentId": genitori.get(category),
                "amount": actual_by_category.get(category, 0), "budget": budget,
                "amountWithChildren": con_figli_effettivo.get(category, 0),
                "budgetWithChildren": con_figli_pianificato.get(category, 0),
                "categoryGroup": gruppi.get(category) if category is not None else None}

    # La spesa in una categoria senza piano e' spesa lo stesso. Partire solo dal
    # piano la faceva sparire: un anno senza budget (il 2023) mostrava una
    # ripartizione vuota con 9.799 EUR spesi, e le categorie aggiunte dopo aver
    # scritto il budget non comparivano fra le piu' pesanti. Un padre entra qui
    # anche quando non ha un piano suo: il totale dei figli e' un numero che si
    # vuole leggere, ed e' la riga sotto cui quegli stessi figli si ordinano.
    per_categoria = dict(planned_by_category)
    for category, importo in con_figli_effettivo.items():
        if category not in per_categoria and importo:
            per_categoria[category] = 0
    categories = [_riga(category, budget) for category, budget in per_categoria.items()]
    categories.sort(key=lambda item: (item["budgetWithChildren"], item["amountWithChildren"]), reverse=True)
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
    (Income/Expenses/Savings), come le colonne X:AD di Budget Dashboard.

    La riga porta il padre e i figli insieme al resto, cosi' la tabella puo'
    mostrare un padre con i figli sotto invece di un elenco piatto in cui due
    righe sembrano due spese diverse. I due totali della sezione restano quelli
    di prima: si sommano i valori propri, non quelli del sottoalbero.
    """
    rows = []
    for item in categories:
        tracked, budget = item["amount"], item["budget"]
        completion = round(tracked / budget, 4) if budget else None
        remaining = round(budget - tracked, 2) if budget - tracked > 0 else 0
        excess = round(tracked - budget, 2) if budget - tracked < 0 else 0
        rows.append({"name": item["name"], "categoryId": item["categoryId"], "parentId": item["parentId"],
                     "tracked": tracked, "budget": budget, "completion": completion, "remaining": remaining,
                     "excess": excess, "trackedWithChildren": item["amountWithChildren"],
                     "budgetWithChildren": item["budgetWithChildren"]})
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
    speso_anno: dict[str, dict[int, dict[int, float]]] = {}
    if month is None:
        speso_anno["Income"] = budget_actual_year(session, year, "Income")
        speso_anno["Expenses"] = budget_actual_year(session, year, "Expenses")

    def _actual_per_cat(budget_type: str) -> dict[int | None, float]:
        if budget_type == "Savings":
            entrate = sum(sum(valori.values()) for valori in speso_anno["Income"].values())
            spese = sum(sum(valori.values()) for valori in speso_anno["Expenses"].values())
            return {savings_category_id(session): round(entrate - spese, 2)}
        aggregato: dict[int, float] = defaultdict(float)
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


def _invested_by_month(session: Session, inizio: date, fine: date,
                       mesi: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """Denaro nuovo entrato negli strumenti, mese per mese della finestra.

    Al netto, perche' un ribilanciamento vende uno strumento per comprarne un
    altro e non e' denaro nuovo: contando solo gli acquisti ogni ribilanciamento
    sembrerebbe un versamento. I mesi sono quelli della finestra - nel grafico
    stanno accanto al risparmio, che scorre con lei, e due assi che partono da
    mesi diversi raccontano due periodi diversi nella stessa figura.
    """
    versato: dict[tuple[int, int], float] = defaultdict(float)
    rows = session.scalars(select(InvestmentTransaction).where(
        InvestmentTransaction.occurred_on >= inizio, InvestmentTransaction.occurred_on <= fine)).all()
    for row in rows:
        # Solo acquisti e vendite. Un dividendo e' reddito del portafoglio, non
        # denaro versato: aggiungerlo qui direbbe che questo mese si e'
        # investito piu' di quanto si e' investito davvero.
        if row.transaction_type not in ("Buy", "Sell"):
            continue
        amount = num(row.amount)
        versato[(row.occurred_on.year, row.occurred_on.month)] += amount if row.transaction_type == "Buy" else -amount
    return [{"month": etichetta_mese(*chiave), "amount": round(versato.get(chiave, 0.0), 2)} for chiave in mesi]


def _finestra_periodo(scope: str, year: int, oggi: date) -> tuple[date, date]:
    """Il primo e l'ultimo giorno del periodo su cui la pagina racconta.

    "Ultimi 12 mesi" e' il predefinito perche' a settembre l'anno solare sono
    nove mesi incompleti, e confrontarli con un anno intero direbbe che si spende
    molto meno: non e' vero, e' il calendario. La finestra chiude con il mese di
    oggi e conta dodici mesi compresi, quindi comincia undici mesi prima - a
    settembre, ottobre dell'anno scorso.

    L'anno solare resta per chi lo sceglie, e li' il periodo e' l'anno: le due
    scelte esistono perche' dicono cose diverse, non per abitudine.
    """
    if scope == "last12":
        mese, anno = oggi.month - 11, oggi.year
        if mese <= 0:
            mese, anno = mese + 12, anno - 1
        ultimo_giorno = calendar.monthrange(oggi.year, oggi.month)[1]
        return date(anno, mese, 1), date(oggi.year, oggi.month, ultimo_giorno)
    return date(year, 1, 1), date(year, 12, 31)


def _finestra_precedente(inizio: date, fine: date) -> tuple[date, date]:
    """La finestra di pari durata che finisce il giorno prima di questa.

    Pari durata, non "l'anno prima": un periodo di dodici mesi si confronta con
    dodici mesi, altrimenti la differenza misura la lunghezza del periodo invece
    di cosa e' cambiato. Su un anno solare la finestra precedente e' l'anno
    scorso, perche' gli anni durano uguale.
    """
    giorni = (fine - inizio).days + 1
    prima_fine = inizio - timedelta(days=1)
    return prima_fine - timedelta(days=giorni - 1), prima_fine


def _mediana(valori: list[float]) -> float:
    """La mediana di una lista di numeri, con i mesi a zero dentro.

    Zero e' un'informazione - "quel mese non ho speso niente" - e resta nella
    lista: togliendolo, la mediana direbbe come sono i mesi in cui si spende,
    che e' un'altra domanda. Quanti mesi valgono qualcosa lo dice accanto il
    conteggio dei mesi con movimenti.
    """
    if not valori:
        return 0.0
    ordinati = sorted(valori)
    meta = len(ordinati) // 2
    return ordinati[meta] if len(ordinati) % 2 else (ordinati[meta - 1] + ordinati[meta]) / 2


def etichetta_mese(anno: int, mese: int) -> str:
    """Il nome breve del mese con il suo anno: "Ott 25".

    Le schede mensili di Andamento annuale seguono la finestra dichiarata, e una
    finestra che scorre ne attraversa due: senza l'anno, un asse che comincia a
    ottobre non dice se quell'ottobre e' quello di adesso o quello di un anno fa.
    Il formato e' quello che il frontend gia' traduce (`period-label.ts`).
    """
    return f"{MONTHS[mese - 1]} {anno % 100:02d}"


def _mesi_del_periodo(inizio: date, fine: date, oggi: date) -> list[tuple[int, int]]:
    """I mesi del periodo, e solo quelli gia' cominciati.

    Un anno solare in corso finisce a dicembre, ma i mesi che non sono ancora
    arrivati non sono mesi a zero spese: contarli abbasserebbe la mediana di
    tutte le categorie per colpa del calendario. Gli ultimi dodici mesi
    finiscono con il mese di oggi, e li' non cambia niente.
    """
    ultimo_anno, ultimo_mese = (min(fine, oggi).year, min(fine, oggi).month)
    mesi: list[tuple[int, int]] = []
    anno, mese = inizio.year, inizio.month
    while (anno, mese) <= (ultimo_anno, ultimo_mese):
        mesi.append((anno, mese))
        anno, mese = (anno + 1, 1) if mese == 12 else (anno, mese + 1)
    return mesi


def _mensili_fra_date(session: Session, inizio: date, fine: date,
                           tipo: str) -> dict[int | None, dict[tuple[int, int], float]]:
    """Quanto per categoria, mese per mese, fra due date.

    Si raggruppa qui e non in SQL perche' la mediana vuole i mesi interi, non
    solo il totale del periodo: una query sola, e i mesi sono dodici per
    categoria.
    """
    righe = session.execute(select(Transaction.category_id, Transaction.effective_on, Transaction.amount).where(
        Transaction.effective_on >= inizio, Transaction.effective_on <= fine,
        Transaction.transaction_type == tipo, BUDGET_MOVEMENT)).all()
    per_categoria: dict[int | None, dict[tuple[int, int], float]] = defaultdict(lambda: defaultdict(float))
    for categoria_id, giorno, importo in righe:
        per_categoria[categoria_id][(giorno.year, giorno.month)] += num(importo)
    return per_categoria


def _con_i_figli_mensile(genitori: dict[int, int | None],
                         mensili: dict[int | None, dict[tuple[int, int], float]]
                         ) -> dict[int | None, dict[tuple[int, int], float]]:
    """Gli stessi totali mensili, con dentro quelli dei figli.

    E' `con_i_figli` mese per mese: la regola e' la stessa - un gradino solo, la
    radice vale se stessa piu' i figli - ma quella lavora su mappe piatte, e qui
    i valori sono i mesi invece di un numero solo. L'albero si legge da `padri`,
    come la', e non si risale a mano.
    """
    risultato = {categoria: dict(valori) for categoria, valori in mensili.items()}
    for categoria_id, valori in mensili.items():
        padre = genitori.get(categoria_id) if categoria_id is not None else None
        if padre is None:
            continue
        destinazione = risultato.setdefault(padre, {})
        for mese, valore in valori.items():
            destinazione[mese] = destinazione.get(mese, 0) + valore
    return risultato


def _variazione(attuale: float, prima: float | None) -> dict[str, Any]:
    """Un numero, quello del periodo precedente, e quanto e' cambiato.

    La percentuale e' nulla - non "+infinito" e non "+100%" - quando uno dei due
    periodi e' a zero: un numero nuovo non e' cresciuto di una percentuale, e'
    comparso, e un numero sparito non e' calato del cento per cento, non c'e'
    piu'. Chi legge trova l'importo pieno e un trattino.
    """
    differenza = None if prima is None else round(attuale - prima, 2)
    return {"amount": round(attuale, 2), "previous": prima, "difference": differenza,
            "percent": (round(differenza / prima * 100, 1)
                        if differenza is not None and attuale and prima else None)}


def _flusso(session: Session, finestra: tuple[date, date], precedente: tuple[date, date],
            confrontabile: bool) -> dict[str, Any]:
    """Entrato, uscito, e quello che e' rimasto nel periodo.

    E' la risposta piu' densa della pagina: prima di sapere dove sono andati i
    soldi, uno vuole sapere quanti ne sono passati. Entrate e uscite sono due
    voci distinte e non due numeri da colorare allo stesso modo: per il netto
    non c'e' percentuale, perche' e' gia' una differenza, e con entrate e uscite
    vicine il suo denominatore passa da un numero piccolo a uno negativo, dove
    il segno della percentuale direbbe il contrario di quello che e' successo.
    """
    def _entrate_uscite(periodo: tuple[date, date]) -> tuple[float, float]:
        return (round(sum(_totali_per_categoria(session, *periodo, "Income").values()), 2),
                round(sum(_totali_per_categoria(session, *periodo, "Expenses").values()), 2))

    entrate, uscite = _entrate_uscite(finestra)
    prima_entrate, prima_uscite = _entrate_uscite(precedente) if confrontabile else (None, None)
    netto = round(entrate - uscite, 2)
    prima_netto = round(prima_entrate - prima_uscite, 2) if confrontabile else None
    return {"income": _variazione(entrate, prima_entrate),
            "expenses": _variazione(uscite, prima_uscite),
            "net": {"amount": netto, "previous": prima_netto,
                    "difference": None if prima_netto is None else round(netto - prima_netto, 2),
                    "percent": None}}


def _mosse(righe: list[dict[str, Any]], quante: int = 3) -> list[dict[str, Any]]:
    """Le categorie che spiegano di piu' la differenza: quelle che si sono mosse.

    Non le piu' grandi: una categoria enorme e ferma non spiega niente di cosa e'
    cambiato, e metterla in cima direbbe che il periodo e' andato come sempre. Si
    guarda il valore assoluto della differenza, perche' un calo di trecento euro
    spiega il conto quanto un aumento di trecento.

    Restano fuori le categorie che hanno figli: il loro movimento e' anche quello
    dei figli, e in una lista di tre voci i figli conterebbero due volte.
    """
    genitori = {riga["parentId"] for riga in righe if riga["parentId"] is not None}
    mosse = [riga for riga in righe
             if riga["categoryId"] not in genitori and riga["difference"]]
    mosse.sort(key=lambda riga: abs(riga["difference"]), reverse=True)
    return mosse[:quante]


def _totali_per_categoria(session: Session, inizio: date, fine: date, tipo: str) -> dict[int | None, float]:
    """Quanto e' entrato o uscito per categoria fra due date, estremi compresi.

    Una query sola per periodo invece di una per categoria: le categorie sono
    qualche decina e i periodi due, e il conto delle query si sente
    all'apertura della pagina.
    """
    righe = session.execute(select(Transaction.category_id, func.sum(Transaction.amount)).where(
        Transaction.effective_on >= inizio, Transaction.effective_on <= fine,
        Transaction.transaction_type == tipo, BUDGET_MOVEMENT,
    ).group_by(Transaction.category_id)).all()
    return {categoria_id: num(totale) for categoria_id, totale in righe}


def _prima_data(session: Session) -> date | None:
    """Il giorno del movimento piu' vecchio: da li' in poi i dati esistono.

    Serve a decidere se il periodo precedente si puo' confrontare. Se i dati
    cominciano dopo, quel periodo e' vuoto perche' l'app non c'era ancora, non
    perche' non si spendeva: confrontarcisi direbbe che ogni categoria e'
    cresciuta di tutto, cioe' aumenti del mille per cento che non vogliono dire
    niente.
    """
    return session.scalar(select(func.min(Transaction.effective_on)).where(BUDGET_MOVEMENT))


def _confronto_categorie(session: Session, tipo: str, finestra: tuple[date, date],
                         precedente: tuple[date, date], confrontabile: bool,
                         oggi: date) -> list[dict[str, Any]]:
    """Le categorie del periodo, accanto alle stesse del periodo precedente.

    I totali passano da `con_i_figli` - o dal suo gemello mensile, che serve alla
    mediana: una radice vale se stessa piu' i suoi figli, e "Alimentari" nei due
    periodi deve parlare della stessa cosa.

    La percentuale e' nulla - non "+100%" - quando il periodo precedente era a
    zero, e anche quando e' a zero adesso. Una categoria nuova e' cresciuta di
    tutto, e "di tutto" non e' una percentuale: chi legge trova l'importo pieno
    e un trattino al posto di un numero inventato.

    La mediana viaggia con i mesi che l'hanno formata, come nei suggerimenti di
    budget: una mediana su un mese solo, per quanto tonda, non e' una mediana, e
    chi legge deve poterlo vedere invece di fidarsi.
    """
    genitori = padri(session)
    mensili = _con_i_figli_mensile(genitori, _mensili_fra_date(session, *finestra, tipo))
    attuali = {categoria: round(sum(valori.values()), 2) for categoria, valori in mensili.items()}
    precedenti = (con_i_figli(session, _totali_per_categoria(session, *precedente, tipo))
                  if confrontabile else {})
    nomi_cat = nomi_categorie(session)
    # Il verso di ogni categoria viaggia con la riga: e' quello che decide di che
    # colore si scrive una differenza, e indovinarlo a video dal segno direbbe
    # che guadagnare di piu' e' un problema.
    versi = dict(session.execute(select(Category.id, Category.scope)).all())
    mesi = _mesi_del_periodo(*finestra, oggi)
    righe: list[dict[str, Any]] = []
    for categoria_id in set(attuali) | set(precedenti):
        # Un movimento senza categoria e' denaro uscito davvero, e il flusso lo
        # conta, ma non e' una voce dell'albero: in una tabella di categorie
        # sarebbe una riga senza nome.
        if categoria_id is None:
            continue
        per_mese = [round(mensili.get(categoria_id, {}).get(mese, 0), 2) for mese in mesi]
        con_movimenti = sum(1 for valore in per_mese if valore)
        righe.append({"categoryId": categoria_id, "parentId": genitori.get(categoria_id),
                      "name": nomi_cat.get(categoria_id, ""),
                      "scope": versi.get(categoria_id, "expense"),
                      **_variazione(attuali.get(categoria_id, 0),
                                    round(precedenti.get(categoria_id, 0), 2) if confrontabile else None),
                      # Senza movimenti non c'e' una mediana da leggere: zero
                      # sarebbe un numero, e direbbe un'altra cosa.
                      "median": round(_mediana(per_mese), 2) if con_movimenti else None,
                      "monthsWithMovements": con_movimenti, "monthsConsidered": len(mesi)})
    righe.sort(key=lambda riga: (-riga["amount"], riga["name"].casefold()))
    return righe


@router.get("/api/analysis")
def analysis(
    year: int = Query(ge=2000, le=2100),
    scope: Literal["last12", "year"] = Query("last12", description="last12: gli ultimi dodici mesi; year: l'anno solare"),
    category_type: str = Query("Expenses", description="Income, Expenses o Savings, per Category Analysis"),
    category: str | None = Query(None, description="Categoria per Category Analysis; se omessa nessuna transazione viene restituita"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Vista 'Andamento annuale': il periodo dichiarato, e tutto quello che ci sta dentro.
    - period: su quale finestra la pagina racconta, date comprese. Da qui in giu'
      tutto segue questa finestra - con l'anno scelto sono gennaio-dicembre, con
      gli ultimi dodici mesi i dodici che finiscono con il mese di oggi. Chi
      legge deve sapere su cosa sono calcolati i numeri: un report che non lo
      dice costringe a fidarsi, e uno che lo dice e poi disegna un altro periodo
      e' peggio che non dirlo.
    - monthlyBudget: per Income/Expenses/Savings, i dodici mesi della finestra x
      {inBudget, remaining, excess}, con isCurrentMonth per evidenziare il mese in
      corso (Budget Dashboard AF52:AN72). Etichette con l'anno: "Ott 25".
    - topExpenseCategories: le 10 categorie di spesa maggiori della finestra, per
      un treemap (Budget Trends, 'Top 10 impactful Exp. Category').
    - savingsByMonth: risparmio netto per ciascuno dei dodici mesi della finestra,
      e `investedByMonth` sugli stessi mesi (Budget Trends, 'Savings by month').
    - categoryTransactions: le 10 transazioni di importo piu' alto per category_type/category
      nella finestra (Budget Trends, 'Category Analysis' + 'Top 10 Transactions').
    - comparison: il periodo precedente di pari durata, e se i dati cominciano
      abbastanza indietro da poterlo confrontare (`available`, `since`).
    - flow: entrato, uscito e rimasto nel periodo, ciascuno con il periodo prima
      e la differenza, piu' `movers`: le tre categorie che si sono mosse di piu',
      che sono quelle che spiegano la differenza.
    - categoryComparison: per categoria, quanto in questo periodo e quanto nel
      precedente, con differenza, percentuale e mediana mensile (`null` dove un
      numero non esiste: percentuale di una categoria nuova o sparita, mediana
      senza movimenti, confronto che non si fa). Segue `category_type` come gli
      altri blocchi per categoria; per Savings resta vuoto, perche' il tipo di
      movimento Savings non esiste piu'.
    """
    types = ("Income", "Expenses", "Savings")
    today = date.today()
    inizio, fine = _finestra_periodo(scope, year, today)
    # Tutti i mesi della finestra, anche quelli non ancora cominciati: e' l'asse
    # delle schede mensili, e per un anno solare in corso resta a dodici barre
    # come prima, le ultime mezze vuote.
    mesi_finestra = _mesi_del_periodo(inizio, fine, fine)

    # Una sola query per tipo su tutta la finestra (grazie a
    # `budget_actual_fra_date`, che gestisce anche i rimborsi in un'unica
    # sotto-query) invece di dodici chiamate di `period_total` o
    # `derived_savings` per ciascuno. Il risparmio si ricava da entrate meno
    # spese dello stesso mese: chiederlo al database una terza volta era la
    # stessa domanda fatta tre volte.
    speso_fra_date = {t: budget_actual_fra_date(session, inizio, fine, t) for t in ("Income", "Expenses")}
    entrate_mese = {chiave: round(sum(valori.values()), 2) for chiave, valori in speso_fra_date["Income"].items()}
    spese_mese = {chiave: round(sum(valori.values()), 2) for chiave, valori in speso_fra_date["Expenses"].items()}
    risparmio_mese = {chiave: round(entrate_mese.get(chiave, 0.0) - spese_mese.get(chiave, 0.0), 2)
                      for chiave in set(entrate_mese) | set(spese_mese)}
    speso_per_tipo: dict[str, dict[tuple[int, int], float]] = {
        "income": entrate_mese, "expenses": spese_mese, "savings": risparmio_mese}

    # I piani della finestra, una query sola, cercati per anno e mese: a ottobre
    # 2025 non si applica il budget di ottobre 2026, e in una finestra che scorre
    # i due convivono.
    piani = session.scalars(select(BudgetPlan).where(
        BudgetPlan.period >= date(inizio.year, inizio.month, 1),
        BudgetPlan.period <= date(fine.year, fine.month, 1))).all()
    pianificato: dict[tuple[str, int, int], float] = defaultdict(float)
    for piano in piani:
        pianificato[(piano.budget_type, piano.period.year, piano.period.month)] += num(piano.amount)

    monthly_budget: dict[str, list[dict[str, Any]]] = {}
    for t in types:
        rows = []
        for anno, mese in mesi_finestra:
            tracked = speso_per_tipo[t.lower()].get((anno, mese), 0.0)
            budget = round(pianificato.get((t, anno, mese), 0), 2)
            delta = round(budget - tracked, 2)
            rows.append({
                "month": etichetta_mese(anno, mese),
                "inBudget": round(min(budget, tracked), 2),
                "remaining": delta if delta > 0 else 0,
                "excess": round(-delta, 2) if delta < 0 else 0,
                # L'unico mese acceso e' quello in corso, e nella finestra che
                # scorre e' l'ultimo: e' il mese che si sta vivendo adesso.
                "isCurrentMonth": (anno, mese) == (today.year, today.month),
            })
        monthly_budget[t.lower()] = rows

    # Le categorie piu' pesanti della finestra, con il valore proprio di
    # ciascuna: il padre non si porta dietro i figli, che sono righe loro.
    per_categoria: dict[int, float] = defaultdict(float)
    for valori in speso_fra_date["Expenses"].values():
        for categoria_id, importo in valori.items():
            per_categoria[categoria_id] += importo
    nomi = nomi_categorie(session)
    pesanti = sorted((voce for voce in per_categoria.items() if voce[1] > 0), key=lambda voce: voce[1], reverse=True)
    top_expense_categories = [{"name": nomi.get(categoria_id, ""), "value": round(importo, 2),
                               "color": PIE_COLORS[i % len(PIE_COLORS)]}
                              for i, (categoria_id, importo) in enumerate(pesanti[:10])]

    savings_by_month = [{"month": etichetta_mese(anno, mese), "amount": risparmio_mese.get((anno, mese), 0.0)}
                        for anno, mese in mesi_finestra]

    category_transactions: list[dict[str, Any]] = []
    if category:
        rows = session.scalars(select(Transaction).where(
            Transaction.effective_on >= inizio, Transaction.effective_on <= fine,
            Transaction.transaction_type == category_type,
            Transaction.category_id.in_(categoria_per_nome(category)),
            BUDGET_MOVEMENT,
        ).order_by(Transaction.amount.desc()).limit(10)).all()
        category_transactions = [{"date": row.effective_on.isoformat(), "amount": num(row.amount),
                                  "description": row.details or nomi.get(row.category_id, "")} for row in rows]

    # Le categorie da scegliere sono quelle del periodo dichiarato e del tipo
    # scelto. Il frontend le prendeva dalla Panoramica, cioe' dal mese
    # selezionato li': una categoria senza movimenti in quel mese non c'era.
    category_options = sorted({nomi.get(categoria_id, "") for categoria_id in session.scalars(
        select(Transaction.category_id).where(
            Transaction.effective_on >= inizio, Transaction.effective_on <= fine,
            Transaction.transaction_type == category_type,
            BUDGET_MOVEMENT).distinct()) if nomi.get(categoria_id)}, key=str.casefold)

    # Il periodo precedente di pari durata, e se c'e' storia abbastanza per
    # confrontarlo. Le due cose viaggiano insieme: "non si confronta" senza dire
    # da quando esistono i dati sarebbe un silenzio, non una risposta.
    precedente = _finestra_precedente(inizio, fine)
    prima_data = _prima_data(session)
    confrontabile = prima_data is not None and prima_data <= precedente[0]

    confronto = _confronto_categorie(session, category_type, (inizio, fine), precedente, confrontabile, today)
    # Le tre categorie che spiegano la differenza si cercano fra le spese anche
    # quando la tabella sta mostrando le entrate: il blocco del flusso racconta
    # dove sono finiti i soldi, e quello e' sempre la stessa domanda. Chiedere di
    # nuovo il confronto delle spese serve solo se non e' gia' quello di sopra.
    confronto_spese = (confronto if category_type == "Expenses"
                       else _confronto_categorie(session, "Expenses", (inizio, fine), precedente,
                                                 confrontabile, today))

    return {
        "period": {"scope": scope, "from": inizio.isoformat(), "to": fine.isoformat()},
        "comparison": {"from": precedente[0].isoformat(), "to": precedente[1].isoformat(),
                       "available": confrontabile,
                       "since": prima_data.isoformat() if prima_data else None},
        "flow": {**_flusso(session, (inizio, fine), precedente, confrontabile),
                 "movers": _mosse(confronto_spese)},
        "categoryComparison": confronto,
        "monthlyBudget": monthly_budget,
        "topExpenseCategories": top_expense_categories,
        "savingsByMonth": savings_by_month,
        "investedByMonth": _invested_by_month(session, inizio, fine, mesi_finestra),
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
    event_id: int | None = None,
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
    if event_id is not None:
        # L'evento si guarda dai suoi agganci, non dalle date: un movimento
        # pagato fuori dal periodo dell'evento ne fa parte lo stesso.
        evento_clause = Transaction.id.in_(
            select(TransactionEvent.transaction_id).where(TransactionEvent.event_id == event_id))
        query = query.where(evento_clause)
        count_query = count_query.where(evento_clause)
    if category:
        query = query.where(Transaction.category_id.in_(categoria_per_nome(category)))
        count_query = count_query.where(Transaction.category_id.in_(categoria_per_nome(category)))
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
        # La categoria adesso e' una riga a parte: si cerca sul suo nome e si
        # tiene il movimento se la categoria che ha lo contiene.
        search_clause = (
            (func.lower(func.coalesce(Transaction.details, "")).like(needle))
            | (Transaction.category_id.in_(select(Category.id).where(func.lower(Category.name).like(needle))))
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
    eventi_dei_movimenti = _eventi_per_movimenti(session, ids_righe)
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
    # Gli eventi da mettere nella tendina del filtro e in quella del modulo: come
    # gli anni e gli obiettivi, non dipendono dai filtri attivi, altrimenti
    # filtrando per un evento sparirebbero gli altri due.
    elenco_eventi = [{"id": riga.id, "name": riga.name, "closed": riga.closed} for riga in session.scalars(
        select(Event).order_by(Event.closed, Event.start_date.is_(None), Event.start_date, Event.id))]
    nomi_cat = nomi_categorie(session)
    return {"items": [{**transaction_json(row, linked=collegati.get(row.id, []), liability=rate.get(row.id),
                                          categoria=nomi_cat.get(row.category_id, ""),
                                          evento=eventi_dei_movimenti.get(row.id)), "refundedById": refunds.get(row.id)} for row in rows],
            "total": session.scalar(count_query) or 0,
            "offset": offset, "limit": limit, "years": anni, "goals": obiettivi,
            "events": elenco_eventi}


# Eventi: quanto e' costato quel viaggio, tutto compreso
# ---------------------------------------------------------------------------
#
# Un evento taglia le categorie invece di essere una categoria: il viaggio non
# sta solo in "Viaggi", ci sono i ristoranti, i trasporti e la benzina di quei
# giorni. L'appartenenza e' una riga in `transaction_events` - una sola per
# movimento - e le date servono a proporre i movimenti del periodo, non a
# decidere chi ne fa parte.

class EventPayload(BaseModel):
    """Il nome c'e' sempre; note, date e chiusura si aggiungono dopo."""

    name: str
    notes: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    closed: bool = False


class TransactionEventPayload(BaseModel):
    """L'evento da agganciare a un movimento. Nullo vuol dire "sgancia"."""

    event_id: int | None = None


def _ordine_eventi() -> list[Any]:
    """Aperti prima, chiusi in fondo, e dentro ognuno per data d'inizio.

    Un evento senza date non si sa quando sia successo e sta in fondo anche fra
    i chiusi: in mezzo agli altri sembrerebbe cominciato chissa' quando.
    """
    return [Event.closed, Event.start_date.is_(None), Event.start_date, Event.id]


def _eventi_per_movimenti(session: Session, tx_ids: list[int]) -> dict[int, dict[str, Any]]:
    """L'evento di ogni movimento, in una interrogazione sola.

    Uno per movimento: la chiave primaria di `transaction_events` e' il
    movimento stesso, quindi non ci sono due righe fra cui scegliere.
    """
    if not tx_ids:
        return {}
    righe = session.execute(
        select(TransactionEvent.transaction_id, Event)
        .join(Event, Event.id == TransactionEvent.event_id)
        .where(TransactionEvent.transaction_id.in_(tx_ids))).all()
    return {tx_id: {"id": evento.id, "name": evento.name, "closed": evento.closed}
            for tx_id, evento in righe}


def _ripartizione_evento(session: Session, event_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Quanto ha pesato ogni categoria dentro ogni evento, in una interrogazione.

    Stessa aritmetica dei conteggi del budget: solo i movimenti che contano
    (`BUDGET_MOVEMENT`, quindi fuori i giriconti e i modelli delle ricorrenze),
    e i rimborsi tolti dalla categoria del movimento che rimborsano. Un
    rimborso agganciato a un evento senza il suo originale non muove niente,
    come non muove niente nel budget: da solo non e' ne' una spesa ne' un
    introito.

    La categoria e' un id: due categorie con lo stesso nome sotto padri diversi
    sono due voci distinte, e sommarle per nome le avrebbe fuse in una riga
    sola. Il nome si scrive alla fine, per chi legge. Un movimento senza
    categoria non si perde: entra nella ripartizione con il nome vuoto, che
    l'interfaccia sa come chiamare.
    """
    voci: dict[int, dict[int | None, dict[str, float]]] = {
        event_id: defaultdict(lambda: {"spese": 0.0, "entrate": 0.0}) for event_id in event_ids}
    if not event_ids:
        return {}
    righe = session.execute(
        select(TransactionEvent.event_id, Transaction.id, Transaction.category_id,
               Transaction.transaction_type, Transaction.amount)
        .join(Transaction, Transaction.id == TransactionEvent.transaction_id)
        .where(TransactionEvent.event_id.in_(event_ids), BUDGET_MOVEMENT)).all()
    # Dove sottrarre il rimborso: la categoria e la parte del movimento che
    # rimborsa. Un movimento sta in un evento solo, quindi la mappa non si
    # sovrascrive.
    originali: dict[int, tuple[int, int | None, str]] = {}
    for event_id, tx_id, categoria_id, tipo, importo in righe:
        chiave = "entrate" if tipo == "Income" else "spese"
        voci[event_id][categoria_id][chiave] += num(importo)
        originali[tx_id] = (event_id, categoria_id, chiave)
    if originali:
        for rimborso in session.scalars(select(Transaction).where(
                Transaction.refund_of_id.in_(list(originali)), REAL_MOVEMENT)).all():
            event_id, categoria_id, chiave = originali[rimborso.refund_of_id]
            voci[event_id][categoria_id][chiave] -= num(rimborso.amount)
    nomi = nomi_categorie(session)
    return {event_id: [{"name": nomi.get(categoria_id, ""), "spese": num(valori["spese"]),
                        "entrate": num(valori["entrate"])}
                       # Prima le categorie che hanno pesato di piu', e a parita'
                       # per nome: senza id l'ordine sarebbe quello dei dizionari.
                       for categoria_id, valori in sorted(
                           per_categoria.items(),
                           key=lambda voce: (-voce[1]["spese"], nomi.get(voce[0], "").casefold(), voce[0] or 0))]
            for event_id, per_categoria in voci.items()}


def _numeri_eventi(session: Session, event_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Movimenti, spese, entrate e netto di ogni evento chiesto.

    I totali sono la somma della ripartizione, non un conto fatto a parte: se
    le due cose si calcolassero per conto loro, prima o poi una direbbe un
    numero e l'altra un altro.

    Il numero di movimenti invece conta tutti quelli agganciati, anche quelli
    che non entrano nei totali: dice quanto materiale c'e' dentro l'evento, e
    sotto c'e' comunque l'elenco che li mostra tutti.
    """
    numeri: dict[int, dict[str, Any]] = {event_id: {"movimenti": 0, "spese": 0.0, "entrate": 0.0}
                                         for event_id in event_ids}
    if not event_ids:
        return numeri
    for event_id, voci in _ripartizione_evento(session, event_ids).items():
        for voce in voci:
            numeri[event_id]["spese"] += voce["spese"]
            numeri[event_id]["entrate"] += voce["entrate"]
    for event_id, quanti in session.execute(
            select(TransactionEvent.event_id, func.count(TransactionEvent.transaction_id))
            .join(Transaction, Transaction.id == TransactionEvent.transaction_id)
            .where(TransactionEvent.event_id.in_(event_ids), REAL_MOVEMENT)
            .group_by(TransactionEvent.event_id)).all():
        numeri[event_id]["movimenti"] = quanti
    for valori in numeri.values():
        valori["spese"], valori["entrate"] = num(valori["spese"]), num(valori["entrate"])
    return numeri


def _evento_json(riga: Event, numeri: dict[str, Any] | None = None) -> dict[str, Any]:
    """Un evento con i suoi numeri.

    Spese ed entrate restano separate: un viaggio con un rimborso e' costato il
    lordo meno il rimborso, ma un numero solo nasconderebbe meta' della storia.
    Il netto e' la differenza, e il suo segno dice da che parte pende.
    """
    valori = numeri or {}
    spese, entrate = num(valori.get("spese")), num(valori.get("entrate"))
    return {"id": riga.id, "name": riga.name, "notes": riga.notes,
            "startDate": riga.start_date.isoformat() if riga.start_date else None,
            "endDate": riga.end_date.isoformat() if riga.end_date else None,
            "closed": riga.closed, "movimenti": int(valori.get("movimenti") or 0),
            "spese": spese, "entrate": entrate, "netto": num(entrate - spese)}


def _valida_evento(payload: EventPayload, session: Session, *, esistente: Event | None = None) -> None:
    """Le regole che valgono sia creando sia rinominando."""
    nome = (payload.name or "").strip()
    if not nome:
        raise HTTPException(status_code=422, detail="eventNameRequired")
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="eventDateRange")
    # Il vincolo sulla tabella c'e' gia' e resta come rete per due salvataggi
    # simultanei; questo lo anticipa per rispondere con un codice invece che con
    # un errore di integrita', e vale anche rinominando un evento che esiste.
    condizione = [Event.name == nome]
    if esistente is not None:
        condizione.append(Event.id != esistente.id)
    if session.scalar(select(Event.id).where(*condizione)) is not None:
        raise HTTPException(status_code=422, detail="eventDuplicate")


@router.get("/api/events")
def events(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Tutti gli eventi con i loro numeri: e' quello che si vede nella card."""
    righe = session.scalars(select(Event).order_by(*_ordine_eventi())).all()
    numeri = _numeri_eventi(session, [riga.id for riga in righe])
    return {"items": [_evento_json(riga, numeri.get(riga.id)) for riga in righe]}


@router.post("/api/events", status_code=201)
def create_event(payload: EventPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Crea un evento: nasce aperto, vuoto e senza date se non gliele dai."""
    _valida_evento(payload, session)
    riga = Event(name=payload.name.strip(), notes=payload.notes,
                 start_date=payload.start_date, end_date=payload.end_date, closed=payload.closed)
    session.add(riga)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="eventDuplicate") from error
    return _evento_json(riga)


@router.put("/api/events/{event_id}")
def update_event(event_id: int, payload: EventPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Rinomina, sposta nel tempo, chiude o riapre. I movimenti restano dove sono."""
    riga = session.get(Event, event_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="eventNotFound")
    _valida_evento(payload, session, esistente=riga)
    riga.name, riga.notes = payload.name.strip(), payload.notes
    riga.start_date, riga.end_date = payload.start_date, payload.end_date
    riga.closed = payload.closed
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="eventDuplicate") from error
    return _evento_json(riga, _numeri_eventi(session, [riga.id]).get(riga.id))


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Elimina l'evento e i suoi agganci. I movimenti restano tutti.

    Un viaggio cancellato non deve portarsi via le spese: sono soldi spesi
    davvero, con la loro categoria e il loro posto nei conti. Gli agganci se ne
    vanno con l'evento per via della chiave esterna.
    """
    riga = session.get(Event, event_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="eventNotFound")
    session.delete(riga)
    session.commit()
    return {"success": True}


@router.get("/api/events/{event_id}")
def event_detail(event_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Il dettaglio: i movimenti agganciati e la ripartizione per categoria."""
    riga = session.get(Event, event_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="eventNotFound")
    movimenti = session.scalars(
        select(Transaction).join(TransactionEvent, TransactionEvent.transaction_id == Transaction.id)
        .where(TransactionEvent.event_id == event_id, REAL_MOVEMENT)
        .order_by(Transaction.effective_on, Transaction.id)).all()
    ids = [movimento.id for movimento in movimenti]
    collegati = _linked_ledger_for_transactions(session, ids)
    rate = _liability_details_for_transactions(session, ids)
    eventi_dei_movimenti = _eventi_per_movimenti(session, ids)
    rimborsati = dict(session.execute(select(Transaction.refund_of_id, Transaction.id).where(
        Transaction.refund_of_id.in_(ids))).all())
    nomi_cat = nomi_categorie(session)
    return {"event": _evento_json(riga, _numeri_eventi(session, [riga.id]).get(riga.id)),
            "movements": [{**transaction_json(row, linked=collegati.get(row.id, []), liability=rate.get(row.id),
                                              categoria=nomi_cat.get(row.category_id, ""),
                                              evento=eventi_dei_movimenti.get(row.id)),
                           "refundedById": rimborsati.get(row.id)} for row in movimenti],
            "categories": _ripartizione_evento(session, [riga.id]).get(riga.id, [])}


@router.put("/api/transactions/{tx_id}/event")
def set_transaction_event(tx_id: int, payload: TransactionEventPayload,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    """Aggancia un movimento a un evento, o lo sgancia con `event_id` nullo.

    Uno solo per movimento: agganciarlo a un secondo evento sostituisce il
    primo, non ne aggiunge uno. Un movimento che starebbe in due viaggi e' un
    movimento da dividere, e dividerlo in due questa app la sa gia' fare.
    """
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="movementNotFound")
    aggancio = session.get(TransactionEvent, tx_id)
    if payload.event_id is None:
        if aggancio is not None:
            session.delete(aggancio)
    else:
        evento = session.get(Event, payload.event_id)
        if evento is None:
            raise HTTPException(status_code=404, detail="eventNotFound")
        if aggancio is None:
            session.add(TransactionEvent(transaction_id=tx_id, event_id=evento.id))
        else:
            aggancio.event_id = evento.id
    session.commit()
    return {"success": True, "transaction": transaction_json(tx, session)}


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
    # I nomi delle categorie si scrivono solo qui, al confine: dentro l'app sono
    # id, e l'interfaccia di oggi conosce ancora i nomi.
    nomi = nomi_categorie(session)
    options["categories"] = sorted((nome for nome in nomi.values() if nome), key=str.casefold)
    # Le categorie appartengono a un verso: una spesa non puo' essere "Stipendio".
    # Il verso e' un attributo della categoria e non si ricava da come e' stata
    # usata finora: cosi' una categoria appena creata - che nessun movimento e
    # nessun budget nomina ancora - e' sceglibile subito, che e' il motivo per
    # cui la si e' creata. Quello che non si sceglie e' una categoria di spesa
    # in una tendina di entrate: sono domande diverse.
    #
    # Una radice che ha figli resta sceglibile: e' dove stanno i movimenti non
    # ancora spostati nei figli, e nasconderla vorrebbe dire non poter spaccare
    # una categoria senza prima spostare tutto quello che c'e' dentro.
    by_type: dict[str, set[str]] = {kind: set() for kind in ("Income", "Expenses", "Savings")}
    for riga in session.scalars(select(Category)).all():
        if riga.active and riga.name:
            by_type["Income" if riga.scope == "income" else "Expenses"].add(riga.name)
    # Il risparmio non e' un verso: e' quello che resta, e va a finire su una
    # categoria di spesa che l'utente ha scelto fra quelle che ha. La tendina
    # del risparmio continua a offrire quella, se c'e'.
    if savings_category(session) in by_type["Expenses"]:
        by_type["Savings"].add(savings_category(session))
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
    return {"settings": {row.key: row.value for row in setting_rows}, "labels": {row.key: row.label for row in setting_rows}, "options": options, "categoriesByType": categories_by_type, "budgetYearsByType": budget_years_by_type, "categoryTree": radici_con_figli(session)}


def budget_actual(session: Session, year: int, month: int, budget_type: str = "Expenses") -> dict[int, float]:
    """Quanto si e' speso (o incassato) per categoria in un mese.

    Le chiavi sono id di categoria: il confronto con il pianificato e' allora
    esatto, senza dover abbassare le maiuscole per sommare insieme "Spesa" e
    "spesa" - che adesso sono la stessa riga, scritta una volta sola.
    """
    if budget_type == "Savings":
        # Il risparmio effettivo e' calcolato, non sommato dai movimenti.
        return {savings_category_id(session): derived_savings(session, year, month)}
    rows = session.scalars(select(Transaction).where(
        extract("year", Transaction.effective_on) == year,
        extract("month", Transaction.effective_on) == month,
        Transaction.transaction_type == budget_type, BUDGET_MOVEMENT)).all()
    totals: dict[int, float] = defaultdict(float)
    for row in rows:
        if row.category_id is not None:
            totals[row.category_id] += float(row.amount)
    if budget_type in {"Income", "Expenses"} and rows:
        refunds = session.execute(select(Transaction.refund_of_id, Transaction.amount).where(
            Transaction.refund_of_id.in_([row.id for row in rows]), REAL_MOVEMENT)).all()
        for original_id, amount in refunds:
            original = next(row for row in rows if row.id == original_id)
            if original.category_id is not None:
                totals[original.category_id] -= float(amount)
    return {key: round(value, 2) for key, value in totals.items()}


# Bisogni e piaceri, e il residuo che passa al mese dopo.
# ---------------------------------------------------------------------------

# "Other" non e' una terza categoria di spesa: e' dove finisce cio' che non hai
# ancora classificato. Tenerla visibile serve proprio a farla svuotare.
# Le tre parole stanno in `categorie.GRUPPI`, che e' dove si legge l'albero:
# qui restano col nome che l'interfaccia conosce.
CATEGORY_GROUPS = GRUPPI


def _needs_wants(session: Session, year: int, month: int | None) -> list[dict[str, Any]]:
    """Ripartizione bisogni/piaceri del periodo, pianificata ed effettiva.

    Guarda tutta la spesa, non solo quella a budget: una categoria su cui hai
    speso senza averla pianificata e' esattamente quella che vuoi vedere.

    Il gruppo non si legge piu' dalle righe di budget - era la stessa parola
    ripetuta per mese - ma dall'albero: bisogni e piaceri sono le radici che
    portano quei nomi, e una categoria sta sotto quella che le somiglia.
    """
    gruppi = gruppo_di_categoria(session)
    mesi = [month] if month is not None else range(1, 13)
    speso: dict[int | None, float] = defaultdict(float)
    for mese in mesi:
        for categoria, valore in budget_actual(session, year, mese, "Expenses").items():
            speso[categoria] += valore
    pianificato: dict[int | None, float] = defaultdict(float)
    for piano in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == date(year, month, 1) if month is not None else extract("year", BudgetPlan.period) == year,
            BudgetPlan.budget_type == "Expenses")).all():
        pianificato[piano.category_id] += num(piano.amount)

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
                               category_id=categoria_da_nome(session, savings_category(session)),
                               amount=atteso))
    else:
        riga.amount = atteso
    return atteso


def previous_month_leftover(session: Session, year: int, month: int,
                            budget_type: str = "Expenses") -> dict[int | None, float]:
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
    pianificato: dict[int | None, float] = defaultdict(float)
    for piano in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == precedente, BudgetPlan.budget_type == budget_type)).all():
        pianificato[piano.category_id] += num(piano.amount)
    effettivo: dict[int | None, float] = defaultdict(float)
    for categoria, totale in session.execute(select(Transaction.category_id, func.sum(Transaction.amount)).where(
            extract("year", Transaction.effective_on) == year,
            extract("month", Transaction.effective_on) == month - 1,
            Transaction.transaction_type == budget_type,
            BUDGET_MOVEMENT).group_by(Transaction.category_id)).all():
        if categoria is not None:
            effettivo[categoria] += float(totale or 0)
    # I rimborsi nettono dall'importo della categoria dell'originale nello stesso
    # mese dell'originale (non del rimborso: uno puo' rimborsare a gennaio una
    # spesa di novembre, e il netting va applicato dove il budget era stato
    # registrato). Senza questo, un rimborso di 200 su una spesa di 500 fa'
    # sembrare di essere sforati di 200 anche se il budget era ok.
    if budget_type in {"Income", "Expenses"}:
        Originale = aliased(Transaction, name="originale")
        rimborsi = session.execute(select(
            Originale.category_id, Transaction.amount,
        ).join(Originale, Transaction.refund_of_id == Originale.id).where(
            REAL_MOVEMENT,
            Transaction.refund_of_id.is_not(None),
            Originale.counts_in_budget.is_(True),
            Originale.transaction_type == budget_type,
            extract("year", Originale.effective_on) == year,
            extract("month", Originale.effective_on) == month - 1,
        )).all()
        for categoria_originale, importo in rimborsi:
            if categoria_originale is not None:
                effettivo[categoria_originale] -= float(importo or 0)
    avanzi = {chiave: round(valore - effettivo.get(chiave, 0.0), 2) for chiave, valore in pianificato.items()}
    return {chiave: valore for chiave, valore in avanzi.items() if valore}


def _mensili_per_categoria(session: Session, year: int, month: int, budget_type: str,
                           months_back: int) -> tuple[list[tuple[int, int]],
                                                     dict[int | None, dict[tuple[int, int], float]]]:
    """Totali mensili per categoria nei mesi che precedono quello indicato.

    Serve ai suggerimenti del budget e alla stima di fine mese: sono la stessa
    lettura dello storico, e averne una copia per uso significa poterle far
    dire due cose diverse.

    Le chiavi sono id: due categorie con lo stesso nome sotto padri diversi
    hanno storici diversi, e sommarle per nome ne avrebbe fatto una sola.
    """
    finestra = [((year * 12 + month - 1) - passo) for passo in range(1, months_back + 1)]
    periodi = [(indice // 12, indice % 12 + 1) for indice in finestra]
    per_categoria: dict[int | None, dict[tuple[int, int], float]] = defaultdict(dict)
    if budget_type == "Savings":
        categoria = savings_category_id(session)
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
        Transaction.category_id, func.sum(Transaction.amount),
    ).where(
        Transaction.effective_on >= inizio,
        Transaction.effective_on < fine,
        Transaction.transaction_type == budget_type,
        BUDGET_MOVEMENT,
    ).group_by(
        extract("year", Transaction.effective_on), extract("month", Transaction.effective_on),
        Transaction.category_id,
    )).all()
    for anno, mese, categoria_id, totale in righe:
        if categoria_id is not None:
            per_categoria[categoria_id][(int(anno), int(mese))] = float(totale or 0)
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
    mediane = {categoria_id: _mediana([valori.get(periodo, 0.0) for periodo in periodi])
               for categoria_id, valori in storico.items()}
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
    nomi = nomi_categorie(session)

    items = []
    for categoria_id, valori in per_categoria.items():
        mensili = [round(valori.get(periodo, 0.0), 2) for periodo in periodi]
        con_spesa = [valore for valore in mensili if valore]
        if not con_spesa:
            continue
        # La formula sta in `_mediana`: la pagina Analisi calcola la stessa cosa
        # sugli stessi mesi, e due formule in due posti divergono.
        mediana = _mediana(mensili)
        items.append({
            # Senza id nel dizionario la categoria del risparmio non esiste
            # ancora come riga: si mostra il nome che avra'.
            "category": nomi.get(categoria_id, "") if categoria_id is not None else savings_category(session),
            # L'id accanto al nome: e' con quello che si scrive la riga di budget,
            # e due figli omonimi sotto padri diversi hanno nomi uguali.
            "categoryId": categoria_id,
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


def budget_actual_year(session: Session, year: int, budget_type: str = "Expenses") -> dict[int, dict[int, float]]:
    """Lo speso di un anno intero, mese per mese e categoria per categoria.

    Una interrogazione al posto di una per cella: la griglia annuale ne chiedeva
    dodici per ogni categoria, e l'andamento la ricostruiva per ogni anno. Con
    trenta categorie e tre anni erano piu' di mille interrogazioni per disegnare
    una pagina, ed e' il motivo per cui l'app sembrava pensarci su.

    Le chiavi delle categorie sono id, come in `budget_actual`: il confronto con
    il pianificato e' esatto, e due categorie omonime restano due voci.
    """
    if budget_type == "Savings":
        categoria = savings_category_id(session)
        mensili = _totali_mensili(session, year, "Savings")
        return {mese: {categoria: mensili.get(mese, 0.0)} for mese in range(1, 13)}
    per_data = budget_actual_fra_date(session, date(year, 1, 1), date(year, 12, 31), budget_type)
    return {mese: per_data.get((year, mese), {}) for mese in range(1, 13)}


def budget_actual_fra_date(session: Session, inizio: date, fine: date,
                           budget_type: str = "Expenses") -> dict[tuple[int, int], dict[int, float]]:
    """Lo speso fra due date, mese per mese e categoria per categoria.

    E' `budget_actual_year` senza l'anno: le chiavi sono `(anno, mese)` perche'
    una finestra di dodici mesi che scorre ne attraversa due, e a ottobre 2025
    non si applica il budget di ottobre 2026. Una query sola, come la', e i
    rimborsi si nettono allo stesso modo - sulla data dell'originale.
    """
    per_mese: dict[tuple[int, int], dict[int, float]] = defaultdict(lambda: defaultdict(float))
    # Una query per le transazioni "budgeable" del periodo, aggregate per mese e
    # categoria.
    righe = session.execute(select(
        extract("year", Transaction.effective_on), extract("month", Transaction.effective_on),
        Transaction.category_id, Transaction.amount,
    ).where(
        Transaction.effective_on >= inizio, Transaction.effective_on <= fine,
        Transaction.transaction_type == budget_type,
        BUDGET_MOVEMENT,
    )).all()
    for anno, mese, categoria_id, importo in righe:
        if categoria_id is not None:
            per_mese[(int(anno), int(mese))][categoria_id] += float(importo or 0)
    # I rimborsi nettono dall'importo della categoria dell'originale nello stesso
    # mese dell'originale (non del rimborso: uno puo' rimborsare a gennaio una
    # spesa di novembre, e il netting va applicato dove il budget era stato
    # registrato). Una sola query con self-join: l'originale deve essere
    # budgeable (altrimenti non era nel `per_mese`); il rimborso e' solo
    # REAL_MOVEMENT perche' in creazione gli si toglie `counts_in_budget`.
    if budget_type in {"Income", "Expenses"}:
        Originale = aliased(Transaction, name="originale")
        rimborsi = session.execute(select(
            extract("year", Originale.effective_on), extract("month", Originale.effective_on),
            Originale.category_id, Transaction.amount,
        ).join(Originale, Transaction.refund_of_id == Originale.id).where(
            REAL_MOVEMENT,
            Transaction.refund_of_id.is_not(None),
            Originale.counts_in_budget.is_(True),
            Originale.transaction_type == budget_type,
            Originale.effective_on >= inizio, Originale.effective_on <= fine,
        )).all()
        for anno, mese, categoria_originale, importo in rimborsi:
            if categoria_originale is not None:
                per_mese[(int(anno), int(mese))][categoria_originale] -= float(importo or 0)
    return {chiave: {categoria: round(valore, 2) for categoria, valore in valori.items()}
            for chiave, valori in per_mese.items()}


@router.get("/api/budgets")
def budgets(year: int, month: int, budget_type: str = "Expenses", session: Session = Depends(get_session)) -> dict[str, Any]:
    # In ordine d'albero, non d'alfabeto: la griglia del budget deve leggersi
    # come la tendina delle categorie, con i figli sotto il loro padre.
    plans = session.scalars(select(BudgetPlan).outerjoin(Category, Category.id == BudgetPlan.category_id)
                            .where(BudgetPlan.period == date(year, month, 1),
                                   BudgetPlan.budget_type == budget_type)
                            .order_by(Category.position, Category.name, BudgetPlan.id)).all()
    actual = budget_actual(session, year, month, budget_type)
    nomi = nomi_categorie(session)
    gruppi = gruppo_di_categoria(session)
    avanzi = previous_month_leftover(session, year, month, budget_type)
    items = [{"id": plan.id, "category": nomi.get(plan.category_id, ""), "categoryId": plan.category_id,
              "categoryLabel": nomi.get(plan.category_id, ""),
              "categoryGroup": gruppi.get(plan.category_id),
              "amount": num(plan.amount), "actual": actual.get(plan.category_id, 0),
              # Solo informativo: non entra in nessun totale.
              "previousLeftover": avanzi.get(plan.category_id, 0.0)}
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
    plans = session.scalars(select(BudgetPlan).outerjoin(Category, Category.id == BudgetPlan.category_id)
                            .where(extract("year", BudgetPlan.period) == year,
                                   BudgetPlan.budget_type == budget_type)
                            .order_by(Category.position, Category.name, BudgetPlan.id)).all()
    by_category: dict[int | None, list[BudgetPlan]] = defaultdict(list)
    for plan in plans: by_category[plan.category_id].append(plan)
    nomi = nomi_categorie(session)
    gruppi = gruppo_di_categoria(session)
    items = []
    speso = budget_actual_year(session, year, budget_type)
    for category_id, rows in by_category.items():
        indexed = {row.period.month: row for row in rows}; months = []
        for month in range(1, 13):
            row = indexed.get(month); actual = speso.get(month, {}).get(category_id, 0); months.append({"month": month, "id": row.id if row else None, "amount": num(row.amount) if row else 0, "actual": actual})
        items.append({"category": nomi.get(category_id, ""), "categoryId": category_id,
                      "categoryLabel": nomi.get(category_id, ""),
                      "categoryGroup": gruppi.get(category_id), "months": months,
                      "plannedTotal": sum(value["amount"] for value in months),
                      "actualTotal": sum(value["actual"] for value in months)})
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
    tappe = _tappe_per_obiettivo(session, [g.id for g in elenco])
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
                      "milestones": [_tappa_json(tappa, current, iniziale, goal.start_date, today)
                                     for tappa in tappe.get(goal.id, [])],
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


# Tappe di un obiettivo: un obiettivo piu' piccolo dentro quello grande
# ---------------------------------------------------------------------------
#
# Una tappa non e' un obiettivo a se': e' un punto sulla strada dello stesso
# obiettivo, con lo stesso punto di partenza e la stessa unita' di misura. Lo
# stato lo calcola `_stato_goal`, la stessa funzione dello stato
# dell'obiettivo: due criteri diversi per la stessa domanda finirebbero per
# dire "in ritardo" dove l'obiettivo dice "in linea", ed e' l'errore che la
# docstring di `_leva` in fire_routes racconta gia' una volta.


class GoalMilestonePayload(BaseModel):
    """Il nome e l'importo ci sono sempre; la data e' facoltativa."""

    name: str
    target_amount: Decimal
    target_date: date | None = None


def _tappe_per_obiettivo(session: Session, goal_ids: list[int]) -> dict[int, list[GoalMilestone]]:
    """Le tappe di tutti gli obiettivi, in una interrogazione sola.

    In ordine di importo, non di inserimento: la strada si legge dal primo
    traguardo all'ultimo, e una tappa aggiunta dopo le altre ma piu' vicina
    della meta' deve stare al suo posto, non in fondo.
    """
    if not goal_ids:
        return {}
    righe = session.scalars(select(GoalMilestone).where(GoalMilestone.goal_id.in_(goal_ids))
                            .order_by(GoalMilestone.target_amount, GoalMilestone.id)).all()
    per_obiettivo: dict[int, list[GoalMilestone]] = defaultdict(list)
    for riga in righe:
        per_obiettivo[riga.goal_id].append(riga)
    return per_obiettivo


def _tappa_json(tappa: GoalMilestone, corrente: float, iniziale: float,
                inizio: date | None, oggi: date) -> dict[str, Any]:
    """Una tappa con il suo stato, calcolato come quello dell'obiettivo.

    Una tappa raggiunta e' "completed" anche senza date: non c'e' niente da
    confrontare col tempo, ma il traguardo e' passato, e quello si vede.
    """
    traguardo = num(tappa.target_amount)
    return {"id": tappa.id, "name": tappa.name, "targetAmount": traguardo,
            "targetDate": tappa.target_date.isoformat() if tappa.target_date else None,
            **_stato_goal(corrente, iniziale, traguardo, inizio, tappa.target_date,
                          corrente >= traguardo, oggi)}


def _importo_tappa(valore: Decimal) -> Decimal:
    """L'importo di una tappa ai centesimi, o un rifiuto.

    Stessi limiti di `_to_decimal` in main.py, ma scritti qui: la' il valore
    arriva come float dal corpo JSON, e importare quella funzione chiuderebbe
    il cerchio degli import fra i due moduli.
    """
    if (valore is None or not valore.is_finite()
            or valore < 0 or valore >= Decimal("100000000000000")):
        raise HTTPException(status_code=422, detail="statementInvalidAmount")
    return valore.quantize(Decimal("0.01"))


def _valida_tappa(payload: GoalMilestonePayload, obiettivo: Goal, session: Session) -> Decimal:
    """Le regole di una tappa, prima di scriverla.

    Una tappa piu' grande dell'obiettivo, o con una data oltre la sua scadenza,
    non e' una tappa: e' un secondo obiettivo travestito, e dirlo subito e'
    meglio che lasciarlo scoprire dal grafico.
    """
    nome = (payload.name or "").strip()
    if not nome:
        raise HTTPException(status_code=422, detail="milestoneNameRequired")
    importo = _importo_tappa(payload.target_amount)
    if importo > Decimal(str(obiettivo.target_amount)):
        raise HTTPException(status_code=422, detail="milestoneAboveGoal")
    if payload.target_date and obiettivo.target_date and payload.target_date > obiettivo.target_date:
        raise HTTPException(status_code=422, detail="milestoneAfterGoal")
    # Il vincolo sulla tabella resta come rete per due salvataggi simultanei;
    # questo lo anticipa per rispondere con un codice invece che con un errore
    # di integrita'.
    doppione = session.scalar(select(GoalMilestone.id).where(GoalMilestone.goal_id == obiettivo.id,
                                                            GoalMilestone.name == nome))
    if doppione is not None:
        raise HTTPException(status_code=422, detail="milestoneDuplicate")
    return importo


def _tappa_creata(session: Session, obiettivo: Goal, tappa: GoalMilestone, oggi: date) -> dict[str, Any]:
    """La tappa appena scritta, nella stessa forma in cui esce dall'elenco.

    Due forme per la stessa entita' costringono chi legge a sapere da quale
    rotta e' arrivata: qui costa tre righe e la forma resta una sola.
    """
    firmati = _movimenti_goal(session, obiettivo)
    corrente = _valore_corrente_goal(session, obiettivo,
                                     num(sum((importo for _, importo in firmati), Decimal("0"))), oggi)
    return _tappa_json(tappa, corrente, num(obiettivo.starting_amount), obiettivo.start_date, oggi)


@router.post("/api/goals/{goal_id}/milestones", status_code=201)
def create_goal_milestone(goal_id: int, payload: GoalMilestonePayload,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    """Aggiunge una tappa a un obiettivo."""
    obiettivo = session.get(Goal, goal_id)
    if obiettivo is None:
        raise HTTPException(status_code=404, detail="goalNotFound")
    riga = GoalMilestone(goal_id=obiettivo.id, name=payload.name.strip(),
                         target_amount=_valida_tappa(payload, obiettivo, session),
                         target_date=payload.target_date)
    session.add(riga)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="milestoneDuplicate") from error
    return _tappa_creata(session, obiettivo, riga, date.today())


@router.delete("/api/goals/{goal_id}/milestones/{milestone_id}")
def delete_goal_milestone(goal_id: int, milestone_id: int,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    """Toglie una tappa. L'obiettivo e le sue altre tappe restano dove sono."""
    riga = session.get(GoalMilestone, milestone_id)
    if riga is None or riga.goal_id != goal_id:
        # Una tappa di un altro obiettivo non si cancella passando di qui: il
        # numero da solo non basta a dire che quella tappa e' di questo goal.
        raise HTTPException(status_code=404, detail="milestoneNotFound")
    session.delete(riga)
    session.commit()
    return {"success": True}



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
            azione = (row.transaction_type or "").strip().lower()
            if azione in {"split", "frazionamento"}:
                # Un frazionamento non muove niente: moltiplica le quote e
                # divide il prezzo. Trattarlo come una vendita toglierebbe dalla
                # posizione il rapporto (un 2:1 toglierebbe due quote) e il
                # valore crollerebbe per un'operazione che non ha spostato un
                # euro.
                holding["units"] *= units
                # Il prezzo segue le quote, come dice la riga sopra: senza, uno
                # strumento senza quotazione raddoppia di valore e il rendimento
                # se lo porta dietro.
                holding["last_price"] /= units
                continue
            if azione in {"buy", "acquisto"}:
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
        # Se almeno uno strumento posseduto ripiega sull'ultimo prezzo di
        # operazione, questo mese e' una stima. Il valore resta - la serie non
        # deve avere buchi - ma il rendimento che ci si costruisce sopra no:
        # vedi `_portfolio_returns`.
        quoted = True
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
                quoted = False
                market_value += holding["units"] * holding["last_price"]
                continue
            price, currency = quote
            if currency and currency.upper() != BASE_CURRENCY:
                fx = _price_on(series.get(f"{BASE_CURRENCY}{currency.upper()}=X", []), month_end)
                if fx and fx[0] > 0:
                    price = price / fx[0]
                else:
                    quoted = False
                    market_value += holding["units"] * holding["last_price"]
                    continue
            market_value += holding["units"] * price

        timeline.append({
            "period": cursor.isoformat(),
            "marketValue": num(market_value),
            "investedCapital": num(invested),
            "quoted": quoted,
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
        # Solo acquisti e vendite, per la stessa ragione di sopra: un dividendo
        # incassato e' reddito, e contarlo qui gonfierebbe i versamenti.
        if row.transaction_type not in ("Buy", "Sell") or not row.occurred_on:
            continue
        key = f"{row.occurred_on.year}-{row.occurred_on.month:02d}"
        monthly[key] += num(row.amount) if row.transaction_type == "Buy" else -num(row.amount)
    return [{"period": key, "label": f"{MONTHS[int(key[5:]) - 1]} {key[2:4]}", "amount": round(value, 2)}
            for key, value in sorted(monthly.items())]


def _portfolio_flows(session: Session) -> list[Flusso]:
    """Denaro che entra ed esce dal portafoglio, datato.

    Acquisti in entrata, vendite in uscita, tutto il resto fuori. La tabella
    delle operazioni dice il contrario - li' versamenti e prelievi verso il
    broker sono i flussi e gli acquisti no - e non e' una svista: quella regola
    presuppone che la liquidita' ferma sul conto sia valorizzata, mentre qui la
    serie valorizza solo gli strumenti. Con quella regola un primo mese che
    compra e basta avrebbe valore iniziale zero e rendimento assurdo, e ogni
    vendita sembrerebbe una perdita. Il confine giusto, per come e' misurata la
    serie, e' fra dentro e fuori il portafoglio: comprare porta denaro dentro,
    vendere lo porta fuori, e il denaro nuovo versato sul conto entra comprando.

    ponytail: un dividendo incassato e lasciato sul conto non si vede, in
    entrata ne' in uscita, e finche' la serie non comprendera' anche la
    liquidita' nemmeno si vedra' nel rendimento. Le commissioni stanno dalla
    stessa parte: abbassano il valore e basta, che e' gia' il loro effetto.
    """
    flussi: list[Flusso] = []
    for row in session.scalars(select(InvestmentTransaction)).all():
        if not row.occurred_on or not row.amount:
            continue
        azione = (row.transaction_type or "").strip().lower()
        if azione in {"buy", "acquisto"}:
            flussi.append(Flusso(row.occurred_on, abs(Decimal(str(row.amount)))))
        elif azione in {"sell", "vendita"}:
            flussi.append(Flusso(row.occurred_on, -abs(Decimal(str(row.amount)))))
    return flussi


def _rendimento_dict(esito: Rendimento) -> dict[str, Any]:
    """Un rendimento come lo legge l'interfaccia: il valore, o il motivo.

    `num` non si usa qui: arrotonda ai centesimi, e un rendimento del 7,34% e'
    0,0734. Il motivo viaggia come codice, la frase la sceglie chi traduce.
    """
    return {"value": float(esito.valore) if esito.valore is not None else None,
            "reason": esito.motivo}


def _month_end_of(period: str) -> date:
    """L'ultimo giorno del mese a cui appartiene un inizio periodo della serie."""
    inizio = date.fromisoformat(period)
    return (date(inizio.year + 1, 1, 1) if inizio.month == 12
            else date(inizio.year, inizio.month + 1, 1)) - timedelta(days=1)


def _valutazioni(timeline: list[dict[str, Any]]) -> list[Valutazione]:
    """Le chiusure di fine mese della serie, come le vuole il calcolo.

    Le date sono quelle vere di fine mese, non il primo del mese che il punto
    della serie porta come etichetta: un periodo e' il tempo fra due chiusure, e
    usare il primo del mese sposterebbe ogni flusso di qualche giorno, quel
    tanto che basta perche' nessuno se ne accorga.

    Un mese senza quotazioni vere vale `None` e non zero: il rendimento esce
    come motivo, invece di descrivere come guadagno un mese che non e' stato
    misurato.
    """
    oggi = date.today()
    return [Valutazione(min(_month_end_of(punto["period"]), oggi),
                        Decimal(str(punto["marketValue"])) if punto.get("quoted", True) else None)
            for punto in timeline]


def _portfolio_returns(session: Session, timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Il rendimento del portafoglio: TWR concatenato e XIRR sui flussi."""
    valutazioni = _valutazioni(timeline)
    flussi = _portfolio_flows(session)
    # Senza nemmeno un mese di storia il rendimento non esiste, ma XIRR ha
    # bisogno di una data finale: gli si passa una valutazione non calcolabile,
    # cosi' risponde "prezzo mancante" invece di rompersi.
    ultima = valutazioni[-1] if valutazioni else Valutazione(date.today(), None)
    return {
        "twr": _rendimento_dict(twr(valutazioni, flussi)),
        "xirr": _rendimento_dict(xirr(flussi, ultima)),
        "months": max(len(valutazioni) - 1, 0),
        "since": valutazioni[0].giorno.isoformat() if valutazioni else None,
        "asOf": ultima.giorno.isoformat() if valutazioni else None,
    }


BENCHMARK_KEY = "benchmark_symbol"


def benchmark_symbol(session: Session) -> str:
    """Il simbolo del metro di paragone, se l'utente ne ha scelto uno.

    Sta in `app_settings`, dove la tabella esiste apposta: una colonna nuova per
    un valore che si cambia dalla pagina sarebbe una migrazione per niente.
    """
    valore = session.scalar(select(AppSetting.value).where(AppSetting.key == BENCHMARK_KEY))
    return (valore or "").strip().upper()


def _benchmark_curves(session: Session, timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Le due curve riportate a 100, mese per mese: portafoglio contro indice.

    Il portafoglio riceve versamenti e l'indice no, quindi i guadagni in euro
    non si possono confrontare: si confrontano i rendimenti concatenati, e la
    curva del portafoglio e' quella del TWR. Le due partono dallo stesso 100 nel
    primo mese in comune - un indice scelto l'anno scorso ha meno mesi del
    portafoglio - e quando i mesi in comune sono meno di due non c'e' confronto:
    una curva di un punto non e' una curva, e riempire i buchi inventerebbe un
    rendimento che non c'e' stato.

    ponytail: il confronto resta nella valuta dell'indice. Per un indice quotato
    in dollari include quindi anche il cambio, che e' la cosa che si vuole
    guardare il giorno in cui due curve divergono senza spiegazione; convertirlo
    mese per mese sarebbe un altro percorso verso la rete per una differenza che
    si dichiara meglio a parole.
    """
    simbolo = benchmark_symbol(session)
    vuoto: dict[str, Any] = {"symbol": simbolo or None, "from": None, "months": 0,
                             "portfolio": {}, "index": {}}
    if not simbolo:
        return vuoto
    prezzi = _price_series(session).get(simbolo, [])
    # La catena segue la serie in ordine di mese: la timeline nasce cosi', e le
    # due liste restano allineate indice per indice.
    fattori = catena(_valutazioni(timeline), _portfolio_flows(session))
    if not prezzi or not fattori:
        return vuoto
    # Solo le chiusure del mese che si sta confrontando: un indice che finisce a
    # giugno non dice niente su luglio, e ripetere l'ultima quotazione
    # disegnerebbe una riga piatta per mesi in cui non e' stato misurato niente.
    chiusure: dict[tuple[int, int], Decimal] = {}
    for osservato, prezzo, _ in prezzi:
        chiusure[(osservato.year, osservato.month)] = prezzo
    comuni = []
    for punto, fattore in zip(timeline, fattori):
        inizio = date.fromisoformat(punto["period"])
        prezzo = chiusure.get((inizio.year, inizio.month))
        if prezzo is not None:
            comuni.append((punto["period"], fattore, prezzo))
    if len(comuni) < 2:
        return vuoto
    portafoglio = da_cento([fattore for _, fattore, _ in comuni])
    indice = da_cento([prezzo for _, _, prezzo in comuni])
    return {"symbol": simbolo, "from": comuni[0][0], "months": len(comuni),
            "portfolio": {periodo: float(valore) for (periodo, _, _), valore in zip(comuni, portafoglio)},
            "index": {periodo: float(valore) for (periodo, _, _), valore in zip(comuni, indice)}}


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
            # Dividendi e interessi incassati, e commissioni pagate: restano
            # fuori dal guadagno, che e' la differenza fra quanto vale e quanto
            # e' costato, cosi' si vede separato quanto ha reso lo strumento e
            # quanto ha pagato in contanti.
            "incomeReceived": num(p["income_received"]),
            "feesPaid": num(p["fees_paid"]),
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
    # Il riequilibrio si ricava dalle posizioni appena costruite, non da una
    # seconda lettura: due elenchi calcolati a parte prima o poi divergono, e la
    # pagina mostrerebbe pesi che non corrispondono alla tabella sopra.
    riordino = riequilibrio([
        PosizionePeso(nome=voce["name"], valore=Decimal(str(voce["marketValue"])),
                      obiettivo=None if voce["targetWeight"] is None else Decimal(str(voce["targetWeight"])),
                      aperta=voce["isOpen"])
        for voce in position_items])
    rendimento = _portfolio_returns(session, history)
    confronto = _benchmark_curves(session, history)
    return {"snapshot": {"period": latest["period"] if latest else None, "marketValue": market, "investedCapital": invested, "gain": gain, "returnRate": round(gain/invested*100, 2) if invested else 0}, "ledger": {"marketValue": market, "costBasis": invested, "gain": gain, "quotedPositions": quoted, "activePositions": len(positions)}, "positions": position_items, "history": [{"period": row["period"], "label": f"{MONTHS[date.fromisoformat(row['period']).month-1]} {date.fromisoformat(row['period']).year}", "marketValue": row["marketValue"], "investedCapital": row["investedCapital"], "gain": round(row["marketValue"] - row["investedCapital"], 2),
        # Rendimento in percentuale: distingue "sta rendendo" da "ho versato di piu'".
        "returnRate": round((row["marketValue"] / row["investedCapital"] - 1) * 100, 2) if row["investedCapital"] else None,
        # Le due curve del confronto, a 100 nel primo mese in comune. Nulle
        # fuori da li' e nulle del tutto quando un indice non e' configurato:
        # il grafico non deve disegnare una linea che non esiste.
        "twrCurve": confronto["portfolio"].get(row["period"]),
        "benchmarkCurve": confronto["index"].get(row["period"])}
        for row in history], "contributions": contributions,
        # Gli importi sono firmati: positivo vuol dire sopra il peso obiettivo,
        # cioe' da vendere. La pagina decide il colore, non riceve una decisione.
        "rebalance": {
            "total": float(riordino.totale),
            "declaredWeight": float(riordino.pesi_dichiarati),
            "warnings": list(riordino.avvisi),
            "rows": [{"name": r.nome, "currentWeight": float(r.peso_attuale),
                      "targetWeight": float(r.obiettivo), "drift": float(r.deriva),
                      "amount": float(r.importo)} for r in riordino.righe],
        },
        # Quanto ha reso il portafoglio, non quanto e' cresciuto: il guadagno in
        # euro sopra e questo numero raccontano due cose diverse, e su un
        # portafoglio che riceve versamenti il primo si muove anche quando il
        # mercato sta fermo.
        "returns": rendimento,
        # Contro cosa si sta confrontando: il simbolo, e da quando. La serie
        # mese per mese sta nei punti dello storico, cosi' il grafico legge una
        # lista sola.
        "benchmark": {"symbol": confronto["symbol"], "from": confronto["from"],
                      "months": confronto["months"]}}


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
        # Uno split non aggiunge ne' toglie quote: le moltiplica. Trattarlo
        # come una vendita toglierebbe dalla posizione un numero di quote pari
        # al rapporto, che non vuol dire niente: 2:1 non toglie due quote.
        if row.transaction_type == "Split" and abs_units > 0:
            running_by_name[row.name] = running_by_name.get(row.name, Decimal("0")) * abs_units
        else:
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
    # Il nome resta per l'interfaccia di oggi, che manda ancora quello; l'id, se
    # arriva, vince: e' l'unico che distingue due figli omonimi sotto padri
    # diversi.
    category: str = ""
    categoryId: int | None = None
    # I tipi che hanno una categoria. Uno spostamento fra conti non ce l'ha, e
    # gli altri tipi li rifiuta gia' il modello prima di arrivare qui.
    transaction_type: Literal["Expenses", "Income"] | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    active: bool = True


class RuleOrderPayload(BaseModel):
    ids: list[int]


class RuleBulkPayload(BaseModel):
    # Le stesse regole del modulo di inserimento, una per proposta spuntata:
    # passano dalla stessa validazione, quindi qui non entra niente che il
    # modulo rifiuterebbe.
    rules: list[RulePayload]


def _regola_json(riga: CategorizationRule, nomi: dict[int, str]) -> dict[str, Any]:
    """La regola come la vuole l'interfaccia: il nome per leggerla, l'id per
    riscriverla. Il nome e' quello di adesso, non quello di quando e' stata
    creata: una categoria rinominata non deve lasciare indietro le regole."""
    return {"id": riga.id, "position": riga.position, "pattern": riga.pattern, "isRegex": riga.is_regex,
            "category": nomi.get(riga.category_id, ""), "categoryId": riga.category_id,
            "transactionType": riga.transaction_type,
            "minAmount": num(riga.min_amount) if riga.min_amount is not None else None,
            "maxAmount": num(riga.max_amount) if riga.max_amount is not None else None,
            "active": riga.active}


def _importo(valore: float | None) -> Decimal | None:
    return None if valore is None else Decimal(str(valore)).quantize(Decimal("0.01"))


def _categoria_regola(session: Session, category_id: int | None, nome: str) -> int:
    """L'id della categoria che una regola decide, senza inventarne una.

    Il nome deve essere di una categoria che esiste: una regola che nomina una
    categoria mai vista scriverebbe nei movimenti una categoria che non sta in
    nessun elenco, e chi la trova non sa da dove sia uscita. Per questo non si
    crea, al contrario dell'import di un estratto conto, che un nome lo accetta
    perche' arriva da un file scritto prima che le categorie fossero un albero.
    """
    if category_id is not None:
        if session.get(Category, category_id) is None:
            raise HTTPException(status_code=404, detail="categoryNotFound")
        return category_id
    pulito = (nome or "").strip()
    trovate = session.scalars(select(Category.id).where(func.lower(Category.name) == pulito.casefold())).all()
    if not trovate:
        raise HTTPException(status_code=422, detail="ruleCategoryUnknown")
    if len(trovate) > 1:
        # Due figli omonimi sotto padri diversi: il nome da solo non dice quale
        # dei due, e indovinare scriverebbe la regola sulla categoria sbagliata.
        raise HTTPException(status_code=422, detail="ruleCategoryAmbiguous")
    return trovate[0]


def _valida_regola(payload: RulePayload, session: Session, *,
                   esistente: CategorizationRule | None = None) -> int:
    """Rifiuta una regola che l'anteprima non saprebbe applicare, e dice su
    quale categoria e' stata scritta.

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
    categoria_id = _categoria_regola(session, payload.categoryId, payload.category)
    minimo, massimo = _importo(payload.min_amount), _importo(payload.max_amount)
    if minimo is not None and massimo is not None and minimo > massimo:
        raise HTTPException(status_code=422, detail="ruleAmountRange")
    # Il vincolo sulla tabella non basta: in Postgres due NULL sono distinti, e
    # un tipo nullo vuol dire "spese ed entrate". Qui lo si confronta come un
    # valore, cosi' il doppione si vede anche quando il tipo non c'e'. Il
    # vincolo resta come rete per due salvataggi simultanei.
    condizione = [CategorizationRule.pattern == pattern,
                  CategorizationRule.transaction_type.is_(None) if payload.transaction_type is None
                  else CategorizationRule.transaction_type == payload.transaction_type]
    if esistente is not None:
        condizione.append(CategorizationRule.id != esistente.id)
    if session.scalar(select(CategorizationRule.id).where(*condizione)) is not None:
        raise HTTPException(status_code=422, detail="ruleDuplicate")
    if esistente is None:
        quante = session.scalar(select(func.count(CategorizationRule.id))) or 0
        if quante >= MAX_REGOLE:
            raise HTTPException(status_code=422, detail="ruleLimitReached")
    return categoria_id


@router.get("/api/categorization-rules")
def categorization_rules(session: Session = Depends(get_session)) -> dict[str, Any]:
    righe = session.scalars(select(CategorizationRule)
                            .order_by(CategorizationRule.position, CategorizationRule.id)).all()
    nomi = nomi_categorie(session)
    return {"items": [_regola_json(riga, nomi) for riga in righe]}


@router.post("/api/categorization-rules", status_code=201)
def create_categorization_rule(payload: RulePayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Crea una regola in coda alle altre: l'ordine e' la priorita'."""
    categoria_id = _valida_regola(payload, session)
    ultima = session.scalar(select(func.max(CategorizationRule.position))) or 0
    riga = CategorizationRule(position=ultima + 1, pattern=payload.pattern.strip(), is_regex=payload.is_regex,
                              category_id=categoria_id, transaction_type=payload.transaction_type,
                              min_amount=_importo(payload.min_amount), max_amount=_importo(payload.max_amount),
                              active=payload.active)
    session.add(riga)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="ruleDuplicate") from error
    return _regola_json(riga, nomi_categorie(session))


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


@router.post("/api/categorization-rules/suggest")
def suggest_categorization_rules(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Le regole che si possono imparare dai movimenti gia' registrati.

    Non scrive niente: le proposte diventano regole solo dopo la spunta.
    """
    return suggest(session)


@router.post("/api/categorization-rules/bulk", status_code=201)
def create_categorization_rules(payload: RuleBulkPayload,
                                session: Session = Depends(get_session)) -> dict[str, Any]:
    """Crea le proposte spuntate, in coda alle regole che ci sono gia'.

    Una proposta non spuntata non arriva qui: l'accettazione e' la spunta, non
    il pulsante. Il tetto si controlla sul totale, non regola per regola: con
    tre posti liberi un lotto da cinque passerebbe cinque volte il controllo e
    scriverebbe due regole di troppo.
    """
    quante = session.scalar(select(func.count(CategorizationRule.id))) or 0
    if quante + len(payload.rules) > MAX_REGOLE:
        raise HTTPException(status_code=422, detail="ruleLimitReached")
    posizione = session.scalar(select(func.max(CategorizationRule.position))) or 0
    try:
        for indice, regola in enumerate(payload.rules, start=1):
            categoria_id = _valida_regola(regola, session)
            riga = CategorizationRule(position=posizione + indice, pattern=regola.pattern.strip(),
                                      is_regex=False, category_id=categoria_id,
                                      transaction_type=regola.transaction_type,
                                      min_amount=_importo(regola.min_amount), max_amount=_importo(regola.max_amount),
                                      active=regola.active)
            session.add(riga)
            # Senza questo, due proposte uguali dentro lo stesso lotto non si
            # vedrebbero fra loro e passerebbero entrambe.
            session.flush()
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="ruleDuplicate") from error
    session.commit()
    return categorization_rules(session)


@router.put("/api/categorization-rules/{rule_id}")
def update_categorization_rule(rule_id: int, payload: RulePayload,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = session.get(CategorizationRule, rule_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="ruleNotFound")
    categoria_id = _valida_regola(payload, session, esistente=riga)
    riga.pattern, riga.is_regex = payload.pattern.strip(), payload.is_regex
    riga.category_id, riga.transaction_type = categoria_id, payload.transaction_type
    riga.min_amount, riga.max_amount = _importo(payload.min_amount), _importo(payload.max_amount)
    riga.active = payload.active
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail="ruleDuplicate") from error
    return _regola_json(riga, nomi_categorie(session))


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
