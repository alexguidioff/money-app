"""Le valute in cui si rilegge il patrimonio: vuoto e' una scelta.

Togliere l'ultima valuta la rimetteva li': il valore di partenza tornava al
posto della scelta, la card continuava a mostrare tre valute e nessuno diceva
niente. La differenza che conta e' fra una riga assente - un account creato
senza `prepara_account`, che vale il valore di sempre - e una riga svuotata,
che e' quello che l'utente ha appena chiesto.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import display_currencies
from app.database import Base
from app.models import AppSetting


class ValuteDelPatrimonioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _imposta(self, valore: str) -> None:
        self.session.add(AppSetting(key="net_worth_currencies",
                                    label="Valute nel patrimonio", value=valore))
        self.session.commit()

    def test_senza_la_riga_valgono_le_tre_di_partenza(self) -> None:
        self.assertEqual(["USD", "CHF", "BTC"], display_currencies(self.session))

    def test_toglierle_tutte_le_toglie_davvero(self) -> None:
        self._imposta("")
        self.assertEqual([], display_currencies(self.session))

    def test_doppioni_maiuscole_e_l_euro_fuori(self) -> None:
        # L'euro e' la valuta dei conti: si rilegge in euro senza chiederlo, e
        # chiederlo di nuovo non deve produrre una seconda card identica.
        self._imposta(" chf,CHF,EUR,usd ")
        self.assertEqual(["CHF", "USD"], display_currencies(self.session))


if __name__ == "__main__":
    unittest.main()
