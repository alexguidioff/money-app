"""Primario e riserva: chi risponde vince, e chi ha risposto resta scritto.

Il fornitore esterno non si chiama davvero: si sostituiscono le due funzioni di
rete e si guarda cosa finisce in cache. Quello che qui si puo' rompere in
silenzio non e' la quotazione ma la *provenienza*: una riga salvata sotto il
nome del fornitore chiesto invece di quello che ha risposto rende impossibile
capire da dove viene un prezzo quando le due fonti non concordano.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.market_cache import DEFAULT_PROVIDER, get_or_fetch_price
from app.market_data import RESERVE_KEY_ENV, RESERVE_PROVIDER, MarketDataError, MarketQuote
from app.models import MarketPrice

CHIAVE = "chiave-di-prova"
CHIESTA = date(2024, 5, 3)
OSSERVATA = date(2024, 5, 2)


class CatenaDeiFornitoriTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _quotazione(self, provider: str) -> MarketQuote:
        return MarketQuote(symbol="SWDA.MI", price=1234.56, currency="EUR",
                           observed_on=OSSERVATA, provider=provider)

    def _righe(self) -> list[MarketPrice]:
        return list(self.session.scalars(select(MarketPrice).order_by(MarketPrice.observed_on)))

    def test_il_primario_che_risponde_non_chiama_la_riserva(self) -> None:
        riserva = Mock(side_effect=AssertionError("la riserva non va chiamata"))
        with patch.dict(os.environ, {RESERVE_KEY_ENV: CHIAVE}), \
                patch("app.market_cache.fetch_yahoo_quote", return_value=self._quotazione(DEFAULT_PROVIDER)), \
                patch("app.market_cache.fetch_reserve_quote", riserva):
            prezzo = get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
        riserva.assert_not_called()
        self.assertEqual(DEFAULT_PROVIDER, prezzo.provider)
        # Il tipo dipende dal dialetto (sqlite restituisce float, Postgres
        # Decimal): qui interessa che il prezzo sia arrivato intero.
        self.assertEqual(Decimal("1234.56"), Decimal(str(prezzo.price)))
        self.assertEqual({DEFAULT_PROVIDER}, {riga.provider for riga in self._righe()})

    def test_se_il_primario_solleva_si_usa_la_riserva_e_il_suo_nome_resta_in_cache(self) -> None:
        primario = Mock(side_effect=MarketDataError("quote source unreachable", code="unreachable"))
        with patch.dict(os.environ, {RESERVE_KEY_ENV: CHIAVE}), \
                patch("app.market_cache.fetch_yahoo_quote", primario), \
                patch("app.market_cache.fetch_reserve_quote", return_value=self._quotazione(RESERVE_PROVIDER)):
            prezzo = get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
        primario.assert_called_once()
        self.assertEqual(RESERVE_PROVIDER, prezzo.provider)
        # Due righe: il giorno osservato dalla riserva e il "mirror" per il
        # giorno chiesto. Entrambe portano il fornitore che ha risposto davvero.
        self.assertEqual([OSSERVATA, CHIESTA], [riga.observed_on for riga in self._righe()])
        self.assertEqual({RESERVE_PROVIDER}, {riga.provider for riga in self._righe()})

    def test_se_falliscono_tutti_e_due_non_si_scrive_niente(self) -> None:
        with patch.dict(os.environ, {RESERVE_KEY_ENV: CHIAVE}), \
                patch("app.market_cache.fetch_yahoo_quote",
                      Mock(side_effect=MarketDataError("quote source unreachable", code="unreachable"))), \
                patch("app.market_cache.fetch_reserve_quote",
                      Mock(side_effect=MarketDataError("symbol not found", code="no_data"))):
            with self.assertRaises(MarketDataError) as errore:
                get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
        # Il motivo e' di tutti e due: non e' stata una fonte sola a tacere, e
        # chi legge deve poterlo dire invece di mostrare un portafoglio fermo al
        # giorno prima.
        self.assertIn("quote source unreachable", str(errore.exception))
        self.assertIn("symbol not found", str(errore.exception))
        self.assertEqual("unreachable", errore.exception.code)
        self.assertEqual([], self._righe())

    def test_senza_chiave_la_riserva_non_esiste(self) -> None:
        riserva = Mock(side_effect=AssertionError("senza chiave la riserva non esiste"))
        with patch.dict(os.environ, {RESERVE_KEY_ENV: ""}), \
                patch("app.market_cache.fetch_yahoo_quote",
                      Mock(side_effect=MarketDataError("quote source unreachable", code="unreachable"))), \
                patch("app.market_cache.fetch_reserve_quote", riserva):
            with self.assertRaises(MarketDataError) as errore:
                get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
        riserva.assert_not_called()
        # Stesso errore di prima che la catena esistesse: una riserva che non
        # c'e' non ha niente da aggiungere al motivo.
        self.assertEqual("quote source unreachable", str(errore.exception))
        self.assertEqual([], self._righe())

    def test_il_prezzo_della_riserva_si_riusa_invece_di_tornare_in_rete(self) -> None:
        # La riga sta in cache sotto il nome della riserva: se la lettura
        # cercasse solo il fornitore predefinito, ogni richiesta ripartirebbe
        # da capo e la cache non servirebbe piu' a niente.
        with patch.dict(os.environ, {RESERVE_KEY_ENV: CHIAVE}), \
                patch("app.market_cache.fetch_yahoo_quote",
                      Mock(side_effect=MarketDataError("quote source unreachable", code="unreachable"))), \
                patch("app.market_cache.fetch_reserve_quote",
                      return_value=self._quotazione(RESERVE_PROVIDER)) as riserva:
            get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
            righe_scritte = len(self._righe())
            riserva.reset_mock()
            prezzo = get_or_fetch_price(self.session, "SWDA.MI", CHIESTA)
        riserva.assert_not_called()
        self.assertEqual(righe_scritte, len(self._righe()))
        self.assertEqual(RESERVE_PROVIDER, prezzo.provider)


if __name__ == "__main__":
    unittest.main()
