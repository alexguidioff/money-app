"""I filtri dei movimenti, ora che li applica il database e non piu' il browser.

Erano codice dell'interfaccia, e nel trasloco un dettaglio si era gia' perso:
filtrando per conto sparivano i trasferimenti *arrivati* su quel conto.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import transactions
from app.database import Base
from app.models import Transaction


def _tx(giorno: date, tipo: str, categoria: str, importo: str, conto: str,
        destinazione: str | None = None, dettagli: str | None = None,
        template: bool = False, obiettivo: str | None = None) -> Transaction:
    return Transaction(occurred_on=giorno, effective_on=giorno, transaction_type=tipo,
                       category=categoria, amount=Decimal(importo), account_type="Bank",
                       account_name=conto, destination_name=destinazione, details=dettagli,
                       is_recurring_template=template, goal=obiettivo)


class FiltriMovimentiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            _tx(date(2026, 7, 3), "Expenses", "Groceries", "20", "Conto", dettagli="spesa Intermarché"),
            _tx(date(2026, 7, 20), "Income", "Stipendio", "1500", "Conto"),
            _tx(date(2026, 8, 5), "Transfers", "Giroconto", "100", "Conto", destinazione="Libretto",
                obiettivo="Casa"),
            _tx(date(2025, 7, 9), "Expenses", "Groceries", "35", "Libretto"),
            # Un template di ricorrenza non e' un movimento: non deve comparire.
            _tx(date(2026, 7, 15), "Expenses", "Groceries", "999", "Conto", template=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _categorie(self, **filtri) -> list[str]:
        return [voce["category"] for voce in transactions(100, self.session, **filtri)["items"]]

    def test_i_template_restano_fuori(self) -> None:
        risultato = transactions(100, self.session)
        self.assertEqual(risultato["total"], 4)

    def test_paginazione(self) -> None:
        prima = transactions(2, self.session)
        seconda = transactions(2, self.session, offset=2)
        self.assertEqual(len(prima["items"]), 2)
        self.assertEqual(prima["total"], 4)
        self.assertEqual(seconda["offset"], 2)
        # Nessuna riga ripetuta fra le due pagine.
        self.assertFalse({v["id"] for v in prima["items"]} & {v["id"] for v in seconda["items"]})

    def test_anno_e_mese(self) -> None:
        self.assertEqual(len(self._categorie(year=2026, month=7)), 2)
        self.assertEqual(self._categorie(year=2025), ["Groceries"])
        # Il mese da solo attraversa gli anni, come il menu a tendina consente.
        self.assertEqual(len(self._categorie(month=7)), 3)

    def test_ricerca_su_descrizione_categoria_e_data(self) -> None:
        self.assertEqual(self._categorie(search="intermarché"), ["Groceries"])
        self.assertEqual(self._categorie(search="stipendio"), ["Stipendio"])
        self.assertEqual(len(self._categorie(search="2026-07")), 2)

    def test_il_conto_vale_anche_come_destinazione(self) -> None:
        # Il giroconto parte da "Conto" e arriva su "Libretto": filtrando per
        # Libretto deve comparire, insieme alla spesa del 2025.
        self.assertEqual(len(self._categorie(account_name="Libretto")), 2)

    def test_filtro_per_obiettivo(self) -> None:
        self.assertEqual(self._categorie(goal="Casa"), ["Giroconto"])
        # Il confronto ignora maiuscole e spazi, come quello sui conti.
        self.assertEqual(self._categorie(goal=" casa "), ["Giroconto"])

    def test_senza_obiettivo(self) -> None:
        # "-" e' l'unico modo di trovare i movimenti da taggare: il campo e'
        # vuoto, quindi la ricerca testuale non li distingue dagli altri.
        senza = self._categorie(goal="-")
        self.assertNotIn("Giroconto", senza)
        self.assertEqual(3, len(senza))

    def test_obiettivi_disponibili_non_dipendono_dai_filtri(self) -> None:
        # Come per gli anni: filtrando per "senza obiettivo" la tendina deve
        # continuare a offrire "Casa", altrimenti non si torna indietro.
        self.assertEqual(transactions(100, self.session, goal="-")["goals"], ["Casa"])

    def test_anni_disponibili_non_dipendono_dai_filtri(self) -> None:
        # Filtrando il 2025 l'elenco anni deve continuare a offrire il 2026,
        # altrimenti non ci sarebbe modo di tornare indietro.
        self.assertEqual(transactions(100, self.session, year=2025)["years"], ["2026", "2025"])


if __name__ == '__main__':
    unittest.main()
