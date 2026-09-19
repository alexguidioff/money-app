"""Il periodo della pagina Analisi, e i confronti che ne nascono.

"Ultimi 12 mesi" non e' l'anno solare: a settembre comincia a ottobre dell'anno
scorso. E' la differenza fra un confronto che vuol dire qualcosa e uno che dice
che si spende la meta' soltanto perche' l'anno non e' finito.

I numeri sono tondi e inventati: questo repository e' pubblico e i valori veri di
chi usa l'app non ci entrano.
"""
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import _finestra_periodo, analysis
from app.database import Base


class OggiFinto(date):
    """Un `date` che dice sempre lo stesso giorno.

    Serve perche' "ultimi 12 mesi" parte da oggi: senza fissarlo, il test
    passerebbe a settembre e cadrebbe il primo di ottobre.
    """
    @classmethod
    def today(cls) -> date:
        return date(2026, 9, 19)


class FinestraTests(unittest.TestCase):
    def test_9_ultimi_dodici_mesi_a_settembre_partono_da_ottobre(self) -> None:
        """Il mese di oggi chiude la finestra, e dodici mesi indietro la aprono.

        E' il caso che l'anno solare sbaglia: a settembre "2026" sono nove mesi,
        e metterli accanto a un anno intero direbbe che si spende molto meno.
        """
        inizio, fine = _finestra_periodo("last12", 2026, date(2026, 9, 19))
        self.assertEqual(date(2025, 10, 1), inizio)
        self.assertEqual(date(2026, 9, 30), fine)

    def test_la_finestra_attraversa_l_anno_a_gennaio(self) -> None:
        """Dodici mesi indietro da gennaio e' febbraio dell'anno prima."""
        inizio, fine = _finestra_periodo("last12", 2026, date(2026, 1, 5))
        self.assertEqual(date(2025, 2, 1), inizio)
        self.assertEqual(date(2026, 1, 31), fine)

    def test_la_finestra_finisce_con_il_mese_e_non_con_oggi(self) -> None:
        """Un movimento di fine mese sta dentro la finestra.

        Chiudere a "oggi" lascerebbe fuori gli ultimi giorni del mese in corso, e
        un totale che cambia a seconda del giorno in cui lo si guarda non e' un
        totale.
        """
        _, fine = _finestra_periodo("last12", 2026, date(2026, 2, 3))
        self.assertEqual(date(2026, 2, 28), fine)
        # E negli anni bisestili il mese ha un giorno in piu'.
        _, bisestile = _finestra_periodo("last12", 2024, date(2024, 2, 3))
        self.assertEqual(date(2024, 2, 29), bisestile)

    def test_l_anno_solare_non_guarda_oggi(self) -> None:
        """Chi sceglie un anno sceglie quell'anno: primo gennaio, trentun dicembre."""
        inizio, fine = _finestra_periodo("year", 2024, date(2026, 9, 19))
        self.assertEqual(date(2024, 1, 1), inizio)
        self.assertEqual(date(2024, 12, 31), fine)


class RispostaTests(unittest.TestCase):
    """La risposta dichiara su cosa e' calcolata: senza, tocca fidarsi."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_la_risposta_dichiara_il_suo_periodo(self) -> None:
        with patch("app.core_routes.date", OggiFinto):
            risposta = analysis(2026, "last12", "Expenses", None, self.session)
        self.assertEqual("last12", risposta["period"]["scope"])
        self.assertEqual("2025-10-01", risposta["period"]["from"])
        self.assertEqual("2026-09-30", risposta["period"]["to"])
        # L'anno resta quello delle quattro cose che restano annuali.
        self.assertEqual(2026, risposta["year"])

    def test_con_l_anno_solare_il_periodo_e_l_anno(self) -> None:
        risposta = analysis(2025, "year", "Expenses", None, self.session)
        self.assertEqual("year", risposta["period"]["scope"])
        self.assertEqual("2025-01-01", risposta["period"]["from"])
        self.assertEqual("2025-12-31", risposta["period"]["to"])
