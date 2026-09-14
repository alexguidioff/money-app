"""Il ledger deve dire, riga per riga, se un'operazione e' gia' agganciata.

Non e' un dettaglio estetico: il collegamento decide a quale conto attribuire
il guadagno di quell'operazione, e finche' manca il conto mostra il costo senza
la rivalutazione. Sapere quali righe restano da collegare e' l'unico modo di
sapere quanto manca perche' il patrimonio torni giusto.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core_routes import investments_ledger
from app.database import Base
from app.models import InvestmentTransaction, InvestmentTransactionDetail, Transaction, TransactionLedgerLink

TABELLE = [InvestmentTransaction.__table__, InvestmentTransactionDetail.__table__,
           Transaction.__table__, TransactionLedgerLink.__table__]


class LedgerCollegamentiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        for nome in ("Alfa", "Beta", "Gamma"):
            self.session.add(InvestmentTransaction(name=nome, transaction_type="Buy",
                                                   occurred_on=date(2024, 1, 5), units=Decimal("1"),
                                                   amount=Decimal("100"), price=Decimal("100")))
        self.movimento = Transaction(occurred_on=date(2024, 1, 5), effective_on=date(2024, 1, 5),
                                     transaction_type="Transfers", category="", amount=Decimal("100"),
                                     account_name="Conto", destination_name="Titoli")
        self.session.add(self.movimento)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _righe(self) -> dict[str, bool]:
        return {voce["name"]: voce["linked"] for voce in investments_ledger(self.session)["items"]}

    def test_senza_collegamenti_nessuna_risulta_agganciata(self) -> None:
        self.assertEqual({"Alfa": False, "Beta": False, "Gamma": False}, self._righe())

    def test_solo_quella_collegata_risulta_agganciata(self) -> None:
        alfa = next(r for r in self.session.query(InvestmentTransaction) if r.name == "Alfa")
        self.session.add(TransactionLedgerLink(transaction_id=self.movimento.id, ledger_id=alfa.id))
        self.session.commit()
        self.assertEqual({"Alfa": True, "Beta": False, "Gamma": False}, self._righe())

    def test_due_movimenti_sulla_stessa_operazione_non_la_duplicano(self) -> None:
        """Un acquisto puo' essere finanziato da due bonifici: resta una riga
        sola, agganciata. Contare i collegamenti invece delle operazioni
        raddoppierebbe la riga nell'elenco."""
        alfa = next(r for r in self.session.query(InvestmentTransaction) if r.name == "Alfa")
        secondo = Transaction(occurred_on=date(2024, 1, 6), effective_on=date(2024, 1, 6),
                              transaction_type="Transfers", category="", amount=Decimal("50"),
                              account_name="Conto", destination_name="Titoli")
        self.session.add(secondo)
        self.session.flush()
        self.session.add(TransactionLedgerLink(transaction_id=self.movimento.id, ledger_id=alfa.id))
        self.session.add(TransactionLedgerLink(transaction_id=secondo.id, ledger_id=alfa.id))
        self.session.commit()
        righe = investments_ledger(self.session)["items"]
        self.assertEqual(3, len(righe))
        self.assertEqual(1, sum(1 for r in righe if r["linked"]))

    def test_elenco_restituisce_fee_e_note_salvate(self) -> None:
        alfa = next(r for r in self.session.query(InvestmentTransaction) if r.name == "Alfa")
        self.session.add(InvestmentTransactionDetail(transaction_id=alfa.id,
                                                     fee=Decimal("1.25"), notes="Commissione broker"))
        self.session.commit()
        riga = next(r for r in investments_ledger(self.session)["items"] if r["id"] == alfa.id)
        self.assertEqual(1.25, riga["fee"])
        self.assertEqual("Commissione broker", riga["notes"])

    def test_le_operazioni_vengono_lette_una_volta_sola(self) -> None:
        query_operazioni = 0

        def conta_query(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            nonlocal query_operazioni
            if "FROM investment_transactions" in statement:
                query_operazioni += 1

        event.listen(self.engine, "before_cursor_execute", conta_query)
        try:
            investments_ledger(self.session)
        finally:
            event.remove(self.engine, "before_cursor_execute", conta_query)
        self.assertEqual(1, query_operazioni)


if __name__ == "__main__":
    unittest.main()
