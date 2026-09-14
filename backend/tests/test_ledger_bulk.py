from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.models import Account, InvestmentTransaction, Transaction, TransactionLedgerLink


TABELLE = [Account.__table__, Transaction.__table__, InvestmentTransaction.__table__,
           TransactionLedgerLink.__table__]


class LedgerBulkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(source_group="bank", name="Banca", starting_balance=Decimal("1000"),
                    current_balance=Decimal("1000")),
            Account(source_group="asset", name="Broker", starting_balance=Decimal("0"),
                    current_balance=Decimal("0"), is_broker=True),
        ])
        self.ops = [InvestmentTransaction(name=f"Titolo {index}", transaction_type="Buy",
                    occurred_on=date(2026, 1, 5), amount=Decimal(amount), units=Decimal("1"),
                    price=Decimal(amount), currency="EUR")
                    for index, amount in enumerate(("100", "150", "50"), 1)]
        self.session.add_all(self.ops)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def movimento(self, amount: str = "300", tipo: str = "Investment") -> Transaction:
        tx = Transaction(occurred_on=date(2026, 1, 5), effective_on=date(2026, 1, 5),
                         transaction_type=tipo, category="_", amount=Decimal(amount),
                         account_name="Banca", destination_name="Broker")
        self.session.add(tx)
        self.session.commit()
        return tx

    def test_bulk_name_modifica_solo_le_righe_indicate(self) -> None:
        result = main.bulk_investment_transactions(main.BulkInvestmentTransactionsPayload(
            ids=[self.ops[0].id, self.ops[1].id], changes={"name": "MSCI ACWI"}), self.session)
        self.assertEqual(2, result["updated"])
        self.assertEqual(["MSCI ACWI", "MSCI ACWI", "Titolo 3"], [row.name for row in self.ops])

    def test_bulk_rifiuta_amount(self) -> None:
        with self.assertRaises(HTTPException) as error:
            main.bulk_investment_transactions(main.BulkInvestmentTransactionsPayload(
                ids=[self.ops[0].id], changes={"amount": 10}), self.session)
        self.assertEqual(422, error.exception.status_code)

    def test_bulk_link_completo_passa(self) -> None:
        tx = self.movimento()
        result = main.bulk_link_investment_transactions(main.BulkLedgerLinkPayload(
            ids=[row.id for row in self.ops], transaction_id=tx.id), self.session)
        self.assertEqual(3, result["linked"])
        self.assertEqual(3, self.session.scalar(select(func.count(TransactionLedgerLink.id))))

    def test_bulk_link_parziale_non_scrive_niente(self) -> None:
        tx = self.movimento()
        with self.assertRaises(HTTPException) as error:
            main.bulk_link_investment_transactions(main.BulkLedgerLinkPayload(
                ids=[self.ops[0].id, self.ops[1].id], transaction_id=tx.id), self.session)
        self.assertEqual("ledgerGroupUnbalanced", error.exception.detail["code"])
        self.assertEqual(0, self.session.scalar(select(func.count(TransactionLedgerLink.id))))

    def test_bulk_link_rifiuta_un_movimento_non_investment(self) -> None:
        tx = self.movimento(tipo="Transfers")
        with self.assertRaises(HTTPException) as error:
            main.bulk_link_investment_transactions(main.BulkLedgerLinkPayload(
                ids=[row.id for row in self.ops], transaction_id=tx.id), self.session)
        self.assertEqual("linkNeedsInvestment", error.exception.detail["code"])
        self.assertEqual(0, self.session.scalar(select(func.count(TransactionLedgerLink.id))))


if __name__ == "__main__":
    unittest.main()
