"""Dividere un movimento: la parte propria e quella da farsi restituire."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import SplitPayload, split_transaction
from app.models import Account, Transaction
from tests.categorie_fixture import categoria


class DividiMovimentoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        for nome, gruppo in (("Carta", "bank"), ("Splitwise", "asset"), ("Broker", "asset")):
            self.session.add(Account(source_group=gruppo, name=nome, starting_balance=Decimal("0"), current_balance=Decimal("0"),
                                     is_active=True, is_broker=nome == "Broker"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _spesa(self, **campi) -> Transaction:
        # PIANO-B3: la categoria e' una riga e il movimento la nomina per id.
        # "_" continua a voler dire "nessuna categoria" - un investimento non
        # ne ha una - e non deve diventare una categoria vera.
        nome = campi.pop("category", "Eating out")
        riga = Transaction(**{"occurred_on": date(2026, 7, 26), "effective_on": date(2026, 7, 26), "transaction_type": "Expenses",
                              "category_id": None if nome in ("", "_") else categoria(self.session, nome),
                              "amount": Decimal("27.00"), "account_name": "Carta",
                              "details": "LS Drama bar", **campi})
        self.session.add(riga)
        self.session.commit()
        return riga

    def _dividi(self, riga: Transaction, **campi) -> dict:
        return split_transaction(riga.id, SplitPayload(**{"amount": 13.50, "transaction_type": "Transfers",
                                                          "destination_name": "Splitwise", **campi}), self.session)

    def test_meta_spesa_e_meta_trasferimento(self) -> None:
        riga = self._spesa()
        esito = self._dividi(riga)
        originale, nuovo = self.session.get(Transaction, riga.id), self.session.get(Transaction, esito["created"]["id"])
        # PIANO-B3: la categoria si confronta per id. "Eating out" e' la stessa
        # categoria che la fixture ha creato, e una volta creata si ritrova.
        eating_out = categoria(self.session, "Eating out")
        self.assertEqual((Decimal("13.50"), "Expenses", eating_out), (originale.amount, originale.transaction_type, originale.category_id))
        self.assertEqual((Decimal("13.50"), "Transfers", "Carta", "Splitwise", date(2026, 7, 26), "LS Drama bar"),
                         (nuovo.amount, nuovo.transaction_type, nuovo.account_name, nuovo.destination_name,
                          nuovo.occurred_on, nuovo.details))
        # Il trasferimento non pesa sul budget: resta fuori come ogni giroconto.
        self.assertFalse(nuovo.counts_in_budget)

    def test_la_parte_nuova_puo_avere_la_sua_categoria(self) -> None:
        esito = self._dividi(self._spesa(), amount=7, transaction_type="Expenses", destination_name=None, category="Gifts")
        self.assertEqual(("Expenses", "Gifts", 7.0), (esito["created"]["transactionType"], esito["created"]["category"], esito["created"]["amount"]))

    def test_importo_fuori_dai_limiti(self) -> None:
        riga = self._spesa()
        for importo in (0, 27, 30, 1.005):
            with self.subTest(importo=importo), self.assertRaises(HTTPException) as errore:
                self._dividi(riga, amount=importo)
            self.assertEqual("splitInvalidAmount", errore.exception.detail)
        self.assertEqual(1, self.session.scalar(select(func.count(Transaction.id))))

    def test_un_trasferimento_senza_destinazione_non_lascia_il_movimento_a_meta(self) -> None:
        riga = self._spesa()
        with self.assertRaises(HTTPException):
            self._dividi(riga, destination_name=None)
        self.session.rollback()
        self.assertEqual((1, Decimal("27.00")), (self.session.scalar(select(func.count(Transaction.id))),
                                                 self.session.get(Transaction, riga.id).amount))

    def test_investimenti_e_rimborsi_restano_interi(self) -> None:
        investimento = self._spesa(transaction_type="Investment", category="_", destination_name="Broker")
        rimborsata = self._spesa()
        self._spesa(transaction_type="Income", category="Others", refund_of_id=rimborsata.id)
        for riga in (investimento, rimborsata):
            with self.subTest(tipo=riga.transaction_type), self.assertRaises(HTTPException) as errore:
                self._dividi(riga)
            self.assertEqual((409, "splitNotAllowed"), (errore.exception.status_code, errore.exception.detail))

    def test_il_risparmio_non_e_un_tipo(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            self._dividi(self._spesa(), transaction_type="Savings")
        self.assertEqual("statementInvalidType", errore.exception.detail)


if __name__ == "__main__":
    unittest.main()
