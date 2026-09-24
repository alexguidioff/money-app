"""Il patrimonio somma conti in valute diverse, quindi si converte.

Un conto in franchi vale in franchi - e' il numero che si legge sulla sua riga -
ma "quanto vali" e' una cifra sola, in euro. Si converte al cambio del mese a
cui il saldo si riferisce, non a quello di oggi: con il cambio di oggi la storia
si riscriverebbe a ogni movimento del cambio, e una casa comprata nel 2024
varrebbe in euro quello che vale adesso.

Un conto la cui valuta non ha un cambio **non entra nel totale**: sommarlo a 1
(un franco per un euro) sarebbe la sola cosa peggiore del non sommarlo, perche'
il numero sembrerebbe giusto. Il motivo lo dice l'avviso.

I cambi dei test sono scritti a mano nel listino: numeri tondi e inventati.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import _net_worth_breakdown, accounts, net_worth
from app.database import Base
from app.models import Account, MarketPrice
from app.notifications import costruisci


def _cambio(session: Session, codice: str, giorno: date, valore: str) -> None:
    """Un cambio nel listino, come lo scriverebbe lo scarico delle quotazioni."""
    session.add(MarketPrice(symbol=f"EUR{codice}=X", observed_on=giorno, price=Decimal(valore)))


class PatrimonioInValutaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(name="Conto", source_group="bank", currency="EUR",
                    starting_balance=Decimal("1000"), current_balance=Decimal("1000"), counts_in_net_worth=True),
            # Il conto in franchi: 500 franchi, con il cambio a 1,10 franchi per
            # euro valgono 454,55 euro - non 500.
            Account(name="Revolut", source_group="bank", currency="CHF",
                    starting_balance=Decimal("500"), current_balance=Decimal("500"), counts_in_net_worth=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _patrimonio(self, anno: int, mese: int) -> float:
        return _net_worth_breakdown(self.session, anno, mese)["total"]

    def test_il_conto_in_franchi_entra_convertito(self) -> None:
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.10")
        self.session.commit()
        # 1000 + 500 / 1,10 = 1454,55
        self.assertAlmostEqual(self._patrimonio(2026, 7), 1454.55, places=2)

    def test_il_cambio_e_quello_del_mese_non_quello_di_oggi(self) -> None:
        # A giugno un franco valeva 1,25 (500 franchi = 400 euro), a luglio 1,00
        # (500 euro). Il patrimonio di giugno deve usare il cambio di giugno.
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.25")
        _cambio(self.session, "CHF", date(2026, 7, 31), "1.00")
        self.session.commit()
        self.assertAlmostEqual(self._patrimonio(2026, 6), 1400.0, places=2)
        self.assertAlmostEqual(self._patrimonio(2026, 7), 1500.0, places=2)

    def test_senza_cambio_il_conto_resta_fuori_e_l_avviso_lo_dice(self) -> None:
        # Nessun cambio per il franco: il totale e' il solo conto in euro, non
        # 1500 (che vorrebbe dire un franco per un euro).
        self.assertAlmostEqual(self._patrimonio(2026, 7), 1000.0, places=2)
        avvisi = costruisci(self.session)
        avviso = next(a for a in avvisi if a["code"] == "accountMissingFx")
        self.assertEqual("CHF", avviso["params"]["currency"])
        self.assertIn("Revolut", avviso["params"]["accounts"])

    def test_con_il_cambio_l_avviso_sparisce(self) -> None:
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.10")
        self.session.commit()
        self.assertEqual([], [a for a in costruisci(self.session) if a["code"] == "accountMissingFx"])

    def test_ogni_riga_di_conto_porta_il_suo_valore_in_euro(self) -> None:
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.10")
        self.session.commit()
        righe = {riga["name"]: riga for riga in accounts(at="2026-07-31", session=self.session)["items"]}
        # La riga del conto in franchi si legge in franchi...
        self.assertEqual(500.0, righe["Revolut"]["value"])
        # ...e i totali della pagina, che sommano conti diversi, hanno l'euro.
        self.assertEqual(454.55, righe["Revolut"]["valueInEuro"])
        self.assertEqual(1000.0, righe["Conto"]["valueInEuro"])

    def test_senza_cambio_il_valore_in_euro_e_nullo(self) -> None:
        righe = {riga["name"]: riga for riga in accounts(at="2026-07-31", session=self.session)["items"]}
        self.assertIsNone(righe["Revolut"]["valueInEuro"])
        # Il conto in euro non dipende da nessun cambio.
        self.assertEqual(1000.0, righe["Conto"]["valueInEuro"])

    def test_panoramica_e_patrimonio_dicono_lo_stesso_numero(self) -> None:
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.10")
        self.session.commit()
        self.assertAlmostEqual(self._patrimonio(2026, 7),
                               net_worth(2026, 7, 12, self.session)["totals"]["netWorth"], places=2)

    def test_un_conto_fuori_dal_patrimonio_non_ha_avvisi(self) -> None:
        # Gli accantonamenti non entrano nel patrimonio, quindi non c'e' niente
        # da convertire e non c'e' niente da dire. Il cambio del conto che invece
        # ci entra c'e', se no l'avviso arriverebbe per quello.
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.10")
        self.session.add(Account(name="Salvadanaio", source_group="bank", currency="SEK",
                                 starting_balance=Decimal("300"), current_balance=Decimal("300"),
                                 counts_in_net_worth=False))
        self.session.commit()
        self.assertEqual([], [a for a in costruisci(self.session) if a["code"] == "accountMissingFx"])


if __name__ == '__main__':
    unittest.main()
