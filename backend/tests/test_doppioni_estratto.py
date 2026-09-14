"""Doppioni fra un estratto conto e i movimenti gia' registrati.

Casi presi da un estratto vero, con importi e nomi inventati: la descrizione
della banca non somiglia a quella scritta a mano, l'affitto si segna il primo
del mese e parte il cinque, una spesa divisa si registra in due movimenti.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.models import Transaction


class DoppioniEstrattoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _movimento(self, giorno: int, importo: str, tipo: str = "Expenses", dettagli: str | None = None,
                   conto: str = "Banca", destinazione: str | None = None) -> int:
        riga = Transaction(occurred_on=date(2026, 7, giorno), effective_on=date(2026, 7, giorno),
                           transaction_type=tipo, category="_" if destinazione else "Other",
                           amount=Decimal(importo), account_name=conto, destination_name=destinazione, details=dettagli)
        self.session.add(riga)
        self.session.commit()
        return riga.id

    def _anteprima(self, *righe: tuple[int, str, str, str]) -> list[dict]:
        return main.statement_preview([{"occurredOn": f"2026-07-{giorno:02d}", "amount": importo,
                                        "transactionType": tipo, "description": descrizione}
                                       for giorno, importo, tipo, descrizione in righe], self.session)["transactions"]

    def test_la_descrizione_diversa_non_nasconde_il_doppione(self) -> None:
        esistente = self._movimento(1, "915.00", dettagli="affitto luglio")
        riga, = self._anteprima((5, "915.00", "Expenses", "Outgoing transfer for Mario Rossi (IT00...)"))
        self.assertEqual(esistente, riga["duplicateOf"]["id"])

    def test_oltre_i_giorni_di_tolleranza_non_e_un_doppione(self) -> None:
        self._movimento(1, "915.00")
        riga, = self._anteprima((1 + main.GIORNI_DUPLICATO + 1, "915.00", "Expenses", "affitto"))
        self.assertFalse(riga["duplicate"])

    def test_un_entrata_non_e_il_doppione_di_una_spesa(self) -> None:
        self._movimento(3, "50.00", tipo="Expenses")
        riga, = self._anteprima((3, "50.00", "Income", "rimborso"))
        self.assertFalse(riga["duplicate"])

    def test_un_giroconto_vale_per_entrambi_i_versi(self) -> None:
        giroconto = self._movimento(7, "2800.00", tipo="Transfers", conto="Banca", destinazione="Broker")
        riga, = self._anteprima((9, "2800.00", "Income", "Incoming transfer"))
        self.assertEqual(giroconto, riga["duplicateOf"]["id"])

    def test_fra_piu_candidati_vince_la_descrizione_piu_simile(self) -> None:
        self._movimento(10, "21.96", dettagli="abbonamento musica")
        giusto = self._movimento(10, "21.96", dettagli="Claude sub")
        riga, = self._anteprima((10, "21.96", "Expenses", "ANTHROPIC* CLAUDE SUB"))
        self.assertEqual(giusto, riga["duplicateOf"]["id"])

    def test_due_spese_uguali_e_una_sola_registrata(self) -> None:
        self._movimento(11, "1.50", dettagli="caffe")
        prima, seconda = self._anteprima((11, "1.50", "Expenses", "BAR"), (11, "1.50", "Expenses", "BAR"))
        self.assertEqual((True, False), (prima["duplicate"], seconda["duplicate"]))

    def test_una_spesa_divisa_in_due_movimenti(self) -> None:
        self._movimento(26, "13.50", dettagli="bar")
        self._movimento(26, "13.50", tipo="Transfers", destinazione="Splitwise", dettagli="bar")
        riga, = self._anteprima((26, "27.00", "Expenses", "LS Drama bar"))
        self.assertTrue(riga["duplicate"])
        self.assertEqual(27.0, riga["duplicateOf"]["amount"])

    def test_la_coppia_deve_essere_dello_stesso_conto(self) -> None:
        self._movimento(26, "13.50", conto="Banca")
        self._movimento(26, "13.50", conto="Carta")
        riga, = self._anteprima((26, "27.00", "Expenses", "cena"))
        self.assertFalse(riga["duplicate"])


if __name__ == "__main__":
    unittest.main()
