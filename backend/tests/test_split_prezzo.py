"""Uno split non crea valore, nemmeno per uno strumento senza quotazione.

Il caso che il codice sbagliava: le quote si moltiplicavano e il prezzo di
ripiego - l'ultimo prezzo di scambio, usato quando non c'e' una quotazione -
restava quello di prima. Un 2:1 raddoppiava il valore di mercato e inventava
una plusvalenza del cento per cento.

Per uno strumento quotato non si vedeva, perche' il prezzo di mercato il
frazionamento ce l'ha gia' dentro: e' proprio il caso senza quotazione, quello
che nessuno guarda, a rompersi.
"""
import unittest
from datetime import date
from decimal import Decimal

from app.calculation_engine import investment_positions


def _riga(numero: int, giorno: int, tipo: str, *, importo: str = "0",
          quote: str = "0", prezzo: str | None = None):
    return {
        "id": numero, "occurred_on": date(2026, 1, giorno), "name": "Senza quotazione",
        "ticker": None, "transaction_type": tipo, "amount": Decimal(importo),
        "units": Decimal(quote), "price": None if prezzo is None else Decimal(prezzo),
        "currency": "EUR",
    }


def _posizione(righe, prezzi=None):
    risultato = investment_positions(righe, {}, prezzi or {}, {})
    return risultato[0]


class SplitSenzaQuotazione(unittest.TestCase):
    def test_uno_split_non_cambia_il_valore(self) -> None:
        # 10 quote a 100 = 1000. Dopo un 2:1: 20 quote a 50, sempre 1000.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="10", prezzo="100"),
            _riga(2, 2, "Split", quote="2"),
        ])
        self.assertEqual(Decimal("20"), posizione["units"])
        self.assertEqual(Decimal("1000.00"), posizione["market_value"])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])

    def test_uno_split_non_inventa_una_plusvalenza(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="10", prezzo="100"),
            _riga(2, 2, "Split", quote="2"),
        ])
        self.assertEqual(Decimal("0.00"), posizione["unrealized_gain"])

    def test_un_raggruppamento_non_dimezza_il_valore(self) -> None:
        # 1:10 al contrario: 10 quote diventano 1, e il prezzo si decuplica.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="10", prezzo="100"),
            _riga(2, 2, "Split", quote="0.1"),
        ])
        self.assertEqual(Decimal("1"), posizione["units"])
        self.assertEqual(Decimal("1000.00"), posizione["market_value"])

    def test_con_una_quotazione_vera_il_prezzo_lo_dice_il_mercato(self) -> None:
        # Qui il prezzo non si tocca: il mercato il frazionamento ce l'ha gia'.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="10", prezzo="100"),
            _riga(2, 2, "Split", quote="2"),
        ], prezzi={"senza quotazione": Decimal("60")})
        self.assertEqual(Decimal("20"), posizione["units"])
        self.assertEqual(Decimal("1200.00"), posizione["market_value"])


if __name__ == "__main__":
    unittest.main()
