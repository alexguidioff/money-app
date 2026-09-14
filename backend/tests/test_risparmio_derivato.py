"""Il risparmio nel grafico "Budget vs tracciato" si deriva, non si somma.

Il tipo di movimento `Savings` non esiste piu': il risparmio e' entrate meno
spese. Sommando i movimenti di quel tipo la barra del tracciato restava a zero
per tutti e dodici i mesi, e accanto alla barra del budget non c'era niente.

Il test controlla anche che il numero sia lo STESSO della pagina Budget: erano
due strade allo stesso risparmio, ed e' il modo in cui questo progetto si e'
gia' trovato con due cifre diverse per la stessa cosa.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import analysis, budget_annual
from app.database import Base
from app.models import (AppSetting, BudgetPlan, InvestmentInstrument, InvestmentTransaction,
                        MarketPrice, Transaction)

TABELLE = [Transaction.__table__, BudgetPlan.__table__, AppSetting.__table__,
           InvestmentTransaction.__table__, MarketPrice.__table__, InvestmentInstrument.__table__]


class RisparmioDerivatoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        self._movimento("Income", "Stipendio", "2000")
        self._movimento("Expenses", "Casa", "1200")
        self.session.add(BudgetPlan(period=date(2026, 1, 1), budget_type="Savings",
                                    category="Savings", amount=Decimal("500")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _movimento(self, tipo: str, categoria: str, importo: str) -> None:
        self.session.add(Transaction(occurred_on=date(2026, 1, 15), effective_on=date(2026, 1, 15),
                                     transaction_type=tipo, category=categoria,
                                     amount=Decimal(importo), account_name="Conto"))

    def _analisi(self) -> dict:
        # Il "budget contro tracciato" dell'anno vive solo in Andamento annuale:
        # la copia in Panoramica e' stata tolta, la regola si controlla qui.
        return analysis(2026, "Expenses", None, self.session)

    def test_il_tracciato_e_entrate_meno_spese(self) -> None:
        self.assertEqual(800.0, self._analisi()["savingsByMonth"][0]["amount"])

    def test_dice_lo_stesso_della_pagina_budget(self) -> None:
        atteso = budget_annual(2026, "Savings", self.session)["monthTotals"][0]["actual"]
        self.assertEqual(atteso, self._analisi()["savingsByMonth"][0]["amount"])

    def test_un_mese_senza_movimenti_resta_a_zero(self) -> None:
        self.assertEqual(0.0, self._analisi()["savingsByMonth"][5]["amount"])

    def test_anche_il_grafico_impilato_deriva_il_risparmio(self) -> None:
        """L'altro grafico mensile ("Budget vs tracked by month") sommava anche
        lui i movimenti di tipo Savings: `inBudget` restava a zero e la barra
        era tutta grigia, cioe' "budget non ancora usato" per dodici mesi."""
        gennaio = analysis(2026, "Expenses", None, self.session)["monthlyBudget"]["savings"][0]
        # 800 risparmiati contro 500 pianificati: 500 dentro il budget, 300 oltre.
        self.assertEqual(500.0, gennaio["inBudget"])
        self.assertEqual(0, gennaio["remaining"])
        self.assertEqual(300.0, gennaio["excess"])

    def test_entrate_e_spese_nel_grafico_impilato_restano_somme(self) -> None:
        # La deroga vale solo per il risparmio: gli altri due tipi si sommano.
        mesi = analysis(2026, "Expenses", None, self.session)["monthlyBudget"]
        self.assertEqual(1200.0, mesi["expenses"][0]["inBudget"] + mesi["expenses"][0]["excess"])
        self.assertEqual(2000.0, mesi["income"][0]["inBudget"] + mesi["income"][0]["excess"])


if __name__ == "__main__":
    unittest.main()
