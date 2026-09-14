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
            # Senza data di competenza: il calcolo per data lo ignora, e anche
            # la serie deve ignorarlo.
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


if __name__ == '__main__':
    unittest.main()
