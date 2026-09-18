"""I contratti fra frontend e backend, nelle due direzioni.

Gli errori piu' frequenti di questa app non stavano ne' nel backend ne' nel
frontend, ma fra i due: il profilo che mandava `birthYear` a un'API che voleva
`birth_year`, la lista dei flussi che arrivava come `items` a una pagina che
leggeva `streams`, la stringa "null" al posto di un valore. Ognuno dei due lati,
testato da solo, era corretto.

    python -m tests.contratti risposte  > risposte.json
        Le risposte vere degli endpoint, su un database sqlite con dati che
        coprono i casi facoltativi. Il frontend le verifica contro i suoi tipi.

    python -m tests.contratti richieste < richieste.json
        I corpi costruiti dalle funzioni del frontend, passati ai gestori veri.
        Esce con codice 1 se un gestore li rifiuta o se contengono campi che il
        modello non conosce e quindi scarterebbe in silenzio.

Mai sul database dell'app: tutto gira su sqlite in memoria.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import typing
from datetime import date, datetime
from pathlib import Path
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import backup
from app.auth import OPZIONI_INIZIALI
from app.categorization import suggest
from app.core_routes import (RuleBulkPayload, RulePayload, accounts, analysis, balance_sheet_series, budget_annual, budget_dashboard,
                             budget_suggestions, budget_trends, budgets, calculations, categorization_rules,
                             create_categorization_rule, create_categorization_rules, goals, instrument_history,
                             investments_allocation, investments_dashboard, investments_ledger, net_worth, notes,
                             settings, summary, summary_breakdown, transactions)
from app.database import Base
from app.fire_routes import (FlussoPayload, ProfiloPayload, RegolePayload, crea_flusso, elenco_flussi, fire,
                             leggi_profilo, leggi_regole, salva_profilo, salva_regole, spostamento_pensioni)
from app.main import (AccountPayload, BudgetCreatePayload, BudgetUpdatePayload, GoalPayload, InvestmentTxPayload,
                      LiabilityPayload, NotePayload, RecurringTransactionCreate, SettingValueUpdate, SplitPayload, TransactionPayload,
                      create_account, create_budget, create_goal, create_investment_tx, create_note,
                      create_recurring_transaction, create_transaction, liabilities, list_backups_endpoint,
                      list_recurring_transactions, save_liability, split_transaction, update_budget, update_setting)
from app.models import (Account, AccountValuation, AppSetting, BudgetPlan, CategorizationRule, Goal, IncomeStream,
                        InvestmentInstrument,
                        LiabilityProfile, LookupOption, MarketPrice, Note, RetirementProfile, Transaction, TransactionLedgerLink)
from app.notifications import elenco as notifiche

# Un simbolo inventato per l'indice di riferimento: i contratti non devono
# somigliare a un portafoglio vero, devono solo avere i campi pieni.
INDICE_PROVA = "IDX.TEST"


def _sessione() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _semina(session: Session) -> None:
    """Dati che rendono non vuoti gli elenchi e presenti i campi facoltativi.

    Un elenco vuoto nel file delle risposte e' compatibile con qualunque tipo:
    il controllo non vedrebbe niente. Per questo ogni elenco ha almeno una riga.
    """
    oggi = date.today()
    anno_scorso = oggi.year - 1
    session.add_all([
        Account(source_group="bank", name="Banca", starting_balance=Decimal("20000"), current_balance=Decimal("20000")),
        Account(source_group="asset", name="Broker", starting_balance=Decimal("0"), current_balance=Decimal("0"),
                is_broker=True, is_liquid=False),
        Account(source_group="asset", name="Casa", starting_balance=Decimal("0"), current_balance=Decimal("0"),
                needs_manual_valuation=True, is_liquid=False),
        Account(source_group="liability", name="Fido", starting_balance=Decimal("0"), current_balance=Decimal("0")),
        Account(source_group="liability", name="Prestito", starting_balance=Decimal("0"), current_balance=Decimal("0")),
    ])
    for chiave, valore in {"header_color": "Blue", "late_income_shift": "Inactive", "late_income_day": "20",
                           "savings_default_category": "Savings", "net_worth_currencies": "CHF",
                           # Un indice configurato: senza, la seconda curva del
                           # confronto non comparirebbe nel file delle risposte
                           # e il contratto non controllerebbe niente.
                           "benchmark_symbol": INDICE_PROVA}.items():
        session.add(AppSetting(key=chiave, label=chiave, value=valore))
    # Il vocabolario di partenza e' quello di un account vero (`OPZIONI_INIZIALI`):
    # senza, il database dei contratti non conosce le categorie che l'app offre a
    # chiunque, e un contratto che ne usa una verrebbe rifiutato da un controllo
    # che in produzione non rifiuterebbe niente. Colori e anni restano invece
    # quelli brevi di qui: servono ai contratti, non a somigliare all'app.
    for gruppo, valori in {**OPZIONI_INIZIALI, "colors": ["Blue", "Yellow"],
                           "years": [str(anno_scorso), str(oggi.year)]}.items():
        session.add_all(LookupOption(option_group=gruppo, position=i, value=v) for i, v in enumerate(valori))
    session.commit()
    conto = {a.name: a.id for a in session.scalars(select(Account))}
    session.add(AccountValuation(account_id=conto["Casa"], observed_on=date(anno_scorso, 6, 1),
                                 value=Decimal("300000"), notes="perizia"))
    session.add(LiabilityProfile(account_id=conto["Fido"], kind="credit_line", credit_limit=Decimal("40000"),
                                 original_principal=Decimal("1"), annual_rate=Decimal("1.5"),
                                 start_date=date(anno_scorso, 1, 1), end_date=date(oggi.year + 5, 1, 1)))
    session.commit()
    save_liability(conto["Prestito"], LiabilityPayload(
        kind="term_loan", debt_type="personal", original_principal=10000, annual_rate=2, rate_type="fixed",
        start_date=f"{anno_scorso}-01-15", end_date=f"{oggi.year + 4}-01-15",
        planned_drawdowns=[{"occurred_on": f"{anno_scorso}-01-15", "amount": 10000}]), session)

    def movimento(**campi: Any) -> None:
        create_transaction(TransactionPayload(**campi), session)

    for mese in (2, 5, 9):
        movimento(occurred_on=f"{anno_scorso}-{mese:02d}-10", transaction_type="Income", category="Salary",
                  amount=3000, account_name="Banca")
        movimento(occurred_on=f"{anno_scorso}-{mese:02d}-12", transaction_type="Expenses", category="Housing",
                  amount=900, account_name="Banca", details="Affitto")
        movimento(occurred_on=f"{anno_scorso}-{mese:02d}-14", transaction_type="Expenses", category="Groceries",
                  amount=300, account_name="Banca")
    movimento(occurred_on=f"{anno_scorso}-01-15", transaction_type="Debt", amount=10000, account_name="Prestito",
              destination_name="Banca", debt_principal=10000, debt_interest=0)
    movimento(occurred_on=f"{anno_scorso}-03-15", transaction_type="Debt", amount=450, account_name="Banca",
              destination_name="Prestito", debt_principal=420, debt_interest=30)
    movimento(occurred_on=f"{anno_scorso}-04-01", transaction_type="Expenses", category="Commissions", amount=25,
              account_name="Fido", debt_interest=25)
    movimento(occurred_on=f"{anno_scorso}-06-01", transaction_type="Investment", amount=2000, account_name="Banca",
              destination_name="Broker")
    # Tre gruppi per l'apprendimento: uno deciso, uno con una minoranza e uno
    # diviso a meta'. "Affitto" c'e' gia' sopra, quindi qui bastano gli altri
    # due: con gli elenchi vuoti il contratto non verificherebbe niente.
    for indice, (categoria, quante) in enumerate((("Groceries", 4), ("Other", 1))):
        for mese in range(1, quante + 1):
            movimento(occurred_on=f"{anno_scorso}-{mese:02d}-20", transaction_type="Expenses", category=categoria,
                      amount=15 + indice, account_name="Banca", details="Bar")
    for categoria in ("Car", "Leisure"):
        for mese in range(1, 6):
            movimento(occurred_on=f"{anno_scorso}-{mese:02d}-25", transaction_type="Expenses", category=categoria,
                      amount=8, account_name="Banca", details="Parcheggio")

    salva_profilo(ProfiloPayload(birth_year=oggi.year - 30, country="CH", target_retirement_age=60, real_return=4,
                                 withdrawal_rate=4, withdrawal_tax_rate=10, expense_basis="average",
                                 lean_annual_expenses=5000, inflation=2), session)
    session.add_all([
        IncomeStream(name="AVS", kind="annuity", amount=Decimal("20000"), start_age=65, indexed=True, country="CH"),
        IncomeStream(name="LPP", kind="annuity", amount=Decimal("12000"), start_age=65, indexed=False, country="CH",
                     amount_if_stopping_now=Decimal("6000")),
        IncomeStream(name="3a", kind="capital", amount=Decimal("80000"), start_age=64, indexed=True, country="CH"),
    ])
    session.commit()
    salva_regole(RegolePayload(rules=[{"category": "Housing", "mode": "change", "amount": 6000}]), session)
    # Una regola con tutti i campi facoltativi valorizzati: un elenco vuoto
    # sarebbe compatibile con qualunque tipo e non controllerebbe niente.
    session.add_all([
        CategorizationRule(position=0, pattern="spesa coop", category="Groceries"),
        CategorizationRule(position=1, pattern=r"^pos \d+", is_regex=True, category="Commissions",
                           transaction_type="Expenses", min_amount=Decimal("10"), max_amount=Decimal("500"),
                           active=False),
    ])
    session.commit()

    # Budget, obiettivi, investimenti, appunti e ricorrenze: quanto basta perche'
    # ogni elenco delle risposte abbia almeno una riga.
    for anno in (anno_scorso, oggi.year):
        for mese in range(1, 13):
            session.add_all([
                BudgetPlan(period=date(anno, mese, 1), budget_type="Expenses", category_group="Needs", category="Housing", amount=Decimal("900")),
                BudgetPlan(period=date(anno, mese, 1), budget_type="Expenses", category_group="Wants", category="Groceries", amount=Decimal("250")),
                BudgetPlan(period=date(anno, mese, 1), budget_type="Income", category="Salary", amount=Decimal("3000")),
                BudgetPlan(period=date(anno, mese, 1), budget_type="Savings", category="Savings", amount=Decimal("1000")),
            ])
    session.add_all([
        Goal(name="Fondo emergenza", starting_amount=Decimal("1000"), target_amount=Decimal("10000"),
             start_date=date(anno_scorso, 1, 1), target_date=date(oggi.year + 2, 1, 1), kind="contributions", target_account="Banca"),
        Goal(name="Portafoglio", starting_amount=Decimal("0"), target_amount=Decimal("50000"),
             start_date=date(anno_scorso, 1, 1), target_date=date(oggi.year + 5, 1, 1), kind="portfolio"),
        Goal(name="Vecchio", starting_amount=Decimal("500"), target_amount=Decimal("500"),
             start_date=date(anno_scorso - 1, 1, 1), target_date=date(anno_scorso, 1, 1),
             completed_at=date(anno_scorso, 2, 1), kind="contributions"),
        InvestmentInstrument(name="ETF Mondo", provider_symbol="SWDA.MI", asset_class="Equity", area="World",
                             sector="Diversified", currency="EUR", target_weight=Decimal("0.8")),
        Note(section="Appunti", title="Perche' un ETF", body="Costi bassi", status="Fatto"),
    ])
    for mese in range(1, 13):
        session.add(MarketPrice(symbol="SWDA.MI", provider="yahoo", observed_on=date(anno_scorso, mese, 28),
                                fetched_at=datetime(anno_scorso, 12, 31), price=Decimal(str(90 + mese)), currency="EUR"))
        # L'indice di prova ha la sua serie, con mesi in comune e mesi no: e' da
        # li' che si vede se il confronto si accorcia dove serve.
        session.add(MarketPrice(symbol=INDICE_PROVA, provider="yahoo", observed_on=date(anno_scorso, mese, 28),
                                fetched_at=datetime(anno_scorso, 12, 31), price=Decimal(str(100 + mese * 2)), currency="EUR"))
    session.commit()
    movimento(occurred_on=f"{anno_scorso}-07-01", transaction_type="Transfers", amount=100, account_name="Banca",
              destination_name="Casa", goal="Fondo emergenza", details="Accantonamento")
    acquisto = create_investment_tx(InvestmentTxPayload(name="ETF Mondo", transaction_type="Buy", amount=1900,
                                                        occurred_on=f"{anno_scorso}-06-01", units=20, price=95, fee=2),
                                    False, session)
    vendita = create_investment_tx(InvestmentTxPayload(name="ETF Mondo", transaction_type="Sell", amount=500,
                                                       occurred_on=f"{anno_scorso}-11-01", units=5, price=100), False, session)
    versamento = next(t for t in transactions(100, session, offset=0)["items"] if t["transactionType"] == "Investment")
    session.add(TransactionLedgerLink(transaction_id=int(versamento["id"]), ledger_id=int(acquisto["id"])))
    session.commit()
    asyncio.run(create_recurring_transaction(RecurringTransactionCreate(
        description="Affitto", category="Housing", amount=900, accountName="Banca",
        recurrence_rule="FREQ=MONTHLY;BYMONTHDAY=1", start_date=f"{oggi.year}-01-01"), session))
    assert vendita


def risposte() -> dict[str, Any]:
    session = _sessione()
    _semina(session)
    oggi = date.today()
    uscita = {
        "fire": fire(session),
        "fireProfile": leggi_profilo(session),
        "fireStreams": elenco_flussi(session),
        "fireExpenseRules": leggi_regole(session),
        "firePensionShift": spostamento_pensioni(session),
        "liabilities": liabilities(session),
        "settings": settings(session),
        "netWorth": net_worth(oggi.year, oggi.month, 12, session),
        "accounts": accounts(None, session),
        "transactions": transactions(100, session, offset=0),
        "summary": summary(anno_scorso := oggi.year - 1, 9, "prior_year", session),
        "summaryBreakdownMonth": summary_breakdown(anno_scorso, 9, session),
        "summaryBreakdownYear": summary_breakdown(anno_scorso, None, session),
        "analysis": analysis(anno_scorso, "Expenses", "Housing", session),
        "budgets": budgets(anno_scorso, 9, "Expenses", session),
        "calculations": calculations(anno_scorso, 9, session),
        "budgetAnnual": budget_annual(anno_scorso, "Expenses", session),
        "budgetDashboardMonth": budget_dashboard(anno_scorso, 9, "Expenses", session),
        "budgetDashboardYear": budget_dashboard(anno_scorso, None, "Expenses", session),
        "budgetTrends": budget_trends(f"{anno_scorso},{oggi.year}", 2024, 2027, "Expenses", session),
        "budgetSuggestions": budget_suggestions(oggi.year, oggi.month, "Expenses", 6, session),
        "goals": goals(session),
        "investmentsDashboard": investments_dashboard(session),
        "investmentsLedger": investments_ledger(session),
        "investmentsAllocation": investments_allocation(session),
        "instrumentHistory": instrument_history("ETF Mondo", session),
        "balanceSheetSeries": balance_sheet_series(f"{anno_scorso}-01", f"{oggi.year}-{oggi.month:02d}", "month",
                                                   "networth", None, None, session),
        "notes": notes(session),
        "categorizationRules": categorization_rules(session),
        "categorizationSuggestions": suggest(session),
        "recurring": asyncio.run(list_recurring_transactions(session)),
        "notifications": notifiche(session),
        "backups": _backup_di_prova(),
    }
    return jsonable_encoder(uscita)


def _backup_di_prova() -> dict[str, Any]:
    # L'elenco vero legge la cartella dei backup del container: qui una
    # cartella temporanea con un dump finto, per non leggere quelli veri.
    originale = backup.BACKUPS_DIR
    with tempfile.TemporaryDirectory() as cartella:
        backup.BACKUPS_DIR = Path(cartella)
        (backup.BACKUPS_DIR / "auto-20260101T000000Z.dump").write_bytes(b"x")
        try:
            return list_backups_endpoint()
        finally:
            backup.BACKUPS_DIR = originale


# ---------------------------------------------------------------------------
# Richieste

def _chiavi_sconosciute(modello: type[BaseModel], corpo: Any, percorso: str = "") -> list[str]:
    """I campi che Pydantic scarterebbe senza dirlo.

    Il modello ignora le chiavi che non conosce: un `refundOfId` scritto al
    posto di `refund_of_id` non da' errore, il valore sparisce e basta.
    """
    if not isinstance(corpo, dict):
        return []
    noti: dict[str, Any] = {}
    for nome, campo in modello.model_fields.items():
        noti[nome] = campo.annotation
        if campo.alias:
            noti[campo.alias] = campo.annotation
    problemi = [f"{percorso}{chiave}" for chiave in corpo if chiave not in noti]
    for chiave, valore in corpo.items():
        interni = [a for a in typing.get_args(noti.get(chiave)) if isinstance(a, type) and issubclass(a, BaseModel)]
        if interni and isinstance(valore, list):
            for i, elemento in enumerate(valore):
                problemi += _chiavi_sconosciute(interni[0], elemento, f"{percorso}{chiave}[{i}].")
    return problemi


def _gestori(session: Session) -> dict[str, tuple[type[BaseModel], Any]]:
    conto = {a.name: a.id for a in session.scalars(select(Account))}
    return {
        "profile": (ProfiloPayload, lambda p: salva_profilo(p, session)),
        "stream": (FlussoPayload, lambda p: crea_flusso(p, session)),
        "expenseRules": (RegolePayload, lambda p: salva_regole(p, session)),
        "transaction": (TransactionPayload, lambda p: create_transaction(p, session)),
        "creditLineTerms": (LiabilityPayload, lambda p: save_liability(conto["Fido"], p, session)),
        "termLoanTerms": (LiabilityPayload, lambda p: save_liability(conto["Prestito"], p, session)),
        "setting": (SettingValueUpdate, lambda p: update_setting("header_color", p, session)),
        "account": (AccountPayload, lambda p: create_account(p, session)),
        "goal": (GoalPayload, lambda p: create_goal(p, session)),
        "ledgerOperation": (InvestmentTxPayload, lambda p: create_investment_tx(p, True, session)),
        "note": (NotePayload, lambda p: create_note(p, session)),
        "budgetCreate": (BudgetCreatePayload, lambda p: create_budget(p, session)),
        "budgetUpdate": (BudgetUpdatePayload, lambda p: update_budget(
            session.scalars(select(BudgetPlan.id).where(BudgetPlan.category == "Groceries")).first(), p, session)),
        "recurring": (RecurringTransactionCreate, lambda p: asyncio.run(create_recurring_transaction(p, session))),
        "categorizationRule": (RulePayload, lambda p: create_categorization_rule(p, session)),
        "categorizationBulk": (RuleBulkPayload, lambda p: create_categorization_rules(p, session)),
        "split": (SplitPayload, lambda p: split_transaction(session.scalars(select(Transaction.id).where(
            Transaction.transaction_type == "Expenses", Transaction.category == "Housing")).first(), p, session)),
    }


def richieste(elenco: list[dict[str, Any]]) -> list[str]:
    errori: list[str] = []
    for voce in elenco:
        # Un database per richiesta: una scrittura non deve preparare la strada
        # alla successiva, ne' uno stream duplicato farla fallire.
        session = _sessione()
        _semina(session)
        nome, corpo = voce["endpoint"], voce["body"]
        etichetta = f"{nome} ({voce.get('case', '')})"
        gestori = _gestori(session)
        if nome not in gestori:
            errori.append(f"{etichetta}: endpoint sconosciuto al test")
            continue
        modello, gestore = gestori[nome]
        errori += [f"{etichetta}: campo sconosciuto al backend '{c}'" for c in _chiavi_sconosciute(modello, corpo)]
        try:
            gestore(modello.model_validate(corpo))
        except HTTPException as errore:
            errori.append(f"{etichetta}: rifiutata {errore.status_code} {errore.detail}")
        except Exception as errore:  # noqa: BLE001 - qualunque rifiuto va riportato
            errori.append(f"{etichetta}: {type(errore).__name__}: {errore}")
        finally:
            session.close()
    return errori


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando == "risposte":
        json.dump(risposte(), sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)
    elif comando == "richieste":
        elenco = json.load(sys.stdin)
        errori = richieste(elenco)
        for riga in errori:
            print(riga)
        print(f"{len(elenco)} richieste controllate, {len(errori)} problemi")
        sys.exit(1 if errori or not elenco else 0)
    else:
        sys.exit("uso: python -m tests.contratti risposte|richieste")
