"""Il peso obiettivo di uno strumento: frazione fra 0 e 1, sei decimali, azzerabile.

La scala e' l'errore facile di questa funzione: il fixture dei contratti scriveva
80 dove il database vuole 0,80, e senza un controllo il numero non dava errore -
arrivava a video come "8000%".
"""
import unittest
from decimal import Decimal

from fastapi import HTTPException

from app.main import _peso_obiettivo


class PesoObiettivo(unittest.TestCase):
    def test_frazione_valida(self):
        self.assertEqual(Decimal("0.175000"), _peso_obiettivo(0.175))

    def test_sei_decimali_non_due(self):
        # `_to_decimal` arrotonda ai centesimi: 0,175 ci diventerebbe 0,18 e il
        # peso di un ETF sarebbe sbagliato di mezzo punto.
        self.assertEqual(Decimal("0.175000"), _peso_obiettivo("0.175"))
        self.assertNotEqual(Decimal("0.18"), _peso_obiettivo("0.175"))

    def test_estremi_ammessi(self):
        self.assertEqual(Decimal("0.000000"), _peso_obiettivo(0))
        self.assertEqual(Decimal("1.000000"), _peso_obiettivo(1))

    def test_percentuale_scambiata_per_frazione(self):
        # Il caso vero: 80 invece di 0,80.
        with self.assertRaises(HTTPException) as caso:
            _peso_obiettivo(80)
        self.assertEqual("targetWeightRange", caso.exception.detail["code"])

    def test_negativo(self):
        with self.assertRaises(HTTPException):
            _peso_obiettivo(-0.1)

    def test_non_numerico(self):
        with self.assertRaises(HTTPException) as caso:
            _peso_obiettivo("un terzo")
        self.assertEqual("targetWeightInvalid", caso.exception.detail["code"])


class ClassificazioneAzzerabile(unittest.TestCase):
    """Assente vuol dire "non toccare", nullo vuol dire "togli l'obiettivo"."""

    def payload(self, **campi):
        from app.main import InstrumentClassificationPayload
        return InstrumentClassificationPayload(**campi)

    def test_assente_non_e_nullo(self):
        p = self.payload(asset_class="ETF")
        self.assertNotIn("target_weight", p.model_fields_set)

    def test_nullo_esplicito_si_distingue(self):
        p = self.payload(target_weight=None)
        self.assertIn("target_weight", p.model_fields_set)
        self.assertIsNone(p.target_weight)


if __name__ == "__main__":
    unittest.main()
