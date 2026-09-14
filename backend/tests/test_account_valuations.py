"""Le valutazioni manuali dei conti che non hanno un prezzo.

Una casa non vale la somma dei bonifici che ci sono passati sopra, e alzare il
suo `starting_balance` quando rivaluti riscrive anche il patrimonio degli anni
in cui quel valore non lo conoscevi. Una valutazione ha invece una data, quindi
vale da quel giorno in avanti e non prima: e' l'unica cosa che questi test
sorvegliano davvero.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.calculation_engine import account_balances_series, valutazione_al
from app.core_routes import valutazioni_per_conto
from app.database import Base
from app.models import (Account, AccountValuation, InvestmentTransaction, Transaction,
                        TransactionLedgerLink)


class Conto:
    def __init__(self, **campi):
        base = {"id": 1, "name": "Casa", "source_group": "asset", "starting_balance": Decimal("100000"),
                "needs_manual_valuation": True}
        self.__dict__.update({**base, **campi})


def _saldo(risultato) -> float:
    return risultato["accounts"][0]["balance"]


class ValutazioneAlTests(unittest.TestCase):
    STORICO = [(date(2024, 6, 1), Decimal("210000")), (date(2025, 6, 1), Decimal("225000"))]

    def test_prende_l_ultima_non_successiva(self) -> None:
        self.assertEqual(Decimal("210000"), valutazione_al(self.STORICO, date(2024, 12, 31)))
        self.assertEqual(Decimal("225000"), valutazione_al(self.STORICO, date(2026, 1, 1)))

    def test_prima_della_prima_non_inventa_niente(self) -> None:
        # Retrodatare la stima di oggi cambierebbe il patrimonio di anni in cui
        # quel valore non si conosceva: meglio nessuna risposta.
        self.assertIsNone(valutazione_al(self.STORICO, date(2024, 1, 1)))

    def test_senza_storico_o_senza_data(self) -> None:
        self.assertIsNone(valutazione_al([], date(2026, 1, 1)))
        self.assertIsNone(valutazione_al(self.STORICO, None))


class PatrimonioConValutazioniTests(unittest.TestCase):
    TAGLI = [date(2024, 12, 31), date(2025, 12, 31), date(2026, 12, 31)]
    STIME = {1: [(date(2024, 6, 1), Decimal("210000")), (date(2025, 6, 1), Decimal("225000"))]}

    def test_il_mese_intermedio_usa_la_prima_stima(self) -> None:
        # Il punto di tutta la funzione: aggiornare il valore della casa nel 2025
        # non deve muovere il patrimonio del 2024.
        serie = account_balances_series([Conto()], [], self.TAGLI, self.STIME)
        self.assertEqual([210000.0, 225000.0, 225000.0], [_saldo(r) for r in serie])

    def test_senza_il_flag_la_stima_non_viene_nemmeno_prodotta(self) -> None:
        """Chi ha diritto a un valore lo decide `valutazioni_per_conto`, non il
        motore dei saldi: quello applica quel che riceve e basta.

        La decisione stava in due posti - il flag letto qui e i conti scelti la'
        - e due posti che decidono la stessa cosa e' come non deciderla. Il test
        segue la decisione dove e' andata invece di sparire con lei."""
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[
            Account.__table__, AccountValuation.__table__, Transaction.__table__,
            InvestmentTransaction.__table__, TransactionLedgerLink.__table__])
        with Session(engine) as sessione:
            conto = Account(source_group="asset", name="Casa", starting_balance=Decimal("100000"),
                            current_balance=Decimal("100000"), needs_manual_valuation=False)
            sessione.add(conto)
            sessione.flush()
            sessione.add(AccountValuation(account_id=conto.id, observed_on=date(2024, 6, 1),
                                          value=Decimal("210000")))
            sessione.commit()
            self.assertNotIn(conto.id, valutazioni_per_conto(sessione))
            conto.needs_manual_valuation = True
            sessione.commit()
            self.assertIn(conto.id, valutazioni_per_conto(sessione))

    def test_senza_stime_si_ricade_sul_calcolo_normale(self) -> None:
        serie = account_balances_series([Conto()], [], self.TAGLI, {})
        self.assertEqual([100000.0, 100000.0, 100000.0], [_saldo(r) for r in serie])

    def test_una_passivita_valutata_resta_negativa(self) -> None:
        # Le liability escono col segno girato: un mutuo stimato non deve far
        # salire il patrimonio.
        serie = account_balances_series(
            [Conto(source_group="liability")], [], [date(2026, 12, 31)], self.STIME)
        self.assertEqual(-225000.0, _saldo(serie[0]))


if __name__ == "__main__":
    unittest.main()
