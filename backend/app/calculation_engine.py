from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Iterable


ZERO = Decimal("0")
CENT = Decimal("0.01")


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def effective_date(
    occurred_on: date,
    transaction_type: str,
    late_income_shift: str = "Inactive",
    late_income_day: int = 20,
) -> date:
    if transaction_type.casefold() != "income" or late_income_shift.casefold() != "active" or occurred_on.day < late_income_day:
        return occurred_on
    if occurred_on.month == 12:
        return date(occurred_on.year + 1, 1, 1)
    return date(occurred_on.year, occurred_on.month + 1, 1)


def source_effect(transaction_type: str, amount: Any) -> Decimal:
    value = money(amount)
    return value if transaction_type.casefold() == "income" else -value


def normalized_name(value: Any) -> str:
    # Excel text comparisons used by SUMPRODUCT are case-insensitive.
    return str(value or "").strip().casefold()


def _movement_amounts(transaction: Any) -> tuple[Decimal, Decimal]:
    """Un giroconto muove lo stesso importo su entrambi i conti."""
    amount = money(_value(transaction, "amount"))
    return amount, amount


def calculate_account_balance(starting_balance: Any, account_name: str, transactions: Iterable[Any]) -> Decimal:
    balance = money(starting_balance)
    normalized_account = normalized_name(account_name)
    for transaction in transactions:
        source_amount, destination_amount = _movement_amounts(transaction)
        if normalized_name(_value(transaction, "account_name")) == normalized_account:
            balance += source_effect(_value(transaction, "transaction_type", ""), source_amount)
        if normalized_name(_value(transaction, "destination_name")) == normalized_account:
            balance += destination_amount
    return balance.quantize(Decimal("0.01"))


def account_reconciliation(accounts: Iterable[Any], transactions: Iterable[Any]) -> list[dict[str, Any]]:
    """Saldo calcolato e differenza rispetto al saldo dichiarato, per ogni conto.

    Una passata sola sui movimenti, sommati per nome di conto, invece di una
    scansione completa per ogni conto: con quaranta conti e quattromila
    movimenti la differenza fra le due cose e' centosettantamila giri di ciclo.
    Il risultato e' identico a ``calculate_account_balance`` conto per conto -
    la stessa somma, raccolta prima invece che dopo.
    """
    deltas: dict[str, Decimal] = {}
    for transaction in transactions:
        source_amount, destination_amount = _movement_amounts(transaction)
        source = normalized_name(_value(transaction, "account_name"))
        if source:
            deltas[source] = deltas.get(source, ZERO) + source_effect(
                _value(transaction, "transaction_type", ""), source_amount)
        destination = normalized_name(_value(transaction, "destination_name"))
        if destination:
            deltas[destination] = deltas.get(destination, ZERO) + destination_amount

    result = []
    for account in accounts:
        calculated = (money(_value(account, "starting_balance"))
                      + deltas.get(normalized_name(_value(account, "name", "")), ZERO)
                      ).quantize(Decimal("0.01"))
        imported = money(_value(account, "current_balance"))
        difference = (calculated - imported).quantize(Decimal("0.01"))
        result.append({
            "account": account,
            "calculated": calculated,
            "imported": imported,
            "difference": difference,
            "matched": difference == ZERO,
        })
    return result


def _transaction_effective_date(transaction: Any) -> date | None:
    effective = _value(transaction, "effective_on")
    if effective:
        return effective
    return _value(transaction, "occurred_on")


def _applica_movimento(tx: Any, by_name: dict[str, dict[str, Decimal]]) -> None:
    """Somma un movimento ai due lati che tocca: conto di partenza e destinazione."""
    tx_type = str(_value(tx, "transaction_type", "") or "").casefold()
    source_amount, destination_amount = _movement_amounts(tx)
    source_name = normalized_name(_value(tx, "account_name"))
    if source_name:
        entry = by_name.setdefault(source_name, {"source": ZERO, "destination": ZERO})
        entry["source"] += source_amount if tx_type == "income" else -source_amount
    destination_name = normalized_name(_value(tx, "destination_name"))
    if destination_name:
        entry = by_name.setdefault(destination_name, {"source": ZERO, "destination": ZERO})
        entry["destination"] += destination_amount


def valutazione_al(storico: Iterable[tuple[date, Any]] | None, cutoff: date | None) -> Decimal | None:
    """L'ultima valutazione non successiva al cutoff, se c'e'.

    Prima della prima valutazione non si inventa un valore: si torna None e il
    chiamante ricade sul calcolo normale. Retrodatare la stima di oggi
    riscriverebbe il patrimonio degli anni in cui quel valore non si conosceva.
    """
    if not storico or cutoff is None:
        return None
    valide = [valore for quando, valore in storico if quando <= cutoff]
    return money(valide[-1]) if valide else None


def _saldi_da_deltas(accounts: Iterable[Any], by_name: dict[str, dict[str, Decimal]],
                     cutoff: date | None = None,
                     valutazioni: dict[Any, list[tuple[date, Any]]] | None = None,
                     rivalutazioni: dict[Any, list[tuple[date, Any]]] | None = None) -> dict[str, Any]:
    totals: dict[str, Decimal] = {"bank": ZERO, "asset": ZERO, "liability": ZERO, "financial": ZERO}
    rows: list[dict[str, Any]] = []
    for account in accounts:
        name = normalized_name(_value(account, "name", ""))
        starting = money(_value(account, "starting_balance"))
        deltas = by_name.get(name, {"source": ZERO, "destination": ZERO})
        balance = (starting + deltas["source"] + deltas["destination"]).quantize(Decimal("0.01"))
        # Due canali, due operazioni diverse. Una stima a mano SOSTITUISCE il
        # saldo: una casa non vale i bonifici che ci sono passati sopra. Una
        # rivalutazione lo AGGIUSTA: il conto titoli il costo ce l'ha giusto, gli
        # manca solo quanto hanno guadagnato i titoli attribuiti a lui.
        # Chi ne abbia diritto lo ha gia' deciso chi ha costruito i due
        # dizionari: qui si applica e basta.
        if valutazioni is not None:
            stimato = valutazione_al(valutazioni.get(_value(account, "id")), cutoff)
            if stimato is not None:
                balance = stimato.quantize(Decimal("0.01"))
        if rivalutazioni is not None:
            guadagno = valutazione_al(rivalutazioni.get(_value(account, "id")), cutoff)
            if guadagno is not None:
                balance = (balance + guadagno).quantize(Decimal("0.01"))
        group = _value(account, "source_group", "bank")
        signed = -balance if group == "liability" else balance
        totals[group] = totals.get(group, ZERO) + signed
        rows.append({"account_id": _value(account, "id"), "name": _value(account, "name", ""), "group": group, "balance": float(signed)})
    return {"totals": {group: float(total) for group, total in totals.items()}, "accounts": rows}


def account_balances_at(accounts: Iterable[Any], transactions: Iterable[Any], cutoff: date,
                        valutazioni: dict[Any, list[tuple[date, Any]]] | None = None,
                        rivalutazioni: dict[Any, list[tuple[date, Any]]] | None = None) -> dict[str, Any]:
    """Saldo per ogni account + totali per gruppo al netto delle transazioni con
    effective_on <= cutoff. Le liability sono restituite col segno invertito
    (come in _net_worth_breakdown) così lato UI si sommano algebricamente.

    Restituisce: {
        "totals": {source_group: Decimal},
        "accounts": [{account_id, name, group, balance}],
    }
    """
    by_name: dict[str, dict[str, Decimal]] = {}
    for tx in transactions:
        effective = _transaction_effective_date(tx)
        if not effective or effective > cutoff:
            continue
        _applica_movimento(tx, by_name)
    return _saldi_da_deltas(accounts, by_name, cutoff, valutazioni, rivalutazioni)


def account_balances_series(accounts: Iterable[Any], transactions: Iterable[Any],
                            cutoffs: Iterable[date],
                            valutazioni: dict[Any, list[tuple[date, Any]]] | None = None,
                            rivalutazioni: dict[Any, list[tuple[date, Any]]] | None = None) -> list[dict[str, Any]]:
    """Gli stessi saldi di ``account_balances_at``, su piu' date, in una passata.

    Le date crescono, quindi i movimenti si applicano una volta sola e restano
    sommati per quelle successive. Ricalcolare da capo ogni mese vuol dire
    rileggere tutta la storia dodici volte, ed e' cio' che rendeva lenta la
    pagina Patrimonio. Le due funzioni condividono il calcolo, quindi non
    possono divergere: cambia solo quante volte si scorre l'elenco.
    """
    ordinate = sorted(enumerate(cutoffs), key=lambda coppia: coppia[1])
    datati = sorted(
        ((data, tx) for data, tx in ((_transaction_effective_date(tx), tx) for tx in transactions) if data),
        key=lambda coppia: coppia[0],
    )
    by_name: dict[str, dict[str, Decimal]] = {}
    indice = 0
    risultati: list[Any] = [None] * len(ordinate)
    for posizione, cutoff in ordinate:
        while indice < len(datati) and datati[indice][0] <= cutoff:
            _applica_movimento(datati[indice][1], by_name)
            indice += 1
        risultati[posizione] = _saldi_da_deltas(accounts, by_name, cutoff, valutazioni, rivalutazioni)
    return risultati


def savings_rate(income: Any, expenses: Any) -> Decimal | None:
    """Quota delle entrate che non se n'e' andata in spese.

    Il workbook offriva due formule - risparmio/entrate e (entrate-spese)/entrate -
    perche' il risparmio era un numero scritto a mano e poteva discostarsi da
    quello che davvero restava. Ora il risparmio e' calcolato come entrate meno
    spese, quindi le due formule sono la stessa cosa e ne resta una sola.
    """
    income_value = money(income)
    if income_value == ZERO:
        return None
    return ((income_value - money(expenses)) / income_value).quantize(Decimal("0.0001"))


def period_metrics(
    transactions: Iterable[Any],
    budgets: Iterable[Any],
    year: int,
    month: int,
    today: date | None = None,
) -> dict[str, Any]:
    # I movimenti: `Savings` non c'e' piu' (il risparmio si deriva da entrate
    # meno uscite), `Investment` e' un giroconto verso un broker e come i
    # giroconti non entra nel budget. La chiave Savings resta a zero perche' la
    # riga del budget dei risparmi la legge, e li' significa un'altra cosa.
    totals = {"Income": ZERO, "Expenses": ZERO, "Savings": ZERO, "Transfers": ZERO,
              "Investment": ZERO}
    rows = list(transactions)
    originals = {}
    for transaction in rows:
        if _value(transaction, "is_recurring_template", False) or _value(transaction, "counts_in_budget", True) is False:
            continue
        effective = _value(transaction, "effective_on") or _value(transaction, "occurred_on")
        if not effective or effective.year != year or effective.month != month:
            continue
        raw_type = str(_value(transaction, "transaction_type", ""))
        transaction_type = next((name for name in totals if name.casefold() == raw_type.casefold()), raw_type)
        if transaction_type in totals:
            totals[transaction_type] += money(_value(transaction, "amount"))
            if _value(transaction, "id") is not None:
                originals[_value(transaction, "id")] = transaction_type
    for transaction in rows:
        original_id = _value(transaction, "refund_of_id")
        original_type = originals.get(original_id)
        refund_type = str(_value(transaction, "transaction_type", ""))
        if original_type and {original_type, refund_type} == {"Income", "Expenses"}:
            totals[original_type] -= money(_value(transaction, "amount"))

    budget_totals = {"Income": ZERO, "Expenses": ZERO, "Savings": ZERO}
    for budget in budgets:
        period = _value(budget, "period")
        if not period or period.year != year or period.month != month:
            continue
        budget_type = _value(budget, "budget_type", "")
        if budget_type in budget_totals:
            budget_totals[budget_type] += money(_value(budget, "amount"))

    current = today or date.today()
    days_in_period = monthrange(year, month)[1]
    if (year, month) < (current.year, current.month):
        days_passed = days_in_period
    elif (year, month) > (current.year, current.month):
        days_passed = 0
    else:
        days_passed = min(current.day, days_in_period)

    tracking_balance = totals["Income"] - totals["Expenses"] - totals["Savings"]
    return {
        "income": totals["Income"].quantize(Decimal("0.01")),
        "expenses": totals["Expenses"].quantize(Decimal("0.01")),
        "savings": totals["Savings"].quantize(Decimal("0.01")),
        "transfers": totals["Transfers"].quantize(Decimal("0.01")),
        "tracking_balance": tracking_balance.quantize(Decimal("0.01")),
        "savings_rate": savings_rate(totals["Income"], totals["Expenses"]),
        "days_in_period": days_in_period,
        "days_passed": days_passed,
        "days_passed_ratio": (Decimal(days_passed) / Decimal(days_in_period)).quantize(Decimal("0.0001")),
        "budget": {key.lower(): value.quantize(Decimal("0.01")) for key, value in budget_totals.items()},
        "budget_delta": {
            key.lower(): (budget_totals[key] - totals[key]).quantize(Decimal("0.01"))
            for key in budget_totals
        },
    }


def investment_positions(
    transactions: Iterable[Any],
    fees_by_transaction: dict[int, Any] | None = None,
    prices_by_name: dict[str, Any] | None = None,
    tickers_by_name: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Average-cost portfolio ledger, independent of Excel table formulas.

    Buys increase units and cost basis. Sales reduce the basis at its average
    cost and recognize the difference as realized P/L.  This is deliberately
    kept separate from quotation retrieval, so stale/offline market data can
    never alter the transaction ledger.

    Le posizioni sono raggruppate per ticker quando lo strumento ne ha uno: il
    nome nel ledger puo' cambiare nel tempo (uno strumento rinominato a meta'
    storia verrebbe altrimenti spezzato in due, lasciando quote fantasma da una
    parte e una vendita senza copertura dall'altra).

    Oltre ad acquisti e vendite il ledger conosce i movimenti di solo contante
    (dividendi, commissioni, versamenti) e gli split. Il contante non tocca
    quote ne' costo: si somma a ``income_received`` o a ``fees_paid``, oppure
    muove ``net_contributed``, che e' il denaro versato e non il valore
    dell'investimento.
    """
    fee_map = fees_by_transaction or {}
    ticker_map = {normalized_name(key): value.strip().upper() for key, value in (tickers_by_name or {}).items() if value and value.strip()}
    price_map = {}
    for key, value in (prices_by_name or {}).items():
        price_map[normalized_name(key)] = money(value)
        ticker = ticker_map.get(normalized_name(key))
        if ticker:
            price_map[ticker.lower()] = money(value)
    positions: dict[str, dict[str, Any]] = {}
    ordered = sorted(transactions, key=lambda item: (_value(item, "occurred_on") or date.min, _value(item, "id") or 0))
    for row in ordered:
        name = str(_value(row, "name") or "Da classificare").strip()
        # Il ticker e' l'identita' dello strumento: prima quello della riga di
        # ledger (colonna D del foglio Ledger), poi quello configurato
        # nell'app, e solo in mancanza di entrambi si ripiega sul nome.
        row_ticker = _value(row, "ticker")
        ticker = (str(row_ticker).strip().upper() if row_ticker and str(row_ticker).strip() and not str(row_ticker).startswith("#") else None)
        if not ticker:
            ticker = ticker_map.get(normalized_name(name))
        key = ticker.lower() if ticker else normalized_name(name)
        units = abs(Decimal(str(_value(row, "units") or 0)))
        amount = abs(money(_value(row, "amount")))
        fee = money(fee_map.get(_value(row, "id")))
        action = normalized_name(_value(row, "transaction_type"))
        position = positions.get(key)
        if position is None:
            # Una riga di conto - gli interessi del broker, una commissione, un
            # versamento - tiene la sua posizione anche se con quel nome non e'
            # mai stato comprato niente: sono soldi, non quote, e la riga e' il
            # posto dove si vedono. Ma se un ticker c'e' la riga parla di uno
            # strumento, e uno strumento mai comprato non entra in tabella
            # (una posizione fantasma da zero quote non si chiude ne' si
            # cancella); uno split, che di strumenti parla sempre, nemmeno.
            if action not in {"buy", "acquisto", "sell", "vendita"} and (ticker or action in {"split", "frazionamento"}):
                continue
            position = positions[key] = {
                "name": name,
                "ticker": ticker,
                "units": ZERO,
                "cost_basis": ZERO,
                "net_contributed": ZERO,
                "realized_gain": ZERO,
                "income_received": ZERO,
                "fees_paid": ZERO,
                "last_trade_price": ZERO,
                "currency": _value(row, "currency") or "EUR",
            }
        if action in {"buy", "acquisto"}:
            # Il controllo sulle quote sta qui dentro e non prima: un movimento
            # di solo contante ha zero quote, e scartarlo in cima lo renderebbe
            # invisibile.
            if units == ZERO:
                continue
            position["units"] += units
            position["cost_basis"] += amount + fee
            position["net_contributed"] += amount + fee
        elif action in {"sell", "vendita"}:
            if units == ZERO:
                continue
            sold_units = min(units, position["units"])
            average_cost = position["cost_basis"] / position["units"] if position["units"] else ZERO
            disposed_cost = average_cost * sold_units
            proceeds = max(amount - fee, ZERO)
            position["units"] -= sold_units
            position["cost_basis"] -= disposed_cost
            position["net_contributed"] -= proceeds
            position["realized_gain"] += proceeds - disposed_cost
        elif action in {"dividend", "dividendo"}:
            # Un dividendo e' denaro incassato: non e' un acquisto, e il costo
            # dell'investimento resta quello che era.
            position["income_received"] += amount
        elif action in {"fee", "commissione"}:
            # Una commissione staccata dalla borsa: denaro uscito dal
            # portafoglio, stessa logica della commissione su una vendita.
            position["fees_paid"] += amount
            position["net_contributed"] -= amount
        elif action in {"deposit", "deposito", "versamento"}:
            position["net_contributed"] += amount
        elif action in {"withdrawal", "prelievo"}:
            position["net_contributed"] -= amount
        elif action in {"split", "frazionamento"}:
            # Il rapporto sta nelle quote: 2 = due nuove per una vecchia, 0,5 =
            # un raggruppamento. Il costo non cambia, quindi il prezzo medio
            # (costo diviso quote) si aggiusta da solo.
            if units <= ZERO:
                continue
            position["units"] *= units
        else:
            continue
        position["name"] = name
        # Solo un acquisto o una vendita dicono un prezzo: un dividendo non ha
        # un prezzo di scambio, e scriverlo qui falserebbe la valorizzazione
        # dell'ultima riga di ledger disponibile.
        if action in {"buy", "acquisto", "sell", "vendita"}:
            row_price = _value(row, "price")
            if row_price is not None:
                position["last_trade_price"] = money(row_price)

    result = []
    for key, position in positions.items():
        quoted_price = price_map.get(key)
        price = quoted_price if quoted_price is not None else position["last_trade_price"]
        market_value = (position["units"] * price).quantize(Decimal("0.01"))
        unrealized = market_value - position["cost_basis"]
        total_gain = position["realized_gain"] + unrealized
        basis = position["net_contributed"]
        result.append({
            **position,
            "units": position["units"].quantize(Decimal("0.00000001")),
            "cost_basis": position["cost_basis"].quantize(Decimal("0.01")),
            "net_contributed": position["net_contributed"].quantize(Decimal("0.01")),
            "realized_gain": position["realized_gain"].quantize(Decimal("0.01")),
            # I proventi si guardano separati dal guadagno di capitale: un
            # dividendo incassato non e' una vendita, e sommarlo al risultato
            # nasconderebbe quanto ha reso il portafoglio e quanto ha pagato.
            "income_received": position["income_received"].quantize(Decimal("0.01")),
            "fees_paid": position["fees_paid"].quantize(Decimal("0.01")),
            "price": price.quantize(Decimal("0.01")),
            "has_quote": quoted_price is not None,
            "market_value": market_value,
            "unrealized_gain": unrealized.quantize(Decimal("0.01")),
            "total_gain": total_gain.quantize(Decimal("0.01")),
            "return_rate": (total_gain / basis).quantize(Decimal("0.0001")) if basis > ZERO else None,
        })
    return sorted(result, key=lambda item: item["market_value"], reverse=True)


def cape_stock_weight(cape: Any, low_cape: Any, middle_cape: Any, high_cape: Any, low_weight: Any, middle_weight: Any, high_weight: Any) -> Decimal:
    """Piecewise-linear CAPE rule used for an auditable allocation tilt."""
    value = Decimal(str(cape))
    low, middle, high = (Decimal(str(item)) for item in (low_cape, middle_cape, high_cape))
    low_result, middle_result, high_result = (Decimal(str(item)) for item in (low_weight, middle_weight, high_weight))
    if value <= low:
        return low_result
    if value >= high:
        return high_result
    if value <= middle:
        return (low_result + (value - low) * (middle_result - low_result) / (middle - low)).quantize(Decimal("0.0001"))
    return (middle_result + (value - middle) * (high_result - middle_result) / (high - middle)).quantize(Decimal("0.0001"))


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _planned_drawdowns(principal: Any, start: date, drawdowns: Iterable[Any] | None) -> list[tuple[date, Decimal]]:
    if not drawdowns:
        return [(start, money(principal))]
    rows = []
    for row in drawdowns:
        raw_date = _value(row, "occurredOn", _value(row, "date"))
        when = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        rows.append((when, money(_value(row, "amount"))))
    return sorted(rows)


def _scadenze(da: date, a: date, mesi: int) -> list[date]:
    """Le scadenze da `da` (escluso) fino ad `a` (incluso)."""
    date_scadenza, prossima = [], _add_months(da, mesi)
    while prossima < a:
        date_scadenza.append(prossima)
        prossima = _add_months(prossima, mesi)
    date_scadenza.append(a)
    return date_scadenza


def _interessi_maturati(rate: Decimal, saldo: Decimal, da: date, a: date) -> Decimal:
    """Interessi semplici su un saldo fermo, giorni effettivi su 365.

    Una convenzione sola in tutto il piano. Prima la rata si calcolava col tasso
    periodale e gli interessi coi giorni effettivi: due convenzioni nello stesso
    piano non chiudono, e lo scarto finiva tutto sull'ultima rata - 376 euro su
    un prestito da quarantamila.
    """
    return saldo * rate * Decimal((a - da).days) / Decimal("365")


def stato_debito_registrato(drawdowns: Iterable[Any], repayments: Iterable[Any],
                            charges: Iterable[Any], cutoff: date) -> dict[str, Decimal]:
    """Debito reale: solo erogazioni, addebiti e rimborsi registrati."""
    def total(rows: Iterable[Any], field: str) -> Decimal:
        result = ZERO
        for row in rows:
            raw_date = _value(row, "occurredOn", _value(row, "date"))
            when = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
            if when <= cutoff:
                result += money(_value(row, field))
        return result

    drawn = total(drawdowns, "amount")
    repaid = total(repayments, "principal")
    charged = total(charges, "amount")
    interest_paid = total(repayments, "interest")
    principal_outstanding = max(ZERO, drawn - repaid)
    interest_outstanding = max(ZERO, charged - interest_paid)
    return {"drawnPrincipal": drawn.quantize(CENT),
            "repaidPrincipal": repaid.quantize(CENT),
            "principalOutstanding": principal_outstanding.quantize(CENT),
            "interestCharged": charged.quantize(CENT),
            "interestPaid": interest_paid.quantize(CENT),
            "interestOutstanding": interest_outstanding.quantize(CENT),
            "totalDebt": (principal_outstanding + interest_outstanding).quantize(CENT)}


def amortization_schedule(principal: Any, annual_rate: Any, start: date, end: date,
                          frequency: str = "monthly", structure: str = "amortizing",
                          drawdowns: Iterable[Any] | None = None,
                          repayment_start: date | None = None,
                          grace_interest: str = "paid") -> list[dict[str, Any]]:
    """Solo le rate. Per sapere perche' un piano non c'e', usa `piano_ammortamento`."""
    return piano_ammortamento(principal, annual_rate, start, end, frequency, structure,
                              drawdowns, repayment_start, grace_interest)[0]


def piano_ammortamento(principal: Any, annual_rate: Any, start: date, end: date,
                       frequency: str = "monthly", structure: str = "amortizing",
                       drawdowns: Iterable[Any] | None = None,
                       repayment_start: date | None = None,
                       grace_interest: str = "paid") -> tuple[list[dict[str, Any]], str | None]:
    """Piano teorico con erogazioni a tranche, preammortamento e quattro schemi.

    Restituisce `(rate, motivo)`. Il motivo e' `None` quando il piano c'e', e
    altrimenti dice **perche'** non c'e': prima ogni rifiuto tornava una lista
    vuota e basta, e la pagina restava bianca senza modo di capire cosa mancasse.
    """
    tranche = _planned_drawdowns(principal, start, drawdowns)
    capitale = sum((importo for _, importo in tranche), ZERO)
    mesi = {"monthly": 1, "quarterly": 3, "annual": 12}.get(frequency)
    if mesi is None:
        return [], "frequenzaSconosciuta"
    if structure not in {"amortizing", "constant_principal", "interest_only", "bullet"}:
        return [], "strutturaSconosciuta"
    if capitale <= ZERO or any(importo <= ZERO for _, importo in tranche):
        return [], "erogazioniNonValide"
    if end <= tranche[0][0]:
        return [], "scadenzaPrimaDellErogazione"

    rate = Decimal(str(annual_rate or 0)) / Decimal("100")
    if structure == "bullet":
        # Tutto alla scadenza, interessi compresi. `repayment_start` dice quando
        # cominciano le RATE, e un bullet non ne ha: farglielo usare come data
        # di pagamento anticiperebbe di anni ogni prestito che dichiara la fine
        # del preammortamento. Qui si ignora di proposito.
        interessi = sum((_interessi_maturati(rate, importo, allora, end)
                         for allora, importo in tranche), ZERO).quantize(CENT)
        return ([{"number": 1, "dueOn": end.isoformat(), "payment": float(capitale + interessi),
                  "principal": float(capitale), "interest": float(interessi), "remaining": 0.0}], None)

    repayment_start = repayment_start or start
    if repayment_start >= end:
        return [], "rimborsoDopoLaScadenza"
    if structure in {"amortizing", "constant_principal"} and any(q > repayment_start for q, _ in tranche):
        return [], "erogazioniDopoIlRimborso"

    # Scadenze di preammortamento e di rimborso. Prima le prime non esistevano:
    # tutti gli interessi maturati nel frattempo piombavano sulla prima rata -
    # 482 euro su 886 totali, nel caso che ha fatto trovare il difetto.
    preammortamento = _scadenze(tranche[0][0], repayment_start, mesi) if repayment_start > tranche[0][0] else []
    rimborso = _scadenze(repayment_start, end, mesi)
    capitalizza = grace_interest == "capitalised"

    piano: list[dict[str, Any]] = []
    residuo, cursore, indice = ZERO, tranche[0][0], 0

    def matura(fino_a: date) -> Decimal:
        """Interessi dal cursore a `fino_a`, incassando le tranche per strada."""
        nonlocal residuo, cursore, indice
        maturati = ZERO
        while indice < len(tranche) and tranche[indice][0] <= fino_a:
            quando, importo = tranche[indice]
            maturati += _interessi_maturati(rate, residuo, cursore, quando)
            residuo += importo
            cursore = quando
            indice += 1
        maturati += _interessi_maturati(rate, residuo, cursore, fino_a)
        cursore = fino_a
        return maturati

    for numero, scadenza in enumerate(preammortamento, 1):
        interessi = matura(scadenza).quantize(CENT)
        if capitalizza:
            residuo = (residuo + interessi).quantize(CENT)
        piano.append({"number": numero, "dueOn": scadenza.isoformat(),
                      "payment": 0.0 if capitalizza else float(interessi),
                      "principal": 0.0, "interest": float(interessi), "remaining": float(residuo)})

    # La rata costante si calcola sul debito che ci sara' all'inizio del
    # rimborso, non sul capitale nominale: col preammortamento capitalizzato i
    # due numeri sono diversi.
    periodi = len(rimborso)
    tasso_periodale = rate * Decimal(mesi) / Decimal("12")
    da_rimborsare = residuo + sum((importo for _, importo in tranche[indice:]), ZERO)
    rata = (da_rimborsare / periodi if tasso_periodale == ZERO else
            da_rimborsare * tasso_periodale / (Decimal("1") - (Decimal("1") + tasso_periodale) ** -periodi))

    ultima = len(piano) + len(rimborso)
    for numero, scadenza in enumerate(rimborso, len(piano) + 1):
        interessi = matura(scadenza).quantize(CENT)
        if numero == ultima:
            quota = residuo
        elif structure == "interest_only":
            quota = ZERO
        elif structure == "constant_principal":
            quota = min(residuo, da_rimborsare / periodi)
        else:
            quota = min(residuo, max(ZERO, rata - interessi))
        quota = quota.quantize(CENT)
        residuo = max(ZERO, residuo - quota).quantize(CENT)
        piano.append({"number": numero, "dueOn": scadenza.isoformat(),
                      "payment": float((quota + interessi).quantize(CENT)),
                      "principal": float(quota), "interest": float(interessi),
                      "remaining": float(residuo)})
    return piano, None


def debito_pianificato_al(schedule: Iterable[Any], drawdowns: Iterable[Any],
                          annual_rate: Any, cutoff: date) -> Decimal:
    """Capitale e interessi maturati, meno i pagamenti teorici, a una data."""
    eventi = [(date.fromisoformat(str(_value(row, "occurredOn"))), 0, "drawdown", row)
              for row in drawdowns]
    eventi += [(date.fromisoformat(str(_value(row, "dueOn"))), 1, "payment", row)
               for row in schedule]
    eventi.sort(key=lambda row: (row[0], row[1]))
    if not eventi or cutoff < eventi[0][0]:
        return ZERO
    capitale, maturati, cursore = ZERO, ZERO, eventi[0][0]
    tasso = Decimal(str(annual_rate or 0)) / Decimal("100")
    for quando, _, tipo, row in eventi:
        if quando > cutoff:
            break
        maturati += _interessi_maturati(tasso, capitale, cursore, quando)
        if tipo == "drawdown":
            capitale += money(_value(row, "amount"))
        else:
            interesse = money(_value(row, "interest"))
            if money(_value(row, "payment")) == ZERO and interesse:
                capitale += interesse
            else:
                capitale = max(ZERO, capitale - money(_value(row, "principal")))
            maturati = max(ZERO, maturati.quantize(CENT) - interesse)
        cursore = quando
    maturati += _interessi_maturati(tasso, capitale, cursore, cutoff)
    return max(ZERO, capitale + maturati).quantize(CENT)
