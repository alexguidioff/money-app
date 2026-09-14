from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.models import InvestmentTransaction, InvestmentTransactionDetail


TABELLE = [InvestmentTransaction.__table__, InvestmentTransactionDetail.__table__]


class LedgerDuplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        self.session.add(InvestmentTransaction(name="ETF", transaction_type="Buy",
                                               occurred_on=date(2026, 6, 4), amount=Decimal("100"),
                                               units=Decimal("2"), price=Decimal("50")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def payload(self, giorno: str = "2026-06-06", *, units: float = 2, name: str = "ETF") -> main.InvestmentTxPayload:
        return main.InvestmentTxPayload(name=name, transaction_type="Buy", occurred_on=giorno,
                                        amount=100, units=units, price=50)

    def test_identica_a_due_giorni_e_duplicata(self) -> None:
        self.assertIsNotNone(main.operazione_gia_presente(self.session, self.payload()))

    def test_identica_a_cinque_giorni_non_e_duplicata(self) -> None:
        self.assertIsNone(main.operazione_gia_presente(self.session, self.payload("2026-06-09")))

    def test_quote_diverse_non_sono_un_duplicato(self) -> None:
        self.assertIsNone(main.operazione_gia_presente(self.session, self.payload(units=1)))

    def test_batch_salta_il_duplicato_e_crea_le_altre_due(self) -> None:
        result = main.create_investment_tx_batch([
            self.payload(), self.payload(name="Fondo"), self.payload(name="Obbligazione")], self.session)
        self.assertEqual((2, 1), (len(result["created"]), len(result["skipped"])))
        self.assertEqual(3, self.session.scalar(select(func.count(InvestmentTransaction.id))))

    def test_force_crea_comunque(self) -> None:
        result = main.create_investment_tx(self.payload(), force=True, session=self.session)
        self.assertEqual("ETF", result["name"])
        self.assertEqual(2, self.session.scalar(select(func.count(InvestmentTransaction.id))))

    def test_singolo_restituisce_il_duplicato_nel_409(self) -> None:
        with self.assertRaises(HTTPException) as error:
            main.create_investment_tx(self.payload(), session=self.session)
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual("ledgerDuplicate", error.exception.detail["code"])
        self.assertEqual("2026-06-04", error.exception.detail["duplicate"]["occurredOn"])


if __name__ == "__main__":
    unittest.main()
