"""Ritmo, stato e fonte del valore di un goal.

Le tre regole che trasformano un goal da "desiderio con una data" in qualcosa
che dice cosa fare adesso. La quarta cosa che verificano e' che i goal gia'
esistenti non cambino numero: `contributions` deve restare identico a prima.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest import mock
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import core_routes
from app.core_routes import _ritmo_goal, _stato_goal, goals
from app.database import Base
from app.models import Goal, Transaction
from tests.categorie_fixture import categoria


def _tx_goal(obiettivo: str, importo: str, conto: str, destinazione: str | None = None) -> Transaction:
    # Un versamento su un goal e' un giroconto, e i giroconti non hanno
    # categoria: l'id mancante e' quello che prima si scriveva "_".
    return Transaction(occurred_on=date(2026, 2, 4), effective_on=date(2026, 2, 4),
                       transaction_type="Transfers", category_id=None, amount=Decimal(importo),
                       account_type="Bank", account_name=conto, destination_name=destinazione,
                       goal=obiettivo, is_recurring_template=False)


def _goal(**kwargs) -> Goal:
    base = {"name": "Obiettivo", "starting_amount": Decimal("0"), "target_amount": Decimal("10000")}
    return Goal(**{**base, **kwargs})


class RitmoTests(unittest.TestCase):
    OGGI = date(2026, 1, 1)

    def test_quanto_serve_al_mese(self) -> None:
        ritmo = _ritmo_goal(2000, 10000, date(2026, 9, 1), False, self.OGGI)
        self.assertEqual(ritmo["monthsLeft"], 8)
        self.assertEqual(ritmo["monthlyNeeded"], 1000.0)
        self.assertEqual(ritmo["weeklyNeeded"], round(1000 * 12 / 52, 2))

    def test_senza_scadenza_nessun_ritmo(self) -> None:
        self.assertIsNone(_ritmo_goal(2000, 10000, None, False, self.OGGI)["monthlyNeeded"])

    def test_scaduto_dice_scaduto_non_un_ritmo(self) -> None:
        ritmo = _ritmo_goal(2000, 10000, date(2025, 12, 1), False, self.OGGI)
        self.assertIsNone(ritmo["monthlyNeeded"])
        self.assertTrue(ritmo["overdue"])

    def test_completato_nessun_ritmo(self) -> None:
        self.assertIsNone(_ritmo_goal(2000, 10000, date(2026, 9, 1), True, self.OGGI)["monthlyNeeded"])

    def test_traguardo_gia_raggiunto(self) -> None:
        self.assertIsNone(_ritmo_goal(12000, 10000, date(2026, 9, 1), False, self.OGGI)["monthlyNeeded"])

    def test_scadenza_nello_stesso_mese_non_divide_per_zero(self) -> None:
        ritmo = _ritmo_goal(9000, 10000, date(2026, 1, 20), False, self.OGGI)
        self.assertEqual(ritmo["monthsLeft"], 1)
        self.assertEqual(ritmo["monthlyNeeded"], 1000.0)


class StatoTests(unittest.TestCase):
    INIZIO, FINE = date(2026, 1, 1), date(2026, 12, 31)
    META = date(2026, 7, 1)  # ~metà del percorso

    def _stato(self, corrente: float) -> str | None:
        return _stato_goal(corrente, 0, 10000, self.INIZIO, self.FINE, False, self.META)["status"]

    def test_meta_tempo_meta_soldi_e_in_linea(self) -> None:
        self.assertEqual(self._stato(5000), "on_track")

    def test_poco_sotto_e_leggero_ritardo(self) -> None:
        self.assertEqual(self._stato(4500), "slightly_behind")

    def test_molto_sotto_e_ritardo(self) -> None:
        self.assertEqual(self._stato(3000), "behind")

    def test_senza_date_nessuno_stato(self) -> None:
        self.assertIsNone(_stato_goal(5000, 0, 10000, None, self.FINE, False, self.META)["status"])
        self.assertIsNone(_stato_goal(5000, 0, 10000, self.INIZIO, None, False, self.META)["status"])

    def test_completato(self) -> None:
        self.assertEqual(_stato_goal(10000, 0, 10000, self.INIZIO, self.FINE, True, self.META)["status"], "completed")


class FonteDelValoreTests(unittest.TestCase):
    """Il tipo del goal decide da dove arriva il valore corrente."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            _goal(name="Versamenti", starting_amount=Decimal("1000"), kind="contributions"),
            _goal(name="Portafoglio", starting_amount=Decimal("1000"), kind="portfolio"),
            Transaction(occurred_on=date(2026, 1, 5), effective_on=date(2026, 1, 5),
                        transaction_type="Savings", category_id=categoria(self.session, "Savings"),
                        amount=Decimal("500"),
                        account_type="Bank", account_name="Conto", goal="Versamenti",
                        is_recurring_template=False),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _corrente(self, nome: str) -> float:
        return next(v for v in goals(self.session)["items"] if v["name"] == nome)["currentAmount"]

    def test_contributions_resta_come_prima(self) -> None:
        self.assertEqual(self._corrente("Versamenti"), 1500.0)

    def test_senza_conto_indicato_somma_e_basta(self) -> None:
        # Comportamento storico: chi non dichiara il conto non vede cambiare
        # nessun numero. Il prelievo qui sotto si somma, come sempre.
        self.session.add(_tx_goal("Versamenti", "200", conto="Salvadanaio"))
        self.session.commit()
        self.assertEqual(self._corrente("Versamenti"), 1700.0)

    def test_col_conto_indicato_un_prelievo_sottrae(self) -> None:
        goal = self.session.scalars(select(Goal).where(Goal.name == "Versamenti")).one()
        goal.target_account = "Salvadanaio"
        # Il versamento arriva sul salvadanaio: somma.
        self.session.add(_tx_goal("Versamenti", "200", conto="Conto", destinazione="Salvadanaio"))
        # Questo ne esce: sottrae, invece di gonfiare il goal.
        self.session.add(_tx_goal("Versamenti", "300", conto="Salvadanaio", destinazione="Conto"))
        self.session.commit()
        # 1000 iniziali + 500 (non tocca il salvadanaio, si somma) + 200 - 300
        self.assertEqual(self._corrente("Versamenti"), 1400.0)

    def test_lo_storico_segue_la_stessa_regola_del_totale(self) -> None:
        goal = self.session.scalars(select(Goal).where(Goal.name == "Versamenti")).one()
        goal.target_account = "Salvadanaio"
        self.session.add(_tx_goal("Versamenti", "300", conto="Salvadanaio", destinazione="Conto"))
        self.session.commit()
        # Un grafico che contraddice il numero sopra e' peggio di nessun grafico.
        voce = next(v for v in goals(self.session)["items"] if v["name"] == "Versamenti")
        self.assertEqual(voce["history"][-1]["amount"], voce["currentAmount"])

    def test_portfolio_ignora_i_movimenti_taggati_e_starting_amount(self) -> None:
        # Senza ledger investimenti il portafoglio vale zero: il punto e' che
        # non somma i 1000 iniziali, che sarebbero contati due volte.
        voce = next(v for v in goals(self.session)["items"] if v["name"] == "Portafoglio")
        self.assertEqual(voce["currentAmount"], 0.0)


class QuadraturaGoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_somma_solo_i_goal_attivi_con_una_scadenza(self) -> None:
        fra_dieci_mesi = date.today().replace(day=1) + timedelta(days=305)
        self.session.add_all([
            _goal(name="Con scadenza", target_amount=Decimal("1000"), target_date=fra_dieci_mesi),
            _goal(name="Senza scadenza", target_amount=Decimal("9999")),
            _goal(name="Completato", target_amount=Decimal("9999"), target_date=fra_dieci_mesi,
                  completed_at=date.today()),
        ])
        self.session.commit()
        risposta = goals(self.session)
        atteso = next(v for v in risposta["items"] if v["name"] == "Con scadenza")["monthlyNeeded"]
        self.assertEqual(risposta["monthlyNeededTotal"], atteso)


if __name__ == '__main__':
    unittest.main()


class SemaforoControlloTests(unittest.TestCase):
    """Il semaforo della Panoramica: due percentuali e un conteggio, niente di piu'."""

    def _livello(self, speso: float, tempo: float, sforate: int) -> str:
        from app.core_routes import _stato_controllo
        return _stato_controllo(speso, tempo, sforate)["level"]

    def test_sotto_il_ritmo_e_verde(self) -> None:
        self.assertEqual(self._livello(40, 50, 0), "ok")

    def test_appena_sopra_il_ritmo_resta_verde(self) -> None:
        # Cinque punti di margine: un giorno di spesa non deve far scattare un allarme.
        self.assertEqual(self._livello(54, 50, 0), "ok")

    def test_sopra_il_ritmo_e_ambra(self) -> None:
        self.assertEqual(self._livello(70, 50, 0), "watch")

    def test_tre_categorie_sforate_bastano(self) -> None:
        self.assertEqual(self._livello(40, 50, 3), "watch")

    def test_budget_finito_e_rosso(self) -> None:
        self.assertEqual(self._livello(110, 100, 0), "over")


class StimaFinePeriodoTests(unittest.TestCase):
    """La stima non moltiplica mai per i giorni passati."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.oggi = date(2026, 7, 6)   # sei giorni su trentuno
        # Sei mesi di affitto sempre uguale, e l'affitto di luglio gia' pagato.
        for mese in range(1, 8):
            self.session.add(Transaction(
                occurred_on=date(2026, mese, 1), effective_on=date(2026, mese, 1),
                transaction_type="Expenses", category_id=categoria(self.session, "Affitto"),
                amount=Decimal("1000"),
                account_type="Bank", account_name="Conto", is_recurring_template=False))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_l_affitto_pagato_il_primo_non_diventa_trentamila(self) -> None:
        from app.core_routes import stima_fine_periodo
        stima = stima_fine_periodo(self.session, 2026, 7, self.oggi)
        # Proiettando sui giorni passati verrebbero 1000/6*31 = 5166.
        self.assertEqual(stima["estimate"], 1000.0)
        self.assertEqual(stima["spentSoFar"], 1000.0)

    def test_una_categoria_sopra_la_solita_media_vale_quanto_ha_speso(self) -> None:
        from app.core_routes import stima_fine_periodo
        self.session.add(Transaction(
            occurred_on=date(2026, 7, 3), effective_on=date(2026, 7, 3),
            transaction_type="Expenses", category_id=categoria(self.session, "Affitto"),
            amount=Decimal("500"),
            account_type="Bank", account_name="Conto", is_recurring_template=False))
        self.session.commit()
        self.assertEqual(stima_fine_periodo(self.session, 2026, 7, self.oggi)["estimate"], 1500.0)

    def test_un_mese_chiuso_dice_che_non_c_e_niente_da_stimare(self) -> None:
        from app.core_routes import stima_fine_periodo
        chiuso = stima_fine_periodo(self.session, 2026, 6, self.oggi)
        self.assertEqual(chiuso["state"], "closed")
        self.assertIsNone(chiuso["estimate"])
        self.assertEqual(chiuso["spentSoFar"], 1000.0)

    def test_un_mese_futuro_si_stima_sulla_storia_fino_a_oggi(self) -> None:
        # La finestra parte da oggi: ancorandola a dicembre ci finirebbero
        # dentro i mesi vuoti da qui a la', e la mediana crollerebbe.
        from app.core_routes import stima_fine_periodo
        futuro = stima_fine_periodo(self.session, 2026, 12, self.oggi)
        self.assertEqual(futuro["state"], "future")
        self.assertEqual(futuro["estimate"], 1000.0)


class StoricoPatrimonialeTests(unittest.TestCase):
    """Le serie patrimoniali si calcolano una volta, non una per goal.

    Portafoglio, conti, movimenti e valutazioni non dipendono dal singolo
    goal: rifarli dentro il ciclo voleva dire ricostruire l'intera linea del
    portafoglio e la serie dei saldi per ogni obiettivo patrimoniale. E
    `portfolio_state_at` veniva interrogato due volte per mese, una per il
    test di verita' e una per leggere il valore.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _mesi_attesi(self, inizio: date) -> int:
        oggi = date.today()
        return (oggi.year - inizio.year) * 12 + (oggi.month - inizio.month) + 1

    def test_le_basi_si_calcolano_una_volta_per_richiesta(self) -> None:
        inizio = date.today().replace(day=1) - timedelta(days=70)
        self.session.add_all([
            _goal(name="Patrimonio", kind="net_worth", start_date=inizio),
            _goal(name="Portafoglio", kind="portfolio", start_date=inizio),
        ])
        self.session.commit()
        with mock.patch("app.core_routes.portfolio_timeline", wraps=core_routes.portfolio_timeline) as linea:
            dati = goals(self.session)
        self.assertEqual(linea.call_count, 1, "la linea del portafoglio va calcolata una volta sola")
        self.assertEqual(len(dati["items"]), 2)
        # Il ramo deve essere girato davvero, non saltato: se la storia fosse
        # vuota il test passerebbe senza aver provato niente.
        for voce in dati["items"]:
            self.assertTrue(voce["history"], f"storia vuota per {voce['name']}")

    def test_lo_stato_del_portafoglio_si_chiede_una_volta_per_mese(self) -> None:
        inizio = date.today().replace(day=1) - timedelta(days=70)
        self.session.add(_goal(name="Portafoglio", kind="portfolio", start_date=inizio))
        self.session.commit()
        with mock.patch("app.core_routes.portfolio_state_at", wraps=core_routes.portfolio_state_at) as stato:
            goals(self.session)
        # Uno per mese nella storia, piu' uno per il valore di oggi. Col
        # doppio interrogatorio di prima sarebbero stati il doppio.
        self.assertEqual(stato.call_count, self._mesi_attesi(inizio.replace(day=1)) + 1)

    def test_senza_goal_patrimoniali_non_si_calcola_niente(self) -> None:
        self.session.add(_goal(name="Versamenti", kind="contributions"))
        self.session.commit()
        with mock.patch("app.core_routes.portfolio_timeline", wraps=core_routes.portfolio_timeline) as linea:
            goals(self.session)
        self.assertEqual(linea.call_count, 0, "nessun goal patrimoniale: niente da precalcolare")
