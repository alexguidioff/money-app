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

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.calculation_engine import account_balances_series, valutazione_al
from app.core_routes import _avviso_valutazione, stato_valutazione, valutazioni_per_conto
from app.database import Base
from app.models import (Account, AccountValuation, InvestmentTransaction, Transaction,
                        TransactionLedgerLink)
from app.notifications import _valutazioni_scadute


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


class AvvisoValutazioneTests(unittest.TestCase):
    """Quando una stima e' vecchia, e chi ha diritto ad averne una.

    La stessa regola disegna l'avviso della campanella e la riga scritta sotto
    il conto nel Patrimonio: questi test la prendono da entrambe le parti, che e'
    l'unico modo di accorgersi se un giorno divergono.
    """

    # Una data fissa: il risultato non deve cambiare col calendario di chi lancia
    # i test, ed e' il motivo per cui la soglia si conta a giorni e non a "oggi".
    OGGI = date(2026, 9, 19)

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[Account.__table__, AccountValuation.__table__])
        self.session = Session(self.engine)
        self.creati = 0

    def tearDown(self) -> None:
        self.session.close()

    def conto(self, *stime: date, gruppo: str = "asset", manuale: bool = True,
              attivo: bool = True) -> Account:
        # Un nome diverso per conto: il conto e' unico per nome dentro il suo
        # gruppo, e due "Casa" nello stesso test non si potrebbero nemmeno
        # salvare.
        self.creati += 1
        conto = Account(source_group=gruppo, name=f"Casa {self.creati}",
                        starting_balance=Decimal("100000"),
                        current_balance=Decimal("100000"), needs_manual_valuation=manuale,
                        is_active=attivo)
        self.session.add(conto)
        self.session.flush()
        for stima in stime:
            self.session.add(AccountValuation(account_id=conto.id, observed_on=stima,
                                              value=Decimal("200000")))
        self.session.commit()
        return conto

    def avviso(self, conto: Account) -> dict | None:
        """La stessa entrata che riceve `/api/accounts`: le stime dalla piu' nuova."""
        stime = self.session.scalars(select(AccountValuation)
                                     .where(AccountValuation.account_id == conto.id)
                                     .order_by(AccountValuation.observed_on.desc())).all()
        return _avviso_valutazione(conto, list(stime), self.OGGI)

    def test_una_stima_di_un_anno_fa_e_giu_si_vede(self) -> None:
        conto = self.conto(date(2025, 9, 19))  # 365 giorni esatti: la soglia
        self.assertEqual("stale", stato_valutazione(conto, date(2025, 9, 19), self.OGGI))
        self.assertEqual({"code": "valuationStale", "days": 365}, self.avviso(conto))

    def test_una_stima_recente_non_dice_niente(self) -> None:
        conto = self.conto(date(2026, 9, 18))
        self.assertIsNone(stato_valutazione(conto, date(2026, 9, 18), self.OGGI))
        self.assertIsNone(self.avviso(conto))

    def test_senza_stime_il_motivo_e_che_non_ce_ne_sono(self) -> None:
        # Il caso "valore o motivo": non c'e' un'eta' da mostrare, e non si
        # inventa uno zero al posto suo.
        conto = self.conto()
        self.assertEqual("missing", stato_valutazione(conto, None, self.OGGI))
        self.assertEqual({"code": "valuationMissing", "days": None}, self.avviso(conto))

    def test_un_conto_che_si_valuta_da_solo_non_sente_niente(self) -> None:
        # Gli investimenti sono valorizzati dal ledger: non hanno stime, e
        # sembrare vecchi sarebbe un avviso su una cosa che non esiste.
        conto = self.conto(gruppo="financial", manuale=False)
        self.assertIsNone(stato_valutazione(conto, None, self.OGGI))
        self.assertIsNone(self.avviso(conto))

    def test_un_conto_archiviato_non_si_aggiorna_piu(self) -> None:
        conto = self.conto(date(2020, 1, 1), attivo=False)
        self.assertIsNone(stato_valutazione(conto, date(2020, 1, 1), self.OGGI))
        self.assertIsNone(self.avviso(conto))

    def test_la_campanella_e_la_riga_dicono_la_stessa_cosa(self) -> None:
        # Il motivo per cui lo stato sta in una funzione sola: se la soglia
        # vivesse in due posti, la stessa casa sarebbe vecchia per l'avviso e
        # fresca per la riga, e nessuno dei due avrebbe torto da solo.
        vecchia = self.conto(date(2025, 1, 1))
        self.conto(date(2026, 9, 1))

        avvisi = _valutazioni_scadute(self.session, self.OGGI)

        self.assertEqual([vecchia.name], [avviso["params"]["account"] for avviso in avvisi])
        self.assertIsNotNone(self.avviso(vecchia))


if __name__ == "__main__":
    unittest.main()
