"""Una riga del registro porta il suo strumento, e lo strumento nasce con lei.

Le due schede si rimandavano l'una all'altra. La scheda Strumenti si costruisce
dalle **posizioni**, cioe' dalle righe del registro, e quando non ce n'erano
diceva "gli strumenti nascono dalle operazioni del registro"; il registro, con
l'anagrafica vuota, diceva di andare in Quotazioni. Ma il campo prometteva gia'
il contrario - "verra' creato con questo nome" - e il backend non lo faceva:
chi scriveva la sua prima operazione restava con una riga senza anagrafica,
quindi senza ticker e senza prezzo, e la scheda Strumenti continuava a mandarlo
al registro. Un giro chiuso a vuoto.

Qui si fissa che il giro si chiude da **tutte e due** le porte da cui una riga
di ledger nasce: l'operazione scritta a mano e il movimento con le sue righe
(che e' anche la strada dell'import di un elenco).
"""

from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.database import Base, reset_current_user, set_current_user
from app.models import InvestmentInstrument, InvestmentTransaction, Transaction

UTENTE = 7
TABELLE = [InvestmentTransaction.__table__, InvestmentInstrument.__table__]


def _operazione(nome: str) -> main.InvestmentTxPayload:
    return main.InvestmentTxPayload(name=nome, transaction_type="Buy", amount=100.0,
                                    occurred_on="2024-01-05", units=1.0, price=100.0)


class StrumentoDellaRigaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(reset_current_user, set_current_user(UTENTE))
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _strumenti(self) -> list[str]:
        return [riga.name for riga in
                self.session.query(InvestmentInstrument).order_by(InvestmentInstrument.name)]

    def test_un_operazione_nuova_crea_il_suo_strumento(self) -> None:
        # Il nome arriva con gli spazi di troppo, come dal campo di testo: in
        # anagrafica finisce quello pulito, che e' lo stesso che porta la riga.
        main._apply_investment_tx(InvestmentTransaction(), _operazione("  Fondo Alfa  "), self.session)
        self.session.commit()
        self.assertEqual(["Fondo Alfa"], self._strumenti())

    def test_lo_stesso_nome_non_ne_crea_un_secondo(self) -> None:
        for _ in range(2):
            main._apply_investment_tx(InvestmentTransaction(), _operazione("Fondo Alfa"), self.session)
        self.session.commit()
        self.assertEqual(["Fondo Alfa"], self._strumenti())

    def test_anche_le_righe_di_un_movimento_creano_lo_strumento(self) -> None:
        """L'altra porta: il movimento con le sue righe, e l'import di un elenco."""
        main._materialize_linked_ledger(
            self.session, Transaction(occurred_on=date(2024, 1, 5)),
            [main.LinkedLedgerRow(name="Fondo Beta", transaction_type="Buy", units=2.0,
                                  price=50.0, amount=100.0)])
        self.session.commit()
        self.assertEqual(["Fondo Beta"], self._strumenti())

    def test_lo_strumento_nasce_una_volta_sola(self) -> None:
        """Il legame fra strumento e righe e' per nome, e il nome e' unico:
        l'anagrafica e' una lista sola, come la legge la schermata Strumenti."""
        main._apply_investment_tx(InvestmentTransaction(), _operazione("Fondo Alfa"), self.session)
        main._apply_investment_tx(InvestmentTransaction(), _operazione("fondo alfa"), self.session)
        self.session.commit()
        # La ricerca ignora le maiuscole: due grafie non fanno due strumenti
        # con lo stesso ticker, che si contenderebbero la stessa quotazione.
        self.assertEqual(["Fondo Alfa"], self._strumenti())


if __name__ == "__main__":
    unittest.main()
