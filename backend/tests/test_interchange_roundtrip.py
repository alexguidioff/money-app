"""Il formato di scambio deve restituire esattamente cio' che ha preso.

E' la promessa su cui si regge l'autonomia dall'Excel: se un export non si
reimporta identico, i dati non sono trasportabili. Il test lavora su due
database SQLite temporanei, quindi non tocca nulla di reale.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from io import BytesIO

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.interchange import SHEETS, build_export
from app.interchange_import import InterchangeError, import_data
from app.models import (Account, AppSetting, BudgetPlan, Goal,
                        InvestmentInstrument, InvestmentTransaction, InvestmentTransactionDetail,
                        LookupOption, Note, Transaction)


def _populate(session: Session) -> None:
    """Una riga per entita', con i casi che di solito rompono un round-trip:
    booleani, decimali, date, campi vuoti e riferimenti fra tabelle."""
    session.add(Account(source_group="bank", name="Conto corrente",
                        starting_balance=Decimal("100.00"), current_balance=Decimal("1234.56"), status="active"))
    template = Transaction(occurred_on=date(2026, 1, 31), effective_on=date(2026, 1, 31),
                           transaction_type="Expenses", category="Groceries", amount=Decimal("42.50"),
                           account_type="Bank", account_name="Conto corrente", details=None,
                           balance=Decimal("-63.48"), is_recurring_template=True, recurrence_rule="monthly",
                           recurrence_end_date=date(2026, 12, 31))
    session.add(template)
    session.flush()
    session.add(Transaction(occurred_on=date(2026, 2, 28), effective_on=date(2026, 2, 28),
                            transaction_type="Income", category="Salary", amount=Decimal("2000.00"),
                            account_type="Bank", account_name="Conto corrente", goal="Investire",
                            balance=Decimal("0"), is_recurring_template=False,
                            recurrence_parent_id=template.id))
    session.add(BudgetPlan(period=date(2026, 3, 1), budget_type="Expenses", category_group="Casa",
                           category="Affitto", amount=Decimal("750.00")))
    session.add(Goal(name="Fondo emergenza", starting_amount=Decimal("0"), target_amount=Decimal("10000"),
                     start_date=date(2026, 1, 1), target_date=date(2027, 1, 1)))
    ledger = InvestmentTransaction(occurred_on=date(2026, 1, 15), ticker="VWCE.DE", name="Vanguard All-World",
                                   transaction_type="Buy", amount=Decimal("2500.00"),
                                   units=Decimal("21.345678"), price=Decimal("117.12"), currency="EUR")
    session.add(ledger)
    session.flush()
    session.add(InvestmentTransactionDetail(transaction_id=ledger.id, fee=Decimal("1.99"), notes="commissione"))
    session.add(InvestmentInstrument(name="Vanguard All-World", provider_symbol="VWCE.DE", isin="IE00BK5BQT80",
                                     asset_class="Equity", area="World", sector="Diversified",
                                     currency="EUR", target_weight=Decimal("0.6")))
    session.add(Note(section="Investimenti", title="Piano", body="Versare ogni mese", status="open"))
    session.add(AppSetting(key="late_income_shift", label="Shift entrate", value="Active"))
    session.add(AppSetting(key="late_income_day", label="Giorno", value="20"))
    session.add(LookupOption(option_group="accounts", position=1, value="Conto corrente"))
    session.commit()


class InterchangeRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_engine = create_engine("sqlite://")
        self.target_engine = create_engine("sqlite://")
        for engine in (self.source_engine, self.target_engine):
            Base.metadata.create_all(engine)
        self.source = Session(self.source_engine)
        self.target = Session(self.target_engine)
        _populate(self.source)

    def tearDown(self) -> None:
        self.source.close()
        self.target.close()

    def test_export_reimports_identical(self) -> None:
        blob = build_export(self.source).getvalue()
        import_data(self.target, BytesIO(blob), source_name="test.xlsx")

        for title, (model, columns) in SHEETS.items():
            order = "id" if "id" in columns else columns[0]
            fields = ", ".join(f'"{name}"' for name in columns)
            query = text(f'select {fields} from {model.__table__.name} order by "{order}"')
            before = [tuple(row) for row in self.source.execute(query)]
            after = [tuple(row) for row in self.target.execute(query)]
            self.assertEqual(before, after, f"il foglio {title} non torna identico")

    def test_effective_date_is_recomputed_not_copied(self) -> None:
        """La competenza non viaggia nel file: si ricava dalle impostazioni.
        Con lo shift attivo al giorno 20, l'entrata del 28 febbraio pesa su marzo."""
        blob = build_export(self.source).getvalue()
        import_data(self.target, BytesIO(blob), source_name="test.xlsx")
        income = self.target.scalar(select(Transaction).where(Transaction.transaction_type == "Income"))
        self.assertEqual(income.effective_on, date(2026, 3, 1))

    def test_truncated_file_is_refused_without_touching_data(self) -> None:
        from openpyxl import load_workbook
        blob = BytesIO(build_export(self.source).getvalue())
        workbook = load_workbook(blob)
        workbook["Movimenti"].delete_rows(2)
        broken = BytesIO()
        workbook.save(broken)
        broken.seek(0)

        _populate(self.target)
        before = self.target.scalar(select(Transaction.id).order_by(Transaction.id))
        with self.assertRaises(InterchangeError):
            import_data(self.target, broken, source_name="rotto.xlsx")
        self.target.rollback()
        self.assertEqual(self.target.scalar(select(Transaction.id).order_by(Transaction.id)), before)


if __name__ == "__main__":
    unittest.main()
