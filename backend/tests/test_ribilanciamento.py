"""Deriva e riequilibrio: numeri tondi, inventati, e i bordi del calcolo.

Tre strumenti da 10.000, 20.000 e 30.000 su un totale di 60.000, cosi' i pesi
si leggono a mente. Quello che qui puo' rompersi in silenzio non e' la
sottrazione ma il **denominatore**: includere gli strumenti non classificati, o
normalizzare pesi che non tornano, produce numeri plausibili e sbagliati.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import investments_dashboard
from app.database import Base
from app.models import InvestmentInstrument, InvestmentTransaction
from app.ribilanciamento import PosizionePeso, riequilibrio


def posizione(nome: str, valore: str, obiettivo: str | None, aperta: bool = True) -> PosizionePeso:
    return PosizionePeso(nome=nome, valore=Decimal(valore),
                         obiettivo=None if obiettivo is None else Decimal(obiettivo), aperta=aperta)


def riga(esito, nome: str):
    for voce in esito.righe:
        if voce.nome == nome:
            return voce
    raise AssertionError(f"nessuna riga per {nome}: {esito.righe}")


class RiequilibrioTests(unittest.TestCase):
    def test_pesi_rispettati_non_propongono_niente(self) -> None:
        esito = riequilibrio([posizione("A", "10000", "0.166667"),
                              posizione("B", "20000", "0.333333"),
                              posizione("C", "30000", "0.5")])
        self.assertEqual((), esito.righe)
        self.assertEqual((), esito.avvisi)
        self.assertEqual(Decimal("60000"), esito.totale)

    def test_chi_e_sopra_peso_vende_l_importo_esatto(self) -> None:
        # C vale 30.000 su 60.000: mezzo portafoglio contro un obiettivo del
        # 30%. La differenza sono 12.000, e il segno positivo vuol dire vendere.
        esito = riequilibrio([posizione("A", "10000", "0.2"),
                              posizione("B", "20000", "0.5"),
                              posizione("C", "30000", "0.3")])
        voce = riga(esito, "C")
        self.assertEqual(Decimal("0.5"), voce.peso_attuale)
        self.assertEqual(Decimal("0.3"), voce.obiettivo)
        self.assertEqual(Decimal("0.2"), voce.deriva)
        self.assertEqual(Decimal("12000.00"), voce.importo)
        # Qui tutte e tre sono fuori soglia, e allora le vendite pagano gli
        # acquisti: si torna in linea senza versare denaro nuovo.
        self.assertEqual(Decimal("0.00"), sum((r.importo for r in esito.righe), Decimal("0")))

    def test_chi_non_ha_un_obiettivo_resta_fuori_dal_denominatore(self) -> None:
        # B vale 30.000 e non e' nel piano: contarlo farebbe sembrare A e C
        # sotto peso del 25% invece che in linea.
        esito = riequilibrio([posizione("A", "10000", "0.5"),
                              posizione("B", "30000", None),
                              posizione("C", "10000", "0.5")])
        self.assertEqual(Decimal("20000"), esito.totale)
        self.assertEqual((), esito.righe)

    def test_pesi_che_non_sommano_a_uno_avvisano_ma_i_numeri_restano(self) -> None:
        esito = riequilibrio([posizione("A", "10000", "0.4"), posizione("B", "20000", "0.5")])
        self.assertIn("pesi_non_sommano_a_cento", esito.avvisi)
        self.assertEqual(Decimal("0.9"), esito.pesi_dichiarati)
        # Restano: chi legge deve vedere anche quanto e' fuori, non solo che
        # c'e' un errore di configurazione.
        self.assertEqual(Decimal("0.333333"), riga(esito, "A").peso_attuale)
        self.assertEqual(Decimal("5000.00"), riga(esito, "B").importo)

    def test_deriva_sotto_la_soglia_non_si_propone(self) -> None:
        # Un punto di deriva: si corregge con una commissione che costa piu' di
        # quanto sistemi.
        esito = riequilibrio([posizione("A", "21000", "0.34"),
                              posizione("B", "19500", "0.33"),
                              posizione("C", "19500", "0.33")])
        self.assertEqual((), esito.righe)

    def test_deriva_esattamente_sulla_soglia_non_si_propone(self) -> None:
        # La soglia e' inclusiva: due punti esatti non sono "oltre". Uno in piu'
        # basta invece a far comparire la riga, ed e' li' che sta il confine.
        sulla_soglia = riequilibrio([posizione("A", "30000", "0.52"), posizione("B", "30000", "0.48")])
        self.assertEqual((), sulla_soglia.righe)
        oltre = riequilibrio([posizione("A", "32600", "0.52"), posizione("B", "27400", "0.48")])
        self.assertEqual(["A", "B"], [v.nome for v in oltre.righe])

    def test_una_posizione_chiusa_non_compare(self) -> None:
        esito = riequilibrio([posizione("A", "10000", "0.5"),
                              posizione("B", "20000", "0.5", aperta=False)])
        self.assertEqual(Decimal("10000"), esito.totale)
        self.assertEqual(["A"], [v.nome for v in esito.righe])

    def test_portafoglio_vuoto_non_divide_per_zero(self) -> None:
        for posizioni in ([], [posizione("A", "0", "1")], [posizione("A", "10000", None)]):
            esito = riequilibrio(posizioni)
            self.assertEqual(Decimal("0"), esito.totale)
            self.assertEqual((), esito.righe)


class RiequilibrioNellaRispostaTests(unittest.TestCase):
    """Il blocco arriva davvero nella pagina, con i pesi della tabella sopra.

    I pesi obiettivo sono frazioni (0,6 = 60%): la formula del piano sottrae il
    peso al peso, e una tolleranza di 0,005 su una scala 0-100 non vorrebbe dire
    niente. Senza prezzi in cache il valore di mercato e' l'ultimo prezzo di
    transazione, come per le posizioni.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            InvestmentInstrument(name="ETF Mondo", provider_symbol="SWDA.MI", currency="EUR",
                                 target_weight=Decimal("0.6")),
            InvestmentInstrument(name="Obbligazioni", provider_symbol="AGGH.MI", currency="EUR",
                                 target_weight=Decimal("0.4")),
            InvestmentInstrument(name="Da classificare", provider_symbol="XXX.MI", currency="EUR"),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _possiede(self, nome: str, quote: str, prezzo: str) -> None:
        self.session.add(InvestmentTransaction(name=nome, transaction_type="Buy", occurred_on=date(2024, 1, 1),
                                               units=Decimal(quote), price=Decimal(prezzo),
                                               amount=Decimal(quote) * Decimal(prezzo)))
        self.session.commit()

    def test_in_linea_con_gli_obiettivi_non_propone_niente(self) -> None:
        self._possiede("ETF Mondo", "60", "100")
        self._possiede("Obbligazioni", "40", "100")
        riordino = investments_dashboard(self.session)["rebalance"]
        self.assertEqual(10000.0, riordino["total"])
        self.assertEqual(1.0, riordino["declaredWeight"])
        self.assertEqual([], riordino["rows"])
        self.assertEqual([], riordino["warnings"])

    def test_chi_e_fuori_peso_compare_con_l_importo_firmato(self) -> None:
        self._possiede("ETF Mondo", "80", "100")
        self._possiede("Obbligazioni", "20", "100")
        riordino = investments_dashboard(self.session)["rebalance"]
        self.assertEqual(["ETF Mondo", "Obbligazioni"], [r["name"] for r in riordino["rows"]])
        vendita, acquisto = riordino["rows"]
        self.assertEqual(0.8, vendita["currentWeight"])
        self.assertEqual(0.6, vendita["targetWeight"])
        self.assertEqual(0.2, vendita["drift"])
        # Il segno e' il verso: positivo sopra il peso obiettivo, quindi da
        # vendere. Le due operazioni si compensano e il totale non si muove.
        self.assertEqual(2000.0, vendita["amount"])
        self.assertEqual(-2000.0, acquisto["amount"])

    def test_lo_strumento_senza_obiettivo_non_entra_nel_denominatore(self) -> None:
        self._possiede("ETF Mondo", "60", "100")
        self._possiede("Obbligazioni", "40", "100")
        self._possiede("Da classificare", "500", "100")
        riordino = investments_dashboard(self.session)["rebalance"]
        self.assertEqual(10000.0, riordino["total"])
        self.assertEqual([], riordino["rows"])


if __name__ == "__main__":
    unittest.main()
