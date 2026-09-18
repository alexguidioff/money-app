"""Il rendimento del portafoglio: numeri tondi, inventati, e i bordi del metodo.

Quello che qui puo' rompersi in silenzio non e' la divisione ma la **natura dei
flussi**: un versamento contato come rendimento gonfia il risultato, un
dividendo contato come versamento lo azzera esattamente della cifra incassata.
Sono errori che danno un numero plausibile, ed e' per questo che ogni caso ha
il suo.

Le date sono di fine mese perche' e' cosi' che arrivano: in cache ci sono
chiusure di fine mese, non prezzi di ogni giorno.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import investments_dashboard
from app.database import Base
from app.models import AppSetting, InvestmentInstrument, InvestmentTransaction, MarketPrice
from app.rendimenti import (
    FLUSSI_SENZA_CAMBIO_DI_SEGNO,
    NESSUN_FLUSSO,
    PREZZO_MANCANTE,
    STORIA_TROPPO_CORTA,
    Flusso,
    Rendimento,
    Valutazione,
    catena,
    da_cento,
    twr,
    xirr,
)

GEN = date(2024, 1, 31)
FEB = date(2024, 2, 29)
MAR = date(2024, 3, 31)
# Un periodo di trenta giorni tondi, per leggere i pesi a mente: aprile ha 30
# giorni, quindi il primo maggio dista esattamente 30 giorni dal primo aprile.
INIZIO = date(2024, 4, 1)
FINE = date(2024, 5, 1)


def v(giorno: date, valore: str | None) -> Valutazione:
    return Valutazione(giorno, None if valore is None else Decimal(valore))


class TwrTests(unittest.TestCase):
    def test_mercato_fermo_senza_flussi_non_rende_niente(self) -> None:
        esito = twr([v(GEN, "100"), v(FEB, "100")])
        self.assertEqual(Rendimento(Decimal("0.0000"), None), esito)

    def test_da_cento_a_cento_dieci_senza_flussi_e_dieci_per_cento(self) -> None:
        self.assertEqual(Decimal("0.1000"), twr([v(GEN, "100"), v(FEB, "110")]).valore)

    def test_un_versamento_non_e_rendimento(self) -> None:
        # 100 che diventano 220 con 100 versati a meta' periodo: il rendimento
        # e' il guadagno sui 100 che lavoravano, non il 120% del saldo finale.
        # E' il caso che giustifica tutto il metodo.
        esito = twr([v(INIZIO, "100"), v(FINE, "220")], [Flusso(date(2024, 4, 16), Decimal("100"))])
        self.assertNotEqual(Decimal("1.2000"), esito.valore)
        # 20 di guadagno sui 100 di partenza piu' i 100 versati per meta'
        # periodo: 20 / 150.
        self.assertEqual(Decimal("0.1333"), esito.valore)

    def test_due_periodi_del_dieci_per_cento_fanno_ventuno(self) -> None:
        # Concatenare non e' sommare: nel secondo periodo lavora anche il
        # guadagno del primo.
        self.assertEqual(Decimal("0.2100"), twr([v(GEN, "100"), v(FEB, "110"), v(MAR, "121")]).valore)

    def test_lo_stesso_flusso_il_primo_giorno_e_l_ultimo_rendono_diverso(self) -> None:
        # Modified Dietz pesa un flusso per quanto e' rimasto investito: lo
        # stesso versamento arrivato all'inizio lavora di piu' di uno arrivato
        # all'ultimo giorno, quindi per il portafoglio rende meno.
        primo = twr([v(INIZIO, "100"), v(FINE, "220")], [Flusso(date(2024, 4, 2), Decimal("100"))])
        ultimo = twr([v(INIZIO, "100"), v(FINE, "220")], [Flusso(FINE, Decimal("100"))])
        self.assertLess(primo.valore, ultimo.valore)
        # 20 su 100 piu' 100 versati per 29 giorni su 30: 20 / 196,66...
        self.assertEqual(Decimal("0.1017"), primo.valore)
        # All'ultimo giorno il versamento non ha lavorato: il guadagno e' tutto
        # sui 100 di partenza.
        self.assertEqual(Decimal("0.2000"), ultimo.valore)

    def test_un_mese_senza_prezzo_non_diventa_zero(self) -> None:
        esito = twr([v(GEN, "100"), v(FEB, None), v(MAR, "120")])
        self.assertIsNone(esito.valore)
        self.assertEqual(PREZZO_MANCANTE, esito.motivo)

    def test_una_sola_valutazione_non_e_un_periodo(self) -> None:
        esito = twr([v(GEN, "100")])
        self.assertIsNone(esito.valore)
        self.assertEqual(STORIA_TROPPO_CORTA, esito.motivo)

    def test_un_dividendo_lasciato_sul_conto_alza_il_rendimento(self) -> None:
        # Il dividendo e' denaro che arriva e resta: non e' un flusso, quindi e'
        # rendimento. Contarlo per sbaglio come versamento lo azzererebbe
        # esattamente della cifra incassata.
        self.assertEqual(Decimal("0.0500"), twr([v(GEN, "100"), v(FEB, "105")]).valore)
        self.assertEqual(Decimal("0.0000"),
                         twr([v(GEN, "100"), v(FEB, "105")], [Flusso(FEB, Decimal("5"))]).valore)

    def test_una_commissione_abbassa_il_rendimento(self) -> None:
        # La commissione esce dal portafoglio e non entra in nessun altro conto
        # misurato: e' un costo, e si vede nel rendimento.
        self.assertEqual(Decimal("-0.0300"), twr([v(GEN, "100"), v(FEB, "97")]).valore)
        self.assertEqual(Decimal("0.0000"),
                         twr([v(GEN, "100"), v(FEB, "97")], [Flusso(FEB, Decimal("-3"))]).valore)


class CatenaTests(unittest.TestCase):
    def test_il_cumulato_si_legge_a_ogni_passo(self) -> None:
        # Due periodi del dieci per cento: 1,1 e poi 1,21. E' la catena che il
        # TWR riduce a un numero solo, e il confronto con l'indice ha bisogno
        # intera.
        self.assertEqual([Decimal("1"), Decimal("1.1"), Decimal("1.21")],
                         catena([v(GEN, "100"), v(FEB, "110"), v(MAR, "121")]))
        self.assertEqual(Decimal("0.2100"),
                         twr([v(GEN, "100"), v(FEB, "110"), v(MAR, "121")]).valore)

    def test_un_mese_senza_prezzo_non_ha_catena(self) -> None:
        self.assertIsNone(catena([v(GEN, "100"), v(FEB, None), v(MAR, "120")]))

    def test_una_valutazione_sola_non_ha_catena(self) -> None:
        self.assertIsNone(catena([v(GEN, "100")]))


class DaCentoTests(unittest.TestCase):
    def test_la_serie_parte_da_cento(self) -> None:
        self.assertEqual([Decimal("100.00"), Decimal("110.00"), Decimal("121.00")],
                         da_cento([Decimal("100"), Decimal("110"), Decimal("121")]))

    def test_una_serie_che_parte_da_zero_non_si_riporta_a_cento(self) -> None:
        # Dividere per zero non e' un confronto: meglio nessuna curva.
        self.assertEqual([], da_cento([Decimal("0"), Decimal("10")]))

    def test_una_serie_vuota_resta_vuota(self) -> None:
        self.assertEqual([], da_cento([]))


class XirrTests(unittest.TestCase):
    def test_mille_versati_che_diventano_milleduecento_in_un_anno(self) -> None:
        # Un anno tondo: dal primo gennaio al trentuno dicembre del 2024 ci sono
        # esattamente 365 giorni, quindi il tasso e' quello che si legge a mano,
        # 1.100 / 1,1 = 1.000.
        esito = xirr([Flusso(date(2024, 1, 1), Decimal("1000"))], Valutazione(date(2024, 12, 31), Decimal("1100")))
        self.assertEqual(Decimal("0.1000"), esito.valore)

    def test_due_versamenti_irregolari_danno_un_tasso_che_azzera_il_valore_attuale(self) -> None:
        # Il tasso non si legge a mente - e' per questo che serve il solutore -
        # quindi si controlla la definizione: al tasso trovato, il capitale
        # finale riportato a oggi vale quanto i versamenti che l'hanno
        # prodotto. La formula e' scritta qui a mano, non presa dal modulo.
        versamenti = [Flusso(date(2024, 1, 1), Decimal("1000")), Flusso(date(2024, 7, 1), Decimal("1000"))]
        finale = Valutazione(date(2025, 1, 1), Decimal("2100"))
        tasso = float(xirr(versamenti, finale).valore)
        base = date(2024, 1, 1)
        scontati = sum(float(f.importo) / (1.0 + tasso) ** ((f.giorno - base).days / 365.0) for f in versamenti)
        incassato = float(finale.valore) / (1.0 + tasso) ** ((finale.giorno - base).days / 365.0)
        # Il tasso esce arrotondato al quarto decimale, quindi l'uguaglianza si
        # controlla al centesimo di euro: il resto e' quell'arrotondamento.
        self.assertAlmostEqual(incassato, scontati, delta=0.05)
        self.assertGreater(tasso, 0.0)

    def test_flussi_tutti_dello_stesso_verso_non_hanno_un_tasso(self) -> None:
        # Solo prelievi e un valore finale: il denaro esce e rientra dalla stessa
        # parte, e non c'e' nessun tasso che annulli il valore attuale.
        esito = xirr([Flusso(date(2024, 1, 1), Decimal("-500"))], Valutazione(date(2024, 12, 31), Decimal("100")))
        self.assertIsNone(esito.valore)
        self.assertEqual(FLUSSI_SENZA_CAMBIO_DI_SEGNO, esito.motivo)

    def test_senza_flussi_non_esiste_un_tasso(self) -> None:
        esito = xirr([], Valutazione(date(2024, 12, 31), Decimal("1000")))
        self.assertIsNone(esito.valore)
        self.assertEqual(NESSUN_FLUSSO, esito.motivo)

    def test_un_valore_finale_non_calcolabile_non_diventa_un_tasso(self) -> None:
        esito = xirr([Flusso(date(2024, 1, 1), Decimal("1000"))], Valutazione(date(2024, 12, 31), None))
        self.assertIsNone(esito.valore)
        self.assertEqual(PREZZO_MANCANTE, esito.motivo)


TABELLE = [AppSetting.__table__, InvestmentInstrument.__table__, InvestmentTransaction.__table__,
           MarketPrice.__table__]


class RendimentoDelCruscotto(unittest.TestCase):
    """Il blocco `returns` del cruscotto, costruito su una serie inventata.

    Ogni chiamata si apre la sua banca dati: la serie piatta che segue l'ultima
    operazione vale comunque due anni, e riusare la stessa sessione
    significherebbe sommare due volte le stesse operazioni e misurare una cosa
    diversa da quella che il test racconta.
    """

    def _cruscotto(self, *operazioni: tuple[str, date, str, str, str],
                   prezzi: tuple[tuple[date, str], ...] = ((date(2024, 1, 31), "10.00"),
                                                           (date(2024, 2, 29), "11.00")),
                   indice: str = "", prezzi_indice: tuple[tuple[date, str], ...] = ()) -> dict:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=TABELLE)
        session = Session(engine)
        session.add(InvestmentInstrument(name="Titolo", provider_symbol="TIT.MI", currency="EUR"))
        if indice:
            session.add(AppSetting(key="benchmark_symbol", label="Indice di riferimento", value=indice))
        for giorno, valore in prezzi:
            session.add(MarketPrice(symbol="TIT.MI", observed_on=giorno, price=Decimal(valore),
                                    currency="EUR", provider="yahoo"))
        for giorno, valore in prezzi_indice:
            session.add(MarketPrice(symbol=indice, observed_on=giorno, price=Decimal(valore),
                                    currency="EUR", provider="yahoo"))
        for tipo, giorno, quote, importo, prezzo in operazioni:
            session.add(InvestmentTransaction(name="Titolo", transaction_type=tipo, occurred_on=giorno,
                                              units=Decimal(quote), amount=Decimal(importo),
                                              price=Decimal(prezzo)))
        session.commit()
        try:
            return investments_dashboard(session)
        finally:
            session.close()

    def _rendimenti(self, *operazioni: tuple[str, date, str, str, str],
                    prezzi: tuple[tuple[date, str], ...] = ((date(2024, 1, 31), "10.00"),
                                                            (date(2024, 2, 29), "11.00"))) -> dict:
        return self._cruscotto(*operazioni, prezzi=prezzi)["returns"]

    def test_il_rendimento_e_il_guadagno_del_tempo_non_del_saldo(self) -> None:
        # Un acquisto da 100 a gennaio e una quotazione a 110 da febbraio: da li'
        # in poi il portafoglio non si muove, e il dieci per cento e' tutto li'.
        rendimenti = self._rendimenti(("Buy", date(2024, 1, 15), "10", "100.00", "10.00"))
        self.assertEqual(0.10, rendimenti["twr"]["value"])
        self.assertIsNone(rendimenti["twr"]["reason"])
        # Un versamento solo, piu' di due anni fa, su un guadagno del dieci per
        # cento: il tasso annuo e' positivo e piu' piccolo del guadagno totale.
        self.assertIsNotNone(rendimenti["xirr"]["value"])
        self.assertGreater(rendimenti["xirr"]["value"], 0.0)
        self.assertLess(rendimenti["xirr"]["value"], 0.10)
        self.assertEqual("2024-01-31", rendimenti["since"])
        self.assertGreater(rendimenti["months"], 30)

    def test_uno_split_non_cambia_niente(self) -> None:
        # Moltiplicare le quote e dimezzare il prezzo lascia il valore dov'e':
        # dopo il frazionamento 2:1 le dieci quote da 11 valgono come venti da
        # 5,50. Trattato come una vendita, lo split avrebbe tolto due quote
        # dalla posizione e il rendimento sarebbe crollato per un'operazione che
        # non ha spostato un euro.
        senza = self._rendimenti(("Buy", date(2024, 1, 15), "10", "100.00", "10.00"))
        con = self._rendimenti(
            ("Buy", date(2024, 1, 15), "10", "100.00", "10.00"),
            # Un frazionamento porta il rapporto nelle quote e lascia a zero
            # importo e prezzo: e' quello che il ledger ci scrive dentro.
            ("Split", date(2024, 3, 1), "2", "0", "0"),
            prezzi=((date(2024, 1, 31), "10.00"), (date(2024, 2, 29), "11.00"),
                    (date(2024, 3, 31), "5.50")),
        )
        self.assertEqual(senza, con)
        self.assertEqual(0.10, con["twr"]["value"])

    def test_un_prezzo_che_manca_e_un_motivo_non_uno_zero(self) -> None:
        # Strumento posseduto, simbolo impostato, nessuna quotazione in cache: il
        # valore ripiega sul prezzo dell'operazione e il rendimento non e'
        # calcolabile. Meglio dirlo che mostrare un numero costruito.
        rendimenti = self._rendimenti(("Buy", date(2024, 1, 15), "10", "100.00", "10.00"), prezzi=())
        self.assertIsNone(rendimenti["twr"]["value"])
        self.assertEqual(PREZZO_MANCANTE, rendimenti["twr"]["reason"])
        self.assertIsNone(rendimenti["xirr"]["value"])
        self.assertEqual(PREZZO_MANCANTE, rendimenti["xirr"]["reason"])

    def test_senza_indice_non_c_e_una_seconda_serie(self) -> None:
        # Non aver configurato niente non e' un guasto: nessuna curva, nessun
        # errore, e i numeri di prima restano quelli di prima.
        cruscotto = self._cruscotto(("Buy", date(2024, 1, 15), "10", "100.00", "10.00"))
        self.assertEqual({"symbol": None, "from": None, "months": 0}, cruscotto["benchmark"])
        self.assertTrue(all(punto["twrCurve"] is None for punto in cruscotto["history"]))
        self.assertTrue(all(punto["benchmarkCurve"] is None for punto in cruscotto["history"]))
        self.assertEqual(0.10, cruscotto["returns"]["twr"]["value"])

    def test_un_indice_piu_corto_accorcia_il_confronto(self) -> None:
        # L'indice esiste solo da luglio: il confronto parte da li', invece di
        # riempire i mesi prima con l'ultima quotazione disponibile - che
        # disegnerebbe una riga piatta per mesi mai misurati.
        cruscotto = self._cruscotto(
            ("Buy", date(2024, 1, 15), "10", "100.00", "10.00"),
            indice="IDX",
            prezzi_indice=((date(2024, 7, 31), "200.00"), (date(2024, 8, 30), "210.00")))
        self.assertEqual("IDX", cruscotto["benchmark"]["symbol"])
        self.assertEqual("2024-07-01", cruscotto["benchmark"]["from"])
        self.assertEqual(2, cruscotto["benchmark"]["months"])
        punti = {punto["period"]: punto for punto in cruscotto["history"]}
        self.assertIsNone(punti["2024-06-01"]["twrCurve"])
        self.assertIsNone(punti["2024-06-01"]["benchmarkCurve"])
        # Da luglio entrambe ripartono da 100: il portafoglio vale 110 come a
        # febbraio e non si muove, l'indice sale da 200 a 210.
        self.assertEqual(100.0, punti["2024-07-01"]["twrCurve"])
        self.assertEqual(100.0, punti["2024-07-01"]["benchmarkCurve"])
        self.assertEqual(100.0, punti["2024-08-01"]["twrCurve"])
        self.assertEqual(105.0, punti["2024-08-01"]["benchmarkCurve"])


if __name__ == "__main__":
    unittest.main()
