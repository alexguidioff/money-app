"""La serie di saldi deve dare gli stessi numeri del calcolo mese per mese.

E' l'unica cosa che rende sicura l'ottimizzazione: la versione incrementale
scorre i movimenti una volta sola, e va bene solo se il risultato non cambia.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from app.calculation_engine import account_balances_at, account_balances_series


class Conto:
    def __init__(self, id, name, source_group, starting_balance):
        self.id, self.name, self.source_group = id, name, source_group
        self.starting_balance = Decimal(starting_balance)


def _mov(giorno: date, tipo: str, importo: str, conto: str | None, destinazione: str | None = None):
    return {"effective_on": giorno, "occurred_on": giorno, "transaction_type": tipo,
            "amount": Decimal(importo), "account_name": conto, "destination_name": destinazione}


class SerieEquivalenteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conti = [Conto(1, "Conto", "bank", "1000"), Conto(2, "Mutuo", "liability", "0"),
                      Conto(3, "Casa", "asset", "200000")]
        self.movimenti = [
            _mov(date(2026, 1, 10), "Income", "2000", "Conto"),
            _mov(date(2026, 2, 3), "Expenses", "500", "Conto"),
            _mov(date(2026, 2, 28), "Transfers", "300", "Conto", "Mutuo"),
            _mov(date(2026, 4, 1), "Expenses", "120", "Conto"),
            # Senza data: il calcolo per data lo ignora, e anche la serie deve
            # ignorarlo.
            {"effective_on": None, "occurred_on": None, "transaction_type": "Expenses",
             "amount": Decimal("999"), "account_name": "Conto", "destination_name": None},
        ]
        self.date = [date(2026, mese, 28) for mese in range(1, 7)]

    def test_stessi_saldi_del_calcolo_mese_per_mese(self) -> None:
        serie = account_balances_series(self.conti, self.movimenti, self.date)
        for cutoff, calcolato in zip(self.date, serie):
            self.assertEqual(calcolato, account_balances_at(self.conti, self.movimenti, cutoff),
                             msg=str(cutoff))

    def test_regge_anche_con_le_date_in_disordine(self) -> None:
        disordinate = [self.date[3], self.date[0], self.date[5], self.date[1]]
        serie = account_balances_series(self.conti, self.movimenti, disordinate)
        for cutoff, calcolato in zip(disordinate, serie):
            self.assertEqual(calcolato, account_balances_at(self.conti, self.movimenti, cutoff),
                             msg=str(cutoff))

    def test_nessuna_data_nessun_saldo_mosso(self) -> None:
        self.assertEqual(account_balances_series(self.conti, self.movimenti, []), [])

    def test_la_competenza_non_sposta_il_saldo(self) -> None:
        """Incassato il 29, con competenza al primo del mese dopo.

        Il saldo e' denaro che c'e' o non c'e': il 30 settembre quei 2000 sono
        sul conto, anche se il budget li conta a ottobre. Vale per il calcolo a
        una data e per la serie, che devono restare d'accordo.
        """
        movimento = {"effective_on": date(2026, 10, 1), "occurred_on": date(2026, 9, 29),
                     "transaction_type": "Income", "amount": Decimal("2000"),
                     "account_name": "Conto", "destination_name": None}
        conti = [Conto(1, "Conto", "bank", "1000")]
        saldo = account_balances_at(conti, [movimento], date(2026, 9, 30))["accounts"][0]["balance"]
        self.assertEqual(3000.0, saldo)
        serie = account_balances_series(conti, [movimento], [date(2026, 9, 30), date(2026, 10, 31)])
        self.assertEqual([3000.0, 3000.0], [riga["accounts"][0]["balance"] for riga in serie])


if __name__ == '__main__':
    unittest.main()
