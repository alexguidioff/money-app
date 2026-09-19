"""Il seme dei test di contratto deve restare utile, non solo funzionante.

`tests/contratti.py` gira nel gate di deploy; qui si controlla che i dati con
cui lavora coprano davvero i casi: un elenco vuoto nelle risposte e' compatibile
con qualunque tipo del frontend, e il contratto non vedrebbe piu' niente.
"""

from __future__ import annotations

import unittest

from tests.contratti import richieste, risposte


class SemeDeiContrattiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.risposte = risposte()

    def test_ogni_endpoint_risponde(self) -> None:
        self.assertEqual({"fire", "fireProfile", "fireStreams", "fireExpenseRules", "firePensionShift",
                          "liabilities", "settings", "netWorth", "accounts", "transactions",
                          "summary", "summaryBreakdownMonth", "summaryBreakdownYear", "analysis", "budgets",
                          "calculations", "budgetAnnual", "budgetDashboardMonth", "budgetDashboardYear",
                          "budgetTrends", "budgetSuggestions", "goals", "investmentsDashboard", "investmentsLedger",
                          "investmentsAllocation", "instrumentHistory", "balanceSheetSeries", "notes", "recurring",
                          "categorizationRules", "categorizationSuggestions", "notifications", "backups",
                          "events", "eventDetail"},
                         set(self.risposte))

    def test_gli_elenchi_non_sono_vuoti(self) -> None:
        r = self.risposte
        for nome, elenco in {
            "serie del piano": r["fire"]["plan"]["series"], "leva": r["fire"]["plan"]["leverage"],
            "fasi con pensione": [f for f in r["fire"]["plan"]["phases"] if f["kind"] == "pensione"],
            "flussi": r["fireStreams"]["streams"], "categorie in pensione": r["fireExpenseRules"]["categories"],
            "prestiti": [i for i in r["liabilities"]["items"] if i["kind"] == "term_loan"],
            "linee di credito": [i for i in r["liabilities"]["items"] if i["kind"] == "credit_line"],
            "conti": r["accounts"]["items"], "movimenti": r["transactions"]["items"],
            "valute del patrimonio": r["netWorth"]["currencies"],
            "voci di budget": r["budgets"]["items"], "obiettivi": r["goals"]["items"],
            "operazioni del ledger": r["investmentsLedger"]["items"], "appunti": r["notes"]["items"],
            "ricorrenze": r["recurring"], "backup": r["backups"]["items"],
            "categorie dell'analisi": r["analysis"]["categoryOptions"],
            "transazioni dell'analisi": r["analysis"]["categoryTransactions"],
        }.items():
            self.assertTrue(elenco, f"{nome}: vuoto, il contratto non lo controllerebbe")

    def test_i_campi_facoltativi_compaiono_valorizzati(self) -> None:
        profilo = self.risposte["fireProfile"]
        self.assertIsNotNone(profilo["leanAnnualExpenses"])
        self.assertTrue(any(f["amountIfStoppingNow"] is not None for f in self.risposte["fireStreams"]["streams"]))

    def test_un_corpo_rifiutato_viene_riportato(self) -> None:
        errori = richieste([{"endpoint": "setting", "case": "vuoto", "body": {"valore": "x"}}])
        self.assertTrue(any("valore" in e for e in errori), errori)
