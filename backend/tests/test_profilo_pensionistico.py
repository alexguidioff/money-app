"""Le fondamenta della pagina FIRE: profilo e flussi di reddito.

Due cose vanno presidiate qui, e nessuna delle due e' ovvia.

La prima sono i **valori predefiniti**: un rendimento atteso o un tasso di
prelievo sono ipotesi con cui qualcuno pianifichera' vent'anni di vita, non
dettagli di implementazione. Se un giorno cambiano deve essere una decisione,
non un refuso, e questo test la rende visibile.

La seconda e' che i redditi sono un **elenco**, non un campo. Una carriera
divisa fra due paesi produce due pensioni pro-rata a eta' diverse; il secondo
pilastro svizzero e il TFR sono capitali una tantum. Un modello con "la
pensione" al singolare non reggerebbe nessuno di questi casi.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import TABELLE_PERSONALI
from app.database import Base
from app.migrations import PER_UTENTE
from app.models import IncomeStream, RetirementProfile


class FondamentaPensioneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_le_ipotesi_predefinite_sono_quelle_dichiarate(self) -> None:
        self.session.add(RetirementProfile(birth_year=1998))
        self.session.commit()
        profilo = self.session.scalars(select(RetirementProfile)).one()
        self.assertEqual(
            (Decimal("4"), Decimal("4"), Decimal("0"), "IT", "average"),
            (profilo.real_return, profilo.withdrawal_rate, profilo.withdrawal_tax_rate,
             profilo.country, profilo.expense_basis))

    def test_i_redditi_convivono_a_eta_diverse(self) -> None:
        # Il caso vero: italiano che lavora in Svizzera. Due pro-rata a eta'
        # diverse, piu' due capitali una tantum.
        self.session.add_all([
            IncomeStream(name="INPS pro-rata", kind="annuity", amount=Decimal("7000"), start_age=67),
            IncomeStream(name="AVS", kind="annuity", amount=Decimal("12000"), start_age=65),
            IncomeStream(name="LPP 2° pilastro", kind="capital", amount=Decimal("180000"), start_age=65),
            IncomeStream(name="TFR", kind="capital", amount=Decimal("9000"), start_age=50, indexed=False),
        ])
        self.session.commit()
        flussi = self.session.scalars(select(IncomeStream).order_by(IncomeStream.start_age)).all()
        self.assertEqual([50, 65, 65, 67], [f.start_age for f in flussi])
        self.assertEqual({"annuity", "capital"}, {f.kind for f in flussi})
        # Un capitale non indicizzato deve poter dichiarare di non esserlo:
        # in termini reali si svaluta, ed e' una differenza che pesa.
        self.assertFalse(next(f for f in flussi if f.name == "TFR").indexed)
        self.assertTrue(next(f for f in flussi if f.name == "AVS").indexed)

    def test_le_tabelle_nuove_sono_dichiarate_per_utente(self) -> None:
        # Senza l'isolamento, i dati previdenziali di una persona sarebbero
        # visibili a un'altra; senza lo svuotamento, resterebbero dopo che ha
        # cancellato l'account. I due elenchi devono contenerle entrambe.
        for tabella in ("retirement_profiles", "income_streams"):
            self.assertIn(tabella, PER_UTENTE, f"{tabella}: manca dall'isolamento")
            self.assertIn(tabella, TABELLE_PERSONALI, f"{tabella}: non verrebbe svuotata")
