"""Tipi e categorie dei movimenti che non passano dal modulo "Nuovo movimento".

L'anteprima di un estratto conto e il modulo delle ricorrenze offrono gli stessi
tipi del modulo principale e le categorie del tipo scelto. Qui si guarda che il
salvataggio rispetti la stessa regola: il risparmio non e' un tipo.
"""

from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import RecurringTransactionCreate, create_recurring_transaction, save_pdf_transactions
from app.models import Account, Transaction


def _riga(**campi) -> dict:
    return {"date": "2026-08-05", "amount": 50, "accountName": "Banca", "destinationName": None,
            "description": "riga", "category": "Da categorizzare", "categoryAutomatic": True, **campi}


class ImportEstrattoTipiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(source_group="bank", name="Banca", starting_balance=Decimal("0"), current_balance=Decimal("0"), is_active=True),
            Account(source_group="bank", name="Conto deposito", starting_balance=Decimal("0"), current_balance=Decimal("0"), is_active=True),
            Account(source_group="asset", name="Broker", starting_balance=Decimal("0"), current_balance=Decimal("0"),
                    is_active=True, is_broker=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _salva(self, *righe: dict) -> dict:
        return asyncio.run(save_pdf_transactions(list(righe), self.session))

    def test_la_categoria_scelta_si_salva_e_quella_automatica_resta_da_sistemare(self) -> None:
        esito = self._salva(_riga(transactionType="Expenses", category="Groceries", categoryAutomatic=False),
                            _riga(transactionType="Income"))
        self.assertEqual([], esito["errors"])
        self.assertEqual(["Groceries", "Da categorizzare"],
                         [t.category for t in self.session.scalars(select(Transaction).order_by(Transaction.id))])

    def test_un_investimento_va_sul_broker_e_non_ha_categoria(self) -> None:
        esito = self._salva(_riga(transactionType="Investment", destinationName="Broker", category="Groceries", categoryAutomatic=False))
        self.assertEqual([], esito["errors"])
        movimento = self.session.scalar(select(Transaction))
        self.assertEqual(("Investment", "Broker", "_"), (movimento.transaction_type, movimento.destination_name, movimento.category))

    def test_un_investimento_senza_broker_dice_perche(self) -> None:
        esito = self._salva(_riga(transactionType="Investment", destinationName="Conto deposito"))
        self.assertEqual([{"index": 0, "code": "investmentNeedsBroker"}], esito["errors"])

    def test_il_risparmio_non_e_un_tipo(self) -> None:
        esito = self._salva(_riga(transactionType="Savings"))
        self.assertEqual([{"index": 0, "code": "statementInvalidType"}], esito["errors"])


    def test_una_ricorrenza_di_risparmio_viene_rifiutata(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            asyncio.run(create_recurring_transaction(RecurringTransactionCreate(
                description="Accantonamento", category="Savings", amount=100, transactionType="Savings",
                accountName="Banca", recurrence_rule="FREQ=MONTHLY;BYMONTHDAY=1", start_date="2026-09-01"), self.session))
        self.assertEqual((422, "statementInvalidType"), (errore.exception.status_code, errore.exception.detail))
        self.assertIsNone(self.session.scalar(select(Transaction)))


if __name__ == "__main__":
    unittest.main()
