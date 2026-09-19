"""Un movimento incompleto che si accetta com'e' smette di chiedere attenzione.

Serve ai movimenti importati da fogli che il dato non ce l'avevano: senza, il
contatore li conta per sempre, e un badge che non si spegne mai insegna a
ignorare tutti i badge - compresi quelli su cui si potrebbe intervenire.

Cio' che questi test proteggono e' il confine: accettare cambia **chi viene
contato**, non cosa il movimento e'. Chi ne crea uno nuovo incompleto deve
continuare a ricevere un rifiuto.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Account, Transaction
from app.transaction_rules import (INCOMPLETE_MOVEMENT, REAL_MOVEMENT,
                                   missing_fields, validate_movement)
from tests.categorie_fixture import categoria


def _senza_conto(session: Session, accettato: bool = False) -> Transaction:
    # La categoria la nomina l'id: senza, il movimento risulterebbe incompleto
    # anche di categoria e il conto dei "da sistemare" cambierebbe per un
    # motivo che non c'entra con l'accettazione.
    return Transaction(occurred_on=date(2021, 5, 4), effective_on=date(2021, 5, 4),
                       transaction_type="Expenses", category_id=categoria(session, "Groceries"),
                       amount=Decimal("21"), account_name=None, is_recurring_template=False,
                       incomplete_accepted=accettato)


class IncompletiAccettatiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(source_group="bank", name="Conto",
                                 starting_balance=Decimal("0"), current_balance=Decimal("0")))

    def tearDown(self) -> None:
        self.session.close()

    def _da_sistemare(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Transaction)
                                   .where(REAL_MOVEMENT, INCOMPLETE_MOVEMENT))

    def test_accettato_non_viene_contato(self) -> None:
        self.session.add_all([_senza_conto(self.session), _senza_conto(self.session, accettato=True)])
        self.session.commit()
        self.assertEqual(1, self._da_sistemare())

    def test_resta_incompleto_davvero(self) -> None:
        # Accettarlo non gli mette un conto: se un giorno chiedi cosa manca, la
        # risposta e' ancora "il conto".
        self.assertEqual(["account"], missing_fields(_senza_conto(self.session, accettato=True)))

    def test_non_apre_la_porta_ai_nuovi(self) -> None:
        # Il flag non deve diventare un modo per salvare movimenti a meta': chi
        # ne crea uno nuovo riceve lo stesso rifiuto di prima.
        with self.assertRaises(HTTPException) as errore:
            validate_movement(self.session, _senza_conto(self.session, accettato=True))
        self.assertEqual(422, errore.exception.status_code)

    def test_si_torna_indietro(self) -> None:
        riga = _senza_conto(self.session, accettato=True)
        self.session.add(riga)
        self.session.commit()
        self.assertEqual(0, self._da_sistemare())
        riga.incomplete_accepted = False
        self.session.commit()
        self.assertEqual(1, self._da_sistemare())


if __name__ == "__main__":
    unittest.main()
