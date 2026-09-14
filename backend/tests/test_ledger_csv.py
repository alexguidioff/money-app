from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.models import InvestmentTransaction


class LedgerCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[InvestmentTransaction.__table__])
        self.session = Session(self.engine)
        self.session.add(InvestmentTransaction(name="ETF", transaction_type="Buy",
                         occurred_on=date(2026, 6, 4), amount=Decimal("100"),
                         units=Decimal("2"), price=Decimal("50"), currency="EUR"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_mapping_parsing_e_duplicati_nell_anteprima(self) -> None:
        content = ("Data;Nome;Tipo;Importo;Quote;Prezzo;Valuta\n"
                   "06/06/2026;ETF;Acquisto;100,00;2;50,00;EUR\n"
                   "07/06/2026;ETF;Buy;100,00;2;50,00;EUR\n").encode()
        headers, rows = main._ledger_csv_rows(content)
        mapping = dict(zip(("date", "name", "type", "amount", "units", "price", "currency"), headers))
        preview = main._ledger_csv_preview(rows, mapping, self.session)
        self.assertEqual((2, 2, 0), (len(preview), sum(r["duplicate"] for r in preview),
                                    sum(bool(r["error"]) for r in preview)))
        self.assertEqual("2026-06-06", preview[0]["occurred_on"])


if __name__ == "__main__":
    unittest.main()
