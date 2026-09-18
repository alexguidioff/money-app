"""La simulazione a percorsi, provata da sola: motore puro, nessun database.

Il primo test e' il piu' importante: lega la simulazione al motore
deterministico. Senza volatilita' i due devono dire lo stesso numero, al
centesimo, anno per anno: se si rompe, la pagina mostra una banda costruita su
un'altra matematica rispetto alla linea centrale, e nessuno se ne accorge
guardando il grafico.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.fire_engine import Flusso, piano_fire
from app.fire_montecarlo import EsitoMonteCarlo, _seme, simula

# Un profilo inventato, con numeri tondi: ritiro a 65, rendimento reale 6%.
# I dati veri di una persona non stanno in un file di test.
CAPITALE = 150000.0
SPESE = 24000.0
VERSAMENTI = 6000.0
ETA_OGGI, ETA_RITIRO, ETA_FINE = 28, 65, 90
RENDIMENTO = 0.06
RENDITA = 12000.0


def entrate(eta_ritiro_rendita: int = 65) -> dict[int, float]:
    return {eta: (RENDITA if eta >= eta_ritiro_rendita else 0.0) for eta in range(ETA_OGGI, 100)}


def esegui(**sostituzioni) -> EsitoMonteCarlo:
    parametri = dict(capitale=CAPITALE, spese_annue=SPESE, versamenti_annui=VERSAMENTI,
                     entrate_per_eta=entrate(), eta_oggi=ETA_OGGI, eta_ritiro=ETA_RITIRO,
                     eta_fine=ETA_FINE, rendimento_medio=RENDIMENTO, volatilita=0.15,
                     aliquota_prelievo=0.0)
    parametri.update(sostituzioni)
    return simula(**parametri)


class VolatilitaZeroTests(unittest.TestCase):
    """A volatilita' zero la simulazione **e'** il piano deterministico."""

    def _serie_deterministica(self):
        piano = piano_fire(
            capitale=Decimal(str(CAPITALE)), spese_annue=Decimal(str(SPESE)),
            flussi=[Flusso("AVS", "annuity", Decimal(str(RENDITA)), 65)],
            eta_oggi=ETA_OGGI, eta_ritiro=ETA_RITIRO, rendimento_reale=Decimal("0.06"),
            swr=Decimal("0.04"), aliquota_prelievo=Decimal("0"),
            versamenti_annui=Decimal(str(VERSAMENTI)), eta_fine_proiezione=100)
        return {p.eta: p.capitale for p in piano.serie}

    def test_la_serie_segue_il_piano_anno_per_anno(self) -> None:
        deterministico = self._serie_deterministica()
        esito = esegui(volatilita=0.0)
        for punto in esito.percentili[50]:
            self.assertEqual(deterministico[punto.eta], punto.capitale, f"eta' {punto.eta}")

    def test_i_tre_percentili_coincidono(self) -> None:
        esito = esegui(volatilita=0.0)
        self.assertEqual(esito.percentili[10], esito.percentili[50])
        self.assertEqual(esito.percentili[50], esito.percentili[90])

    def test_il_capitale_non_si_esaurisce_e_il_successo_e_totale(self) -> None:
        # Il piano vero, a 6% costante, arriva a 90 anni con i soldi in banca.
        esito = esegui(volatilita=0.0)
        self.assertEqual(Decimal(1), esito.successo)
        self.assertIsNone(esito.eta_esaurimento_mediana)


class RipetibilitaTests(unittest.TestCase):
    def test_due_chiamate_danno_gli_stessi_numeri(self) -> None:
        # Il seme si ricava dai parametri: la pagina deve dire lo stesso numero
        # a ogni apertura, non uno diverso a ogni ricarica.
        self.assertEqual(esegui().percentili, esegui().percentili)
        self.assertEqual(esegui().successo, esegui().successo)

    def test_un_parametro_diverso_cambia_il_seme(self) -> None:
        base = (CAPITALE, SPESE, VERSAMENTI, ETA_OGGI, ETA_RITIRO, ETA_FINE, RENDIMENTO, 0.15, 0)
        self.assertEqual(_seme(*base), _seme(*base))
        self.assertNotEqual(_seme(*base), _seme(*((CAPITALE + 1.0,) + base[1:])))

    def test_un_seme_diverso_cambia_i_percorsi(self) -> None:
        # E' il modo in cui la pagina puo' variare la simulazione senza
        # cambiarne le ipotesi: il seme esplicito si aggiunge alla firma.
        self.assertNotEqual(esegui(seme=1).percentili[50], esegui(seme=2).percentili[50])


class VolatilitaTests(unittest.TestCase):
    def test_piu_volatilita_non_aumenta_il_successo(self) -> None:
        successi = [esegui(volatilita=vol).successo for vol in (0.10, 0.15, 0.20)]
        self.assertEqual(successi, sorted(successi, reverse=True))
        # A volatilita' azionaria realistica un piano su venti non regge: la
        # vista deterministica diceva "mai esaurito" tre volte su tre.
        self.assertLess(successi[1], Decimal(1))

    def test_i_percentili_restano_in_ordine_ogni_anno(self) -> None:
        esito = esegui()
        for basso, medio, alto in zip(esito.percentili[10], esito.percentili[50],
                                      esito.percentili[90], strict=True):
            self.assertLessEqual(basso.capitale, medio.capitale, f"eta' {basso.eta}")
            self.assertLessEqual(medio.capitale, alto.capitale, f"eta' {basso.eta}")
        self.assertEqual([p.eta for p in esito.percentili[50]],
                         list(range(ETA_OGGI, ETA_FINE + 1)))

    def test_nessun_saldo_diventa_negativo_con_volatilita_massima(self) -> None:
        # Senza il taglio a -95% una gaussiana con sigma 100% scende sotto -100%
        # in circa un percorso su sette, e il saldo risalirebbe dal nulla.
        esito = esegui(volatilita=1.0)
        for quota in (10, 50, 90):
            for punto in esito.percentili[quota]:
                self.assertGreaterEqual(punto.capitale, 0, f"p{quota} a {punto.eta} anni")


class EsaurimentoTests(unittest.TestCase):
    def test_capitale_enorme_regge_sempre(self) -> None:
        esito = esegui(capitale=50_000_000.0)
        self.assertEqual(Decimal(1), esito.successo)
        self.assertIsNone(esito.eta_esaurimento_mediana)

    def test_capitale_minuscolo_non_regge_mai(self) -> None:
        esito = esegui(capitale=1000.0, spese_annue=50000.0, versamenti_annui=0.0,
                       entrate_per_eta={}, eta_ritiro=ETA_OGGI)
        self.assertEqual(Decimal(0), esito.successo)
        self.assertEqual(ETA_OGGI, esito.eta_esaurimento_mediana)

    def test_esaurito_resta_esaurito_anche_con_rendimenti_alti(self) -> None:
        # Fallisce al primo anno di ritiro; da li' in poi il saldo e' zero e
        # moltiplicarlo per un rendimento ottimo lo lascia a zero.
        esito = esegui(capitale=1000.0, spese_annue=50000.0, versamenti_annui=0.0,
                       entrate_per_eta={}, eta_ritiro=ETA_OGGI,
                       rendimento_medio=0.20, volatilita=0.05)
        self.assertEqual(Decimal(0).quantize(Decimal("0.01")), esito.percentili[90][-1].capitale)
        self.assertEqual(Decimal(0).quantize(Decimal("0.01")), esito.percentili[50][-1].capitale)


class ValidazioneTests(unittest.TestCase):
    def test_volatilita_fuori_intervallo(self) -> None:
        for valore in (-0.01, 1.01):
            with self.assertRaises(ValueError):
                esegui(volatilita=valore)

    def test_percorsi_fuori_intervallo(self) -> None:
        for valore in (99, 20001):
            with self.assertRaises(ValueError):
                esegui(percorsi=valore)

    def test_aliquota_che_annullerebbe_il_netto(self) -> None:
        with self.assertRaises(ValueError):
            esegui(aliquota_prelievo=1.0)

    def test_ritiro_fuori_dalla_finestra(self) -> None:
        with self.assertRaises(ValueError):
            esegui(eta_ritiro=ETA_OGGI - 1)
