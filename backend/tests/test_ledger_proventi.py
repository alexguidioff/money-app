"""I movimenti di solo contante del ledger: dividendi, interessi, commissioni,
versamenti.

Prima di questi rami il motore scartava ogni riga con zero quote, quindi un
dividendo non esisteva: non alzava niente e non si vedeva da nessuna parte. Qui
si guarda cosa fa al costo, alle quote e al denaro versato - e cosa non deve
fare: un dividendo non e' un acquisto.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.calculation_engine import investment_positions
from app.core_routes import _contributions_by_month
from app.database import Base
from app.models import InvestmentTransaction


def _riga(id: int, giorno: int, tipo: str, *, nome: str = "ETF", importo: str = "0",
          quote: str | None = None, ticker: str | None = None) -> dict:
    return {"id": id, "occurred_on": date(2026, giorno, 1), "name": nome, "ticker": ticker,
            "transaction_type": tipo, "amount": Decimal(importo),
            "units": Decimal(quote) if quote is not None else None, "currency": "EUR"}


def _posizione(righe: list[dict], nome: str = "ETF") -> dict:
    return next(posizione for posizione in investment_positions(righe) if posizione["name"] == nome)


class ProventiTests(unittest.TestCase):
    def test_un_dividendo_non_tocca_quote_ne_costo(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 3, "Dividend", importo="30"),
        ])
        self.assertEqual(Decimal("100"), posizione["units"])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])
        self.assertEqual(Decimal("1000.00"), posizione["net_contributed"])
        self.assertEqual(Decimal("30.00"), posizione["income_received"])

    def test_un_dividendo_su_uno_strumento_mai_comprato_non_crea_una_posizione(self) -> None:
        # Il ticker dice che la riga parla di uno strumento: se nessuno l'ha mai
        # comprato, quella riga non e' una posizione e non deve comparire in
        # tabella con zero quote, che poi non si chiude ne' si cancella.
        posizioni = investment_positions([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 3, "Dividend", nome="Fantasma", importo="12", ticker="FANT"),
        ])
        self.assertEqual(["ETF"], [posizione["name"] for posizione in posizioni])

    def test_l_interesse_senza_ticker_e_i_proventi_del_conto(self) -> None:
        # Gli interessi non hanno uno strumento: si registrano come dividendo
        # senza ticker, col nome del conto. Il conto non ha quote ma ha i suoi
        # proventi, ed e' li' che si leggono.
        conto = _posizione([_riga(1, 5, "Dividend", nome="IBKR", importo="12.50")], nome="IBKR")
        self.assertEqual(Decimal("0"), conto["units"])
        self.assertEqual(Decimal("0.00"), conto["cost_basis"])
        self.assertEqual(Decimal("12.50"), conto["income_received"])

    def test_un_dividendo_senza_ticker_si_attacca_alla_posizione_che_c_e(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 3, "Dividend", importo="25"),
        ])
        self.assertEqual(Decimal("100"), posizione["units"])
        self.assertEqual(Decimal("25.00"), posizione["income_received"])

    def test_una_commissione_staccata_abbassa_il_versato_non_il_costo(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 2, "Fee", importo="5"),
        ])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])
        self.assertEqual(Decimal("995.00"), posizione["net_contributed"])
        self.assertEqual(Decimal("5.00"), posizione["fees_paid"])

    def test_un_tipo_sconosciuto_non_tocca_la_posizione(self) -> None:
        # Versamento e prelievo sono stati tolti dai tipi validi: se una riga
        # cosi' arrivasse comunque - da un import, da un database vecchio - il
        # motore la deve ignorare, non attribuirle quote o costo.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 2, "Deposit", importo="500"),
        ])
        self.assertEqual(Decimal("1000.00"), posizione["net_contributed"])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])
        self.assertEqual(Decimal("100"), posizione["units"])

    def test_il_guadagno_realizzato_resta_quello_della_vendita(self) -> None:
        # Il dividendo e' incassato, non realizzato: sommarlo al risultato della
        # vendita direbbe che il portafoglio ha reso piu' di quanto ha reso.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100"),
            _riga(2, 2, "Dividend", importo="30"),
            _riga(3, 3, "Sell", importo="480", quote="40"),
        ])
        self.assertEqual(Decimal("80.00"), posizione["realized_gain"])
        self.assertEqual(Decimal("30.00"), posizione["income_received"])


class VersamentiMensiliTests(unittest.TestCase):
    """I versamenti mensili sono denaro nuovo, non reddito del portafoglio."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_un_dividendo_non_e_un_versamento(self) -> None:
        self.session.add_all([
            InvestmentTransaction(name="ETF", transaction_type="Buy", amount=Decimal("1000"),
                                  units=Decimal("100"), occurred_on=date(2026, 1, 10)),
            InvestmentTransaction(name="ETF", transaction_type="Dividend", amount=Decimal("15"),
                                  occurred_on=date(2026, 1, 20)),
            InvestmentTransaction(name="ETF", transaction_type="Sell", amount=Decimal("200"),
                                  units=Decimal("20"), occurred_on=date(2026, 2, 10)),
        ])
        self.session.commit()
        versamenti = _contributions_by_month(self.session)
        self.assertEqual(800.0, sum(riga["amount"] for riga in versamenti))
        self.assertEqual(1000.0, next(riga["amount"] for riga in versamenti if riga["period"] == "2026-01"))
