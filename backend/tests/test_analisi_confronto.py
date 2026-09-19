"""Il periodo della pagina Analisi, e i confronti che ne nascono.

"Ultimi 12 mesi" non e' l'anno solare: a settembre comincia a ottobre dell'anno
scorso. E' la differenza fra un confronto che vuol dire qualcosa e uno che dice
che si spende la meta' soltanto perche' l'anno non e' finito.

I numeri sono tondi e inventati: questo repository e' pubblico e i valori veri di
chi usa l'app non ci entrano.
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import _finestra_periodo, analysis
from app.database import Base
from app.models import Category, Transaction


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


class AnalisiTestBase(unittest.TestCase):
    """Un database in memoria, e il vocabolario per riempirlo.

    `_movimento` scrive sempre su `effective_on`: e' la data con cui l'app
    aggrega i budget, e un movimento datato solo su `occurred_on` non entra in
    nessun totale di questa pagina.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _radice(self, nome: str) -> int:
        riga = Category(name=nome)
        self.session.add(riga)
        self.session.flush()
        return riga.id

    def _figlio(self, nome: str, padre_id: int) -> int:
        riga = Category(name=nome, parent_id=padre_id)
        self.session.add(riga)
        self.session.flush()
        return riga.id

    def _movimento(self, categoria_id: int, importo: str, giorno: date,
                   tipo: str = "Expenses") -> None:
        self.session.add(Transaction(occurred_on=giorno, effective_on=giorno, transaction_type=tipo,
                                     category_id=categoria_id, amount=Decimal(importo),
                                     account_name="Conto"))


class RispostaTests(AnalisiTestBase):
    """La risposta dichiara su cosa e' calcolata: senza, tocca fidarsi."""

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


class ConfrontoTests(AnalisiTestBase):
    """Il periodo precedente, e le categorie che si confrontano con se stesse.

    Il quadro e' sempre lo stesso, e ogni prova ne controlla un pezzo: due
    figli sotto una radice, una radice sola, e una categoria che c'era prima e
    adesso non c'e' piu'. Con `oggi` finto al 19 settembre 2026 il periodo sono
    gli ultimi dodici mesi (ottobre 2025 - settembre 2026) e il precedente e'
    ottobre 2024 - settembre 2025.
    """

    def setUp(self) -> None:
        super().setUp()
        alimentari = self._radice("Alimentari")
        self.supermercato = self._figlio("Supermercato", alimentari)
        self.mensa = self._figlio("Mensa", alimentari)
        self.viaggi = self._radice("Viaggi")
        self.palestra = self._radice("Palestra")
        # Un movimento vecchio, fuori da tutti e due i periodi: serve solo a dire
        # che i dati cominciano prima del periodo precedente. Senza, il confronto
        # non si farebbe e ogni prova qui sotto misurerebbe un'altra cosa.
        self._movimento(self.supermercato, "1", date(2024, 5, 1))
        # Periodo in corso.
        self._movimento(self.supermercato, "30", date(2026, 3, 10))
        self._movimento(self.mensa, "20", date(2026, 3, 11))
        self._movimento(self.viaggi, "100", date(2026, 3, 12))
        # Periodo precedente.
        self._movimento(self.supermercato, "20", date(2024, 12, 10))
        self._movimento(self.viaggi, "40", date(2024, 12, 12))
        self._movimento(self.palestra, "25", date(2024, 12, 11))
        self.session.commit()

    def _analisi(self) -> dict:
        with patch("app.core_routes.date", OggiFinto):
            return analysis(2026, "last12", "Expenses", None, self.session)

    def _riga(self, nome: str) -> dict:
        righe = {riga["name"]: riga for riga in self._analisi()["categoryComparison"]}
        return righe[nome]

    def test_il_periodo_precedente_e_quello_di_pari_durata(self) -> None:
        confronto = self._analisi()["comparison"]
        self.assertTrue(confronto["available"])
        self.assertEqual("2024-10-01", confronto["from"])
        self.assertEqual("2025-09-30", confronto["to"])
        self.assertEqual("2024-05-01", confronto["since"])

    def test_1_in_entrambi_i_periodi_differenza_e_percentuale(self) -> None:
        riga = self._riga("Supermercato")
        self.assertEqual(30.0, riga["amount"])
        self.assertEqual(20.0, riga["previous"])
        self.assertEqual(10.0, riga["difference"])
        self.assertEqual(50.0, riga["percent"])

    def test_2_categoria_nuova_differenza_piena_e_percentuale_nulla(self) -> None:
        """Non "+∞" e non "+100%": una categoria appena nata e' cresciuta di tutto."""
        riga = self._riga("Mensa")
        self.assertEqual(20.0, riga["amount"])
        self.assertEqual(0.0, riga["previous"])
        self.assertEqual(20.0, riga["difference"])
        self.assertIsNone(riga["percent"])

    def test_3_categoria_sparita_differenza_negativa_e_percentuale_nulla(self) -> None:
        riga = self._riga("Palestra")
        self.assertEqual(0.0, riga["amount"])
        self.assertEqual(25.0, riga["previous"])
        self.assertEqual(-25.0, riga["difference"])
        self.assertIsNone(riga["percent"])

    def test_5_i_totali_risalgono_al_padre(self) -> None:
        """Trenta in Supermercato e venti in Mensa fanno cinquanta in Alimentari.

        La riga del padre vale se stessa piu' i figli: e' `con_i_figli`, la
        stessa somma che usano gli altri report, e non una seconda che risale
        l'albero per conto suo.
        """
        padre = self._riga("Alimentari")
        self.assertEqual(50.0, padre["amount"])
        self.assertEqual(20.0, padre["previous"])
        self.assertEqual(30.0, padre["difference"])

    def test_6_una_radice_senza_figli_conta_per_se_e_non_sparisce(self) -> None:
        riga = self._riga("Viaggi")
        self.assertIsNone(riga["parentId"])
        self.assertEqual(100.0, riga["amount"])
        self.assertEqual(40.0, riga["previous"])
        self.assertEqual(60.0, riga["difference"])
        self.assertEqual(150.0, riga["percent"])


class MedianaTests(AnalisiTestBase):
    """La mediana dei mesi, e quanti mesi l'hanno formata.

    La mediana e non la media: con un mese da mille euro fra undici da cento, la
    media sale a centosettantacinque e sembra un mese normale. E' il mese dei
    viaggi che si porta dietro, e non dice come sono i mesi normali.
    """

    def setUp(self) -> None:
        super().setUp()
        bollette = self._radice("Bollette")
        # Undici mesi da cento: ottobre 2025 - agosto 2026.
        for anno, mese in [(2025, m) for m in range(10, 13)] + [(2026, m) for m in range(1, 9)]:
            self._movimento(bollette, "100", date(anno, mese, 10))
        # E il mese in cui si e' pagato anche il resto.
        self._movimento(bollette, "1000", date(2026, 9, 10))
        palestra = self._radice("Palestra")
        self._movimento(palestra, "600", date(2026, 1, 20))
        self.session.commit()

    def _riga(self, nome: str) -> dict:
        with patch("app.core_routes.date", OggiFinto):
            risposta = analysis(2026, "last12", "Expenses", None, self.session)
        return {riga["name"]: riga for riga in risposta["categoryComparison"]}[nome]

    def test_7_un_mese_fuori_linea_non_sposta_la_mediana(self) -> None:
        riga = self._riga("Bollette")
        self.assertEqual(100.0, riga["median"])
        self.assertEqual(12, riga["monthsWithMovements"])
        self.assertEqual(12, riga["monthsConsidered"])

    def test_8_su_pochi_mesi_il_conteggio_dice_quanto_vale(self) -> None:
        """Una mediana su un mese su dodici non e' una mediana, e si vede.

        La riga porta il numero dei mesi che l'hanno formata, come fanno i
        suggerimenti di budget: il seicento speso a gennaio resta scritto
        accanto, e a fianco c'e' scritto che gennaio e' l'unico mese.
        """
        riga = self._riga("Palestra")
        self.assertEqual(0.0, riga["median"])
        self.assertEqual(1, riga["monthsWithMovements"])
        self.assertEqual(12, riga["monthsConsidered"])
        self.assertEqual(600.0, riga["amount"])


class SenzaStoriaTests(AnalisiTestBase):
    """Il primo periodo di dati: il confronto non si fa, e la risposta lo dice.

    Confrontare un mese di storia con il periodo precedente vuoto direbbe che
    ogni categoria e' cresciuta di tutto. Non e' un confronto: e' l'inizio.
    """

    def setUp(self) -> None:
        super().setUp()
        casa = self._radice("Casa")
        self._movimento(casa, "200", date(2026, 3, 10))
        self.session.commit()

    def test_4_il_confronto_non_si_fa_e_la_risposta_lo_dichiara(self) -> None:
        with patch("app.core_routes.date", OggiFinto):
            risposta = analysis(2026, "last12", "Expenses", None, self.session)
        confronto = risposta["comparison"]
        self.assertFalse(confronto["available"])
        # La finestra si dichiara lo stesso, e da quando esistono i dati.
        self.assertEqual("2024-10-01", confronto["from"])
        self.assertEqual("2026-03-10", confronto["since"])
        riga = risposta["categoryComparison"][0]
        self.assertEqual(200.0, riga["amount"])
        for campo in ("previous", "difference", "percent"):
            self.assertIsNone(riga[campo])
