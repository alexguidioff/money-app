"""Le regole che propongono la categoria di un movimento in importazione.

Si prova prima il motore da solo - quali regole combaciano e in che ordine -
poi l'innesto nell'anteprima e le rotte. Sono due cose diverse: il motore e'
logica pura, l'innesto e' il punto in cui le regole toccano un import vero.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.categorization import applica, carica_regole, normalizza, scartate
from app.database import Base
from app.main import PENDING_CATEGORY, _resolve_category
from app.models import CategorizationRule


class MotoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _regola(self, pattern: str, categoria: str = "Groceries", **campi) -> CategorizationRule:
        regola = CategorizationRule(pattern=pattern, category=categoria, **campi)
        self.session.add(regola)
        self.session.commit()
        return regola

    def _applica(self, descrizione: str, tipo: str = "Expenses", importo: str = "10") -> tuple[str, str] | None:
        return applica(carica_regole(self.session), descrizione, tipo, Decimal(importo))

    def test_nessuna_regola_lascia_il_movimento_da_categorizzare(self) -> None:
        self.assertIsNone(self._applica("spesa lidl"))
        self.assertEqual(PENDING_CATEGORY, _resolve_category(None, "Expenses"))

    def test_testo_contenuto_ignora_maiuscole_e_spazi_doppi(self) -> None:
        self._regola("spesa lidl")
        self.assertEqual(("Groceries", "spesa lidl"), self._applica("SPESA  Lidl   via roma"))

    def test_vince_la_regola_con_posizione_minore_non_la_piu_specifica(self) -> None:
        self._regola("lidl", "Other", position=5)
        self._regola("spesa lidl", "Groceries", position=1)
        self.assertEqual(("Groceries", "spesa lidl"), self._applica("spesa lidl"))

    def test_una_regola_spenta_non_si_applica(self) -> None:
        self._regola("spesa lidl", active=False)
        self.assertEqual([], carica_regole(self.session))
        self.assertIsNone(self._applica("spesa lidl"))

    def test_una_regex_combacia_e_il_testo_non_e_una_regex(self) -> None:
        self._regola(r"^pos \d+", "Commissions", is_regex=True)
        self.assertEqual(("Commissions", r"^pos \d+"), self._applica("POS 12345 caffe"))
        self.session.query(CategorizationRule).delete()
        self.session.commit()
        # Lo stesso pattern senza la spunta e' testo: il punto e' un punto, non
        # "un carattere qualunque".
        self._regola("a.b")
        self.assertIsNone(self._applica("axb"))
        self.assertEqual(("Groceries", "a.b"), self._applica("a.b caffe"))

    def test_una_regex_malformata_non_fa_esplodere_l_import(self) -> None:
        # La scrittura la rifiuta: per arrivare qui va forzata nel database.
        self._regola("(senza chiusura", is_regex=True)
        regole = carica_regole(self.session)
        self.assertEqual(["(senza chiusura"], scartate(regole))
        self.assertIsNone(applica(regole, "(senza chiusura", "Expenses", Decimal("10")))

    def test_importo_assoluto_fra_minimo_e_massimo_estremi_inclusi(self) -> None:
        self._regola("affitto", "Housing", min_amount=Decimal("10"), max_amount=Decimal("50"))
        self.assertEqual(("Housing", "affitto"), self._applica("affitto", importo="10"))
        self.assertEqual(("Housing", "affitto"), self._applica("affitto", importo="50"))
        self.assertEqual(("Housing", "affitto"), self._applica("affitto", importo="-30"))
        self.assertIsNone(self._applica("affitto", importo="9.99"))
        self.assertIsNone(self._applica("affitto", importo="50.01"))

    def test_il_tipo_valorizzato_non_tocca_l_altro_tipo(self) -> None:
        self._regola("stipendio", "Salary", transaction_type="Income")
        self.assertIsNone(self._applica("stipendio", tipo="Expenses"))
        self.assertEqual(("Salary", "stipendio"), self._applica("stipendio", tipo="Income"))

    def test_un_trasferimento_non_ha_categoria_e_nessuna_regola_lo_tocca(self) -> None:
        self._regola("giroconto")
        self.assertEqual("_", _resolve_category("Groceries", "Transfers", "Groceries"))
        self.assertEqual("_", _resolve_category(None, "Investment"))

    def test_la_categoria_della_riga_vince_sulla_regola(self) -> None:
        # Una regola riempie un vuoto, non sostituisce quello che c'e' gia'.
        self.assertEqual("Other", _resolve_category("Other", "Expenses", "Groceries"))
        self.assertEqual("Groceries", _resolve_category(None, "Expenses", "Groceries"))
        self.assertEqual(PENDING_CATEGORY, _resolve_category(PENDING_CATEGORY, "Expenses", None))

    def test_la_descrizione_normalizzata_e_solo_minuscole_e_spazi(self) -> None:
        self.assertEqual("spesa lidl via roma", normalizza("  Spesa   Lidl  via roma "))


if __name__ == "__main__":
    unittest.main()
