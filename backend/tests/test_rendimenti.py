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

from app.rendimenti import (
    FLUSSI_SENZA_CAMBIO_DI_SEGNO,
    NESSUN_FLUSSO,
    PREZZO_MANCANTE,
    STORIA_TROPPO_CORTA,
    Flusso,
    Rendimento,
    Valutazione,
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


if __name__ == "__main__":
    unittest.main()
