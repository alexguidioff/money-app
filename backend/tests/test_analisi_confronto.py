"""Il periodo della pagina Analisi, e i confronti che ne nascono.

"Ultimi 12 mesi" non e' l'anno solare: a settembre comincia a ottobre dell'anno
scorso. E' la differenza fra un confronto che vuol dire qualcosa e uno che dice
che si spende la meta' soltanto perche' l'anno non e' finito.

I numeri sono tondi e inventati: questo repository e' pubblico e i valori veri di
chi usa l'app non ci entrano.
"""
import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import _finestra_periodo, analysis
from app.database import Base, reset_current_user, set_current_user
from app.models import BudgetPlan, Category, InvestmentTransaction, Transaction
from tests.test_categorie_albero import SchemaIsolato


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

    def _radice(self, nome: str, scope: str = "expense") -> int:
        riga = Category(name=nome, scope=scope)
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

    def test_con_l_anno_solare_il_periodo_e_l_anno(self) -> None:
        risposta = analysis(2025, "year", "Expenses", None, self.session)
        self.assertEqual("year", risposta["period"]["scope"])
        self.assertEqual("2025-01-01", risposta["period"]["from"])
        self.assertEqual("2025-12-31", risposta["period"]["to"])


class SchedeDellaFinestraTests(AnalisiTestBase):
    """Le quattro schede mensili seguono il periodo dichiarato, non un anno solare.

    Erano l'ultima cosa ferma a gennaio-dicembre: con "ultimi dodici mesi" la
    pagina dichiarava una finestra (ottobre 2025 - settembre 2026) e disegnava
    l'anno solare, cioe' dodici mesi diversi da quelli appena dichiarati. Qui si
    controlla che seguano la finestra: l'asse, il budget di ogni mese, il
    risparmio, le categorie piu' pesanti e quelle delle tendine.

    Con `oggi` finto al 19 settembre 2026 la finestra scorrevole e' ottobre 2025
    - settembre 2026; l'anno 2025 e' gennaio-dicembre 2025, e il 2026 tutto
    intero, ottobre compreso.
    """

    def setUp(self) -> None:
        super().setUp()
        self.casa = self._radice("Casa")
        viaggi = self._radice("Viaggi")
        stipendio = self._radice("Stipendio", scope="income")
        # Dentro la finestra scorrevole e dentro il 2025.
        self._movimento(self.casa, "100", date(2025, 10, 5))
        self._movimento(stipendio, "900", date(2025, 10, 20), tipo="Income")
        # Fuori dalla finestra scorrevole, dentro il 2025.
        self._movimento(self.casa, "500", date(2025, 2, 10))
        # Il mese di oggi, che chiude la finestra.
        self._movimento(self.casa, "300", date(2026, 9, 10))
        # Dentro il 2026, fuori dalla finestra: ottobre 2026 non e' cominciato.
        self._movimento(viaggi, "700", date(2026, 10, 5))
        # Due piani con lo stesso mese e anni diversi: e' il caso che una
        # domanda per solo mese sbaglia, e in una finestra che scorre i due
        # ottobre convivono.
        self.session.add(BudgetPlan(period=date(2025, 10, 1), budget_type="Expenses",
                                    category_id=self.casa, amount=Decimal("50")))
        self.session.add(BudgetPlan(period=date(2026, 10, 1), budget_type="Expenses",
                                    category_id=self.casa, amount=Decimal("400")))
        # Un versamento negli strumenti nello stesso periodo del risparmio.
        self.session.add(InvestmentTransaction(occurred_on=date(2025, 11, 10), name="ETF di prova",
                                               transaction_type="Buy", amount=Decimal("1000")))
        self.session.commit()

    def _analisi(self, anno: int = 2026, scope: str = "last12") -> dict:
        with patch("app.core_routes.date", OggiFinto):
            return analysis(anno, scope, "Expenses", None, self.session)

    def _riga(self, risposta: dict, etichetta: str) -> dict:
        return {mese["month"]: mese for mese in risposta["monthlyBudget"]["expenses"]}[etichetta]

    def test_l_asse_e_quello_della_finestra_e_porta_l_anno(self) -> None:
        """Dodici mesi che partono da ottobre, e ogni etichetta dice il suo anno.

        Senza l'anno, un asse che comincia a ottobre non direbbe se quell'ottobre
        e' quello di adesso o quello di un anno fa - ed e' esattamente la
        domanda a cui la pagina risponde con questa finestra.
        """
        scorrevole = [mese["month"] for mese in self._analisi()["monthlyBudget"]["expenses"]]
        self.assertEqual(["Ott 25", "Nov 25", "Dic 25", "Gen 26", "Feb 26", "Mar 26", "Apr 26",
                          "Mag 26", "Giu 26", "Lug 26", "Ago 26", "Set 26"], scorrevole)
        solare = [mese["month"] for mese in self._analisi(2025, "year")["monthlyBudget"]["expenses"]]
        self.assertEqual(["Gen 25", "Feb 25", "Mar 25", "Apr 25", "Mag 25", "Giu 25", "Lug 25",
                          "Ago 25", "Set 25", "Ott 25", "Nov 25", "Dic 25"], solare)

    def test_il_budget_di_ogni_mese_e_quello_del_suo_anno(self) -> None:
        """A ottobre 2025 il piano di ottobre 2025, a ottobre 2026 quello suo.

        `inBudget` piu' `remaining` e' il pianificato del mese: la riga dice
        insieme quanto se ne e' usato e quanto ne resta.
        """
        ottobre_25 = self._riga(self._analisi(), "Ott 25")
        self.assertEqual(50.0, ottobre_25["inBudget"] + ottobre_25["remaining"])
        self.assertEqual(50.0, ottobre_25["inBudget"])
        self.assertEqual(50.0, ottobre_25["excess"])
        ottobre_26 = self._riga(self._analisi(2026, "year"), "Ott 26")
        self.assertEqual(400.0, ottobre_26["inBudget"] + ottobre_26["remaining"])

    def test_l_ultimo_mese_della_finestra_e_quello_in_corso(self) -> None:
        """Nella finestra che scorre il mese acceso e' l'ultimo: e' oggi.

        In un anno passato nessun mese e' acceso.
        """
        scorrevole = self._analisi()["monthlyBudget"]["expenses"]
        self.assertEqual([False] * 11 + [True], [mese["isCurrentMonth"] for mese in scorrevole])
        self.assertEqual("Set 26", scorrevole[-1]["month"])
        self.assertEqual([False] * 12, [mese["isCurrentMonth"] for mese in self._analisi(2025, "year")["monthlyBudget"]["expenses"]])

    def test_risparmio_e_investito_stanno_sugli_stessi_mesi(self) -> None:
        """I due grafici si leggono affiancati, indice per indice: stesso asse.

        Il risparmio e' entrate meno spese del mese (900 meno 100 a ottobre
        2025), non la somma dei movimenti di tipo Savings, che non esistono
        piu': l'investito e' acquisti meno vendite dello stesso mese.
        """
        risposta = self._analisi()
        risparmio = {mese["month"]: mese["amount"] for mese in risposta["savingsByMonth"]}
        investito = {mese["month"]: mese["amount"] for mese in risposta["investedByMonth"]}
        self.assertEqual(800.0, risparmio["Ott 25"])
        self.assertEqual(0.0, risparmio["Nov 25"])
        self.assertEqual(0.0, risparmio["Dic 25"])
        self.assertEqual(1000.0, investito["Nov 25"])
        self.assertEqual(list(risparmio), list(investito))

    def test_le_categorie_sono_quelle_della_finestra(self) -> None:
        """Fuori dalla finestra non si vede niente, dentro si vede tutto.

        Viaggi ha una spesa a ottobre 2026, che negli ultimi dodici mesi non
        c'e': fra le piu' pesanti e fra le categorie da scegliere compare solo
        guardando il 2026. La prima riga del treemap e' la categoria maggiore
        della finestra, con il valore della finestra (100 piu' 300).
        """
        scorrevole = self._analisi()
        self.assertEqual([("Casa", 400.0)], [(voce["name"], voce["value"]) for voce in scorrevole["topExpenseCategories"]])
        self.assertEqual(["Casa"], scorrevole["categoryOptions"])
        solare = self._analisi(2026, "year")
        self.assertEqual(["Viaggi", "Casa"], [voce["name"] for voce in solare["topExpenseCategories"]])
        self.assertEqual(["Casa", "Viaggi"], solare["categoryOptions"])

    def test_le_transazioni_mostrate_sono_quelle_della_finestra(self) -> None:
        """L'elenco delle transazioni di una categoria taglia come tutto il resto.

        Le stesse due categorie si guardano dalle due finestre: quello che una
        mostra e l'altra no e' tutto qui.
        """
        def elenco(scope: str, categoria: str) -> list[str]:
            with patch("app.core_routes.date", OggiFinto):
                risposta = analysis(2026, scope, "Expenses", categoria, self.session)
            return [voce["date"] for voce in risposta["categoryTransactions"]]

        self.assertEqual(["2026-09-10", "2025-10-05"], elenco("last12", "Casa"))
        self.assertEqual(["2026-09-10"], elenco("year", "Casa"))
        self.assertEqual([], elenco("last12", "Viaggi"))
        self.assertEqual(["2026-10-05"], elenco("year", "Viaggi"))


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
        stipendio = self._radice("Stipendio", scope="income")
        # Un movimento vecchio, fuori da tutti e due i periodi: serve solo a dire
        # che i dati cominciano prima del periodo precedente. Senza, il confronto
        # non si farebbe e ogni prova qui sotto misurerebbe un'altra cosa.
        self._movimento(self.supermercato, "1", date(2024, 5, 1))
        # Periodo in corso.
        self._movimento(self.supermercato, "30", date(2026, 3, 10))
        self._movimento(self.mensa, "20", date(2026, 3, 11))
        self._movimento(self.viaggi, "100", date(2026, 3, 12))
        self._movimento(stipendio, "2000", date(2026, 3, 1), tipo="Income")
        # Periodo precedente.
        self._movimento(self.supermercato, "20", date(2024, 12, 10))
        self._movimento(self.viaggi, "40", date(2024, 12, 12))
        self._movimento(self.palestra, "25", date(2024, 12, 11))
        self._movimento(stipendio, "1500", date(2024, 12, 1), tipo="Income")
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


class MosseTests(AnalisiTestBase):
    """Le tre categorie che spiegano la differenza: quelle che si sono mosse.

    Casa e' la piu' grande di tutte e non si e' mossa di un euro: se comparisse
    fra le tre, la pagina direbbe che il periodo e' cambiato per colpa di una
    spesa che e' identica a prima.
    """

    def setUp(self) -> None:
        super().setUp()
        banca = self._radice("Banca")
        self._movimento(banca, "1", date(2024, 5, 2))
        casa = self._radice("Casa")
        self._movimento(casa, "5000", date(2026, 2, 10))
        self._movimento(casa, "5000", date(2025, 2, 10))
        palestra = self._radice("Palestra")
        self._movimento(palestra, "100", date(2026, 2, 11))
        self._movimento(palestra, "40", date(2025, 2, 11))
        viaggi = self._radice("Viaggi")
        self._movimento(viaggi, "200", date(2026, 2, 12))
        self._movimento(viaggi, "350", date(2025, 2, 12))
        mensa = self._radice("Mensa")
        self._movimento(mensa, "50", date(2026, 2, 13))
        self._movimento(mensa, "20", date(2025, 2, 13))
        self.session.commit()

    def _mosse(self) -> list[dict]:
        with patch("app.core_routes.date", OggiFinto):
            return analysis(2026, "last12", "Expenses", None, self.session)["flow"]["movers"]

    def test_10_le_tre_che_si_muovono_non_le_tre_piu_grandi(self) -> None:
        nomi = [riga["name"] for riga in self._mosse()]
        self.assertEqual(["Viaggi", "Palestra", "Mensa"], nomi)
        self.assertNotIn("Casa", nomi)
        self.assertEqual([-150.0, 60.0, 30.0], [riga["difference"] for riga in self._mosse()])


class FlussoTests(AnalisiTestBase):
    """Entrato, uscito, rimasto: la risposta che uno cerca per prima.

    E il verso di entrate e spese viaggia con i numeri: e' quello che decide di
    che colore si scrive una differenza, e sono due versi opposti. Indovinarlo a
    video dal segno direbbe che guadagnare di piu' e' un problema.
    """

    def setUp(self) -> None:
        super().setUp()
        banca = self._radice("Banca")
        self._movimento(banca, "1", date(2024, 5, 3))
        stipendio = self._radice("Stipendio", scope="income")
        self._movimento(stipendio, "2000", date(2026, 3, 1), tipo="Income")
        self._movimento(stipendio, "1500", date(2024, 12, 1), tipo="Income")
        casa = self._radice("Casa")
        self._movimento(casa, "800", date(2026, 3, 2))
        self._movimento(casa, "1000", date(2024, 12, 2))
        self.session.commit()

    def _analisi(self, tipo: str = "Expenses") -> dict:
        with patch("app.core_routes.date", OggiFinto):
            return analysis(2026, "last12", tipo, None, self.session)

    def _flusso(self) -> dict:
        return self._analisi()["flow"]

    def test_11_entrate_e_spese_hanno_versi_opposti_e_il_verso_e_nella_risposta(self) -> None:
        flusso = self._flusso()
        self.assertEqual(2000.0, flusso["income"]["amount"])
        self.assertEqual(1500.0, flusso["income"]["previous"])
        self.assertEqual(500.0, flusso["income"]["difference"])
        self.assertEqual(33.3, flusso["income"]["percent"])
        self.assertEqual(800.0, flusso["expenses"]["amount"])
        self.assertEqual(-200.0, flusso["expenses"]["difference"])
        self.assertEqual(-20.0, flusso["expenses"]["percent"])
        self.assertEqual(1200.0, flusso["net"]["amount"])
        # Il netto e' gia' una differenza: la sua percentuale non esiste.
        self.assertIsNone(flusso["net"]["percent"])
        # Il verso sta su ogni riga: e' quello che colora la differenza.
        self.assertEqual(["expense"], [riga["scope"] for riga in flusso["movers"]])
        righe_entrate = self._analisi("Income")["categoryComparison"]
        self.assertEqual({"income"}, {riga["scope"] for riga in righe_entrate})


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
        # Senza confronto non ci sono nemmeno le categorie che si sono mosse.
        self.assertEqual([], risposta["flow"]["movers"])


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoFlussoTests(unittest.TestCase):
    """I totali di una persona non contengono quelli di un'altra.

    Le politiche di riga del database sono l'unica prova vera di questa cosa: su
    SQLite non esistono, e se una somma dimenticasse di filtrare le due
    contabilita' finirebbero nello stesso mucchio senza che nessuno se ne
    accorga. Per questo la prova gira solo dove le politiche ci sono.
    """

    def _semina(self, schema, utente: int, importo: str) -> None:
        token = set_current_user(utente)
        try:
            with Session(schema.limited) as session:
                casa = Category(name="Casa")
                session.add(casa)
                session.flush()
                session.add(Transaction(occurred_on=date(2026, 3, 10), effective_on=date(2026, 3, 10),
                                        transaction_type="Expenses", category_id=casa.id,
                                        amount=Decimal(importo), account_name="Conto"))
                session.commit()
        finally:
            reset_current_user(token)

    def test_12_i_totali_di_uno_non_contengono_quelli_dell_altro(self) -> None:
        with SchemaIsolato() as schema:
            self._semina(schema, 101, "200")
            self._semina(schema, 202, "9000")
            token = set_current_user(101)
            try:
                with Session(schema.limited) as session:
                    with patch("app.core_routes.date", OggiFinto):
                        risposta = analysis(2026, "last12", "Expenses", None, session)
            finally:
                reset_current_user(token)
        self.assertEqual(200.0, risposta["flow"]["expenses"]["amount"])
        self.assertEqual([200.0], [riga["amount"] for riga in risposta["categoryComparison"]])
        # E il confronto guarda la storia di chi sta guardando: da li' in poi.
        self.assertEqual("2026-03-10", risposta["comparison"]["since"])
