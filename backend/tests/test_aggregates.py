"""Gli aggregati che l'app espone, verificati su dati costruiti a mano.

I test di parita' con il workbook (``test_excel_parity``) confrontano l'app con
l'oracolo, ma richiedono il file e un database gia' popolato. Questi invece
girano ovunque, su un SQLite temporaneo, e presidiano le regole che in passato
si sono rotte in silenzio: un template ricorrente contato come spesa reale, e
il capitale investito calcolato dimenticando le posizioni gia' vendute.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import budget_actual, budget_actual_year, period_total, portfolio_timeline
from app.database import Base
from app.models import (Account, BudgetPlan, InvestmentInstrument, InvestmentTransaction,
                        MarketPrice, Transaction)
from tests.categorie_fixture import categoria


def _tx(session: Session, day: date, tx_type: str, nome: str, amount: str, *, template: bool = False,
        account: str = "Conto", counts_in_budget: bool = True, refund_of_id: int | None = None) -> Transaction:
    """Un movimento su una categoria che esiste.

    "_" vuol dire "non ne ha" - i giroconti non hanno categoria - e vale NULL,
    non una categoria chiamata trattino basso.
    """
    return Transaction(occurred_on=day, effective_on=day, transaction_type=tx_type,
                       category_id=None if nome == "_" else categoria(session, nome),
                       amount=Decimal(amount), account_type="Bank",
                       account_name=account, is_recurring_template=template,
                       counts_in_budget=counts_in_budget, refund_of_id=refund_of_id)


class RecurringTemplatesAreNotMovementsTests(unittest.TestCase):
    """Un template descrive cosa succedera', non cosa e' successo.

    Contarlo gonfia spese, budget e tasso di risparmio senza che nulla segnali
    l'errore: e' esattamente il modo in cui si e' rotto una volta.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(_tx(self.session, date(2026, 9, 3), "Expenses", "Groceries", "50.00"))
        self.session.add(_tx(self.session, date(2026, 9, 10), "Expenses", "Groceries", "999.99", template=True))
        self.session.add(BudgetPlan(period=date(2026, 9, 1), budget_type="Expenses",
                                    category_id=categoria(self.session, "Groceries"), amount=Decimal("100")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_period_total_ignores_templates(self) -> None:
        self.assertEqual(period_total(self.session, 2026, 9, "Expenses"), 50.0)

    def test_budget_actual_ignores_templates(self) -> None:
        # Le chiavi sono id: si chiede la categoria che la fixture ha creato.
        self.assertEqual(budget_actual(self.session, 2026, 9, "Expenses").get(
            categoria(self.session, "Groceries")), 50.0)


class InvestedCapitalTests(unittest.TestCase):
    """Il capitale investito e' il versato netto: le vendite lo riducono.

    Sommare solo le posizioni ancora aperte lo gonfia di tutte le plusvalenze
    gia' realizzate, ed e' il numero che non tornava con l'Excel.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(InvestmentInstrument(name="Fondo A", provider_symbol="AAA.MI",
                                              asset_class="Equity", area="World",
                                              sector="Diversified", currency="EUR"))
        self.session.add(InvestmentInstrument(name="Fondo B", provider_symbol="BBB.MI",
                                              asset_class="Equity", area="World",
                                              sector="Diversified", currency="EUR"))
        # Fondo B viene comprato e rivenduto per intero: la posizione si chiude,
        # ma l'incasso ha ridotto il capitale immobilizzato.
        self.session.add_all([
            InvestmentTransaction(occurred_on=date(2026, 1, 15), ticker="AAA.MI", name="Fondo A",
                                  transaction_type="Buy", amount=Decimal("1000.00"),
                                  units=Decimal("10"), price=Decimal("100"), currency="EUR"),
            InvestmentTransaction(occurred_on=date(2026, 2, 10), ticker="BBB.MI", name="Fondo B",
                                  transaction_type="Buy", amount=Decimal("500.00"),
                                  units=Decimal("5"), price=Decimal("100"), currency="EUR"),
            InvestmentTransaction(occurred_on=date(2026, 3, 10), ticker="BBB.MI", name="Fondo B",
                                  transaction_type="Sell", amount=Decimal("600.00"),
                                  units=Decimal("5"), price=Decimal("120"), currency="EUR"),
        ])
        self.session.add(MarketPrice(symbol="AAA.MI", observed_on=date(2026, 3, 31),
                                     price=Decimal("110"), provider="test", currency="EUR"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_closed_position_reduces_invested_capital(self) -> None:
        timeline = portfolio_timeline(self.session)
        march = next(point for point in timeline if point["period"].startswith("2026-03"))
        # 1000 versati su A, 500 su B, 600 rientrati dalla vendita di B.
        self.assertAlmostEqual(march["investedCapital"], 900.0, places=2)

    def test_market_value_counts_only_open_positions(self) -> None:
        timeline = portfolio_timeline(self.session)
        march = next(point for point in timeline if point["period"].startswith("2026-03"))
        # Solo le 10 quote di A, valorizzate a 110.
        self.assertAlmostEqual(march["marketValue"], 1100.0, places=2)


class AccountBalanceSignTests(unittest.TestCase):
    """Le regole di segno dei conti sono quelle del workbook: un trasferimento
    toglie dall'origine e aggiunge alla destinazione, una spesa toglie e basta."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(source_group="bank", name="Conto",
                                 starting_balance=Decimal("1000"), current_balance=Decimal("0"),
                                 status="active"))
        self.session.add(Account(source_group="asset", name="Deposito",
                                 starting_balance=Decimal("0"), current_balance=Decimal("0"),
                                 status="active"))
        transfer = _tx(self.session, date(2026, 4, 2), "Transfers", "_", "200.00")
        transfer.destination_type, transfer.destination_name = "Asset", "Deposito"
        self.session.add_all([transfer, _tx(self.session, date(2026, 4, 5), "Expenses", "Groceries", "30.00"),
                              _tx(self.session, date(2026, 4, 8), "Income", "Salary", "500.00")])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_balances_follow_the_workbook_rules(self) -> None:
        from app.calculation_engine import account_reconciliation
        from sqlalchemy import select

        accounts = self.session.scalars(select(Account)).all()
        transactions = self.session.scalars(select(Transaction)).all()
        computed = {row["account"].name: float(row["calculated"]) for row in account_reconciliation(accounts, transactions)}
        self.assertAlmostEqual(computed["Conto"], 1000 - 200 - 30 + 500, places=2)
        self.assertAlmostEqual(computed["Deposito"], 200, places=2)


class AnnualRefundNettingTests(unittest.TestCase):
    """Un rimborso netta dall'importo della categoria dell'originale.

    La versione annuale (`budget_actual_year`) deve dare lo stesso numero della
    versione mensile (`budget_actual`) quando si sommano i mesi: 50 di spesa
    meno 20 di rimborso fanno 30 netti, non 70, anche se l'originale e il
    rimborso cadono in mesi (o anni) diversi.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        # Originale a novembre 2026, rimborso a gennaio 2027: il netting va
        # applicato nel mese dell'originale (novembre), non in quello del
        # rimborso. L'originale resta budgeable per poter essere scalato.
        originale = _tx(self.session, date(2026, 11, 5), "Expenses", "Spese mediche", "50.00")
        self.session.add(originale)
        self.session.flush()
        self.session.add(_tx(self.session, date(2027, 1, 10), "Income", "Rimborso spese mediche",
                             "20.00", counts_in_budget=False, refund_of_id=originale.id))
        self.session.add(_tx(self.session, date(2026, 11, 12), "Expenses", "Altro", "30.00"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_refund_nets_in_original_month(self) -> None:
        speso = budget_actual_year(self.session, 2026, "Expenses")
        # 50 di Spese mediche - 20 di rimborso = 30; piu' 30 di Altro.
        self.assertEqual(speso[11].get(categoria(self.session, "Spese mediche")), 30.0)
        self.assertEqual(speso[11].get(categoria(self.session, "Altro")), 30.0)

    def test_refund_does_not_appear_in_refund_month(self) -> None:
        # Il rimborso non rientra nel calcolo del 2027: l'originale non e' del
        # 2027 (lato Expenses), e il rimborso ha counts_in_budget=False (lato
        # Income). Su entrambi i fronti l'aggregato e' vuoto.
        self.assertEqual(budget_actual_year(self.session, 2027, "Expenses"),
                         {m: {} for m in range(1, 13)})
        self.assertEqual(budget_actual_year(self.session, 2027, "Income"),
                         {m: {} for m in range(1, 13)})

    def test_annual_matches_sum_of_monthly(self) -> None:
        per_mese = {m: budget_actual(self.session, 2026, m, "Expenses") for m in range(1, 13)}
        annuale = budget_actual_year(self.session, 2026, "Expenses")
        totale_mensile = {cat: round(sum(per_mese[m].get(cat, 0.0) for m in range(1, 13)), 2)
                          for cat in {c for valori in per_mese.values() for c in valori}}
        totale_annuale = {cat: round(sum(valori.get(cat, 0.0) for valori in annuale.values()), 2)
                          for cat in {c for valori in annuale.values() for c in valori}}
        self.assertEqual(totale_mensile, totale_annuale)


if __name__ == "__main__":
    unittest.main()


class CategorieSenzaPianoTests(unittest.TestCase):
    """La spesa in una categoria senza budget e' spesa lo stesso.

    La ripartizione partiva dal piano: un anno senza budget mostrava una torta
    vuota con migliaia di euro spesi, e le categorie mai pianificate non
    comparivano fra le piu' pesanti di Andamento annuale.
    """

    def setUp(self) -> None:
        from datetime import date as _date
        from decimal import Decimal as _Decimal
        from sqlalchemy import create_engine as _engine
        from sqlalchemy.orm import Session as _Session
        from app.database import Base as _Base
        from app.models import Account as _Account, BudgetPlan as _Plan
        from app.main import TransactionPayload as _Payload, create_transaction as _crea
        self.engine = _engine("sqlite://")
        _Base.metadata.create_all(self.engine)
        self.session = _Session(self.engine)
        self.session.add(_Account(source_group="bank", name="Banca", starting_balance=_Decimal("0"), current_balance=_Decimal("0")))
        self.session.add(_Plan(period=_date(2025, 3, 1), budget_type="Expenses",
                               category_id=categoria(self.session, "Housing"), amount=_Decimal("500")))
        self.session.commit()
        # Il modulo scrive ancora il nome della categoria: e' il confine dove i
        # nomi esistono, e la categoria "Housing" e' gia' una riga per il piano.
        for nome, importo in (("Housing", 400), ("Viaggi", 900)):
            _crea(_Payload(occurred_on="2025-03-10", transaction_type="Expenses", category=nome,
                           amount=importo, account_name="Banca"), self.session)

    def tearDown(self) -> None:
        self.session.close()

    def test_la_categoria_senza_piano_compare_con_budget_zero(self) -> None:
        from app.core_routes import _period_category_breakdown
        righe = {r["name"]: (r["amount"], r["budget"]) for r in _period_category_breakdown(self.session, 2025, None, "Expenses")}
        self.assertEqual({"Housing": (400.0, 500.0), "Viaggi": (900.0, 0)}, righe)

    def test_andamento_annuale_la_mette_fra_le_piu_pesanti_e_fra_le_scelte(self) -> None:
        from app.core_routes import analysis
        esito = analysis(2025, "Expenses", None, self.session)
        self.assertEqual("Viaggi", esito["topExpenseCategories"][0]["name"])
        self.assertEqual(["Housing", "Viaggi"], esito["categoryOptions"])
