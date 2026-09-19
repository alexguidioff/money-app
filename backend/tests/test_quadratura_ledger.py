"""Un gruppo collegato deve tornare: bonifici e operazioni dicono la stessa cifra.

Il gruppo non e' "questo movimento e le sue operazioni". Un'operazione puo'
essere finanziata da due bonifici dello stesso giorno, e allora nessuno dei due
da solo pareggia: guardarne uno alla volta darebbe un falso scarto e il blocco
rifiuterebbe dati corretti. Si segue la catena finche' non si chiude.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.models import (Account, InvestmentTransaction, Transaction, TransactionLedgerLink)

TABELLE = [Account.__table__, Transaction.__table__, InvestmentTransaction.__table__,
           TransactionLedgerLink.__table__]


class QuadraturaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        self.session.add(Account(source_group="bank", name="Banca", starting_balance=Decimal("10000"),
                                 current_balance=Decimal("10000")))
        self.session.add(Account(source_group="asset", name="Broker", starting_balance=Decimal("0"),
                                 current_balance=Decimal("0"), is_broker=True))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _bonifico(self, importo: str, da: str = "Banca", a: str = "Broker") -> Transaction:
        # Un bonifico verso il broker non ha categoria: il segnaposto "_" della
        # vecchia colonna di testo adesso e' semplicemente l'assenza di id.
        riga = Transaction(occurred_on=date(2024, 1, 5), effective_on=date(2024, 1, 5),
                           transaction_type="Investment", category_id=None, amount=Decimal(importo),
                           account_name=da, destination_name=a)
        self.session.add(riga)
        self.session.flush()
        return riga

    def _operazione(self, importo: str, tipo: str = "Buy") -> InvestmentTransaction:
        riga = InvestmentTransaction(name="Titolo", transaction_type=tipo, occurred_on=date(2024, 1, 5),
                                     units=Decimal("1"), amount=Decimal(importo), price=Decimal(importo))
        self.session.add(riga)
        self.session.flush()
        return riga

    def _collega(self, tx: Transaction, op: InvestmentTransaction) -> None:
        self.session.add(TransactionLedgerLink(transaction_id=tx.id, ledger_id=op.id))
        self.session.flush()

    def test_uno_a_uno_quadra(self) -> None:
        tx, op = self._bonifico("100.00"), self._operazione("100.00")
        self._collega(tx, op)
        self.assertTrue(main.quadratura_gruppo(self.session, tx.id)["balanced"])

    def test_due_bonifici_su_una_operazione(self) -> None:
        """Il caso che ha fatto nascere il calcolo di gruppo: 100 + 3,16 = 103,16.
        Preso uno alla volta nessuno dei due pareggia; insieme si'."""
        grande, piccolo = self._bonifico("100.00"), self._bonifico("3.16")
        op = self._operazione("103.16")
        self._collega(grande, op)
        self._collega(piccolo, op)
        for tx in (grande, piccolo):
            esito = main.quadratura_gruppo(self.session, tx.id)
            self.assertTrue(esito["balanced"], msg=str(esito))
            self.assertEqual(2, esito["transactionCount"])

    def test_un_bonifico_su_piu_operazioni(self) -> None:
        tx = self._bonifico("300.00")
        for importo in ("100.00", "150.00", "50.00"):
            self._collega(tx, self._operazione(importo))
        self.assertTrue(main.quadratura_gruppo(self.session, tx.id)["balanced"])

    def test_il_prelievo_ha_il_verso_opposto(self) -> None:
        """Vendendo i soldi escono dal broker: il netto dev'essere negativo.
        Confrontarlo col bonifico positivo dava uno scarto del doppio."""
        tx = self._bonifico("80.00", da="Broker", a="Banca")
        self._collega(tx, self._operazione("80.00", tipo="Sell"))
        esito = main.quadratura_gruppo(self.session, tx.id)
        self.assertEqual(-80.0, esito["transfers"])
        self.assertEqual(-80.0, esito["operations"])
        self.assertTrue(esito["balanced"])

    def test_un_gruppo_che_non_torna_viene_rifiutato(self) -> None:
        tx = self._bonifico("100.00")
        op = self._operazione("70.00")
        self.session.commit()
        with self.assertRaises(HTTPException) as errore:
            main.link_existing_ledger_to_transaction(tx.id, {"ledger_id": op.id}, self.session)
        self.assertEqual("ledgerGroupUnbalanced", errore.exception.detail["code"])
        self.assertEqual(-30.0, errore.exception.detail["difference"])

    def test_un_movimento_senza_operazioni_non_deve_pareggiare(self) -> None:
        # Un Investment appena creato non ha ancora niente da confrontare:
        # pretendere la quadratura qui vieterebbe di crearlo.
        tx = self._bonifico("100.00")
        self.session.commit()
        esito = main.quadratura_gruppo(self.session, tx.id)
        self.assertEqual(0, esito["operationCount"])
        self.assertFalse(esito["balanced"])


if __name__ == "__main__":
    unittest.main()
