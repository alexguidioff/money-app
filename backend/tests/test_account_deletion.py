"""La cancellazione di un account deve svuotare tutte le sue tabelle.

Non e' un dettaglio: una tabella dimenticata lascerebbe righe senza padrone,
invisibili a chiunque, che il prossimo utente creato con lo stesso id si
ritroverebbe fra i propri dati. Nessun errore lo segnalerebbe.

Il test non tocca il database: confronta l'elenco usato dalla cancellazione con
quello che il database usa per l'isolamento fra utenti. Sono per definizione lo
stesso insieme - le tabelle che appartengono a una persona - e devono restare
allineati anche quando se ne aggiunge una.
"""

from __future__ import annotations

import unittest

from app.auth import TABELLE_PERSONALI
from app.migrations import PER_UTENTE


class TestElencoTabellePersonali(unittest.TestCase):
    def test_copre_tutte_le_tabelle_per_utente(self) -> None:
        dimenticate = set(PER_UTENTE) - set(TABELLE_PERSONALI)
        self.assertEqual(set(), dimenticate,
                         f"tabelle con una politica di riga ma mai svuotate: {sorted(dimenticate)}")

    def test_non_contiene_tabelle_condivise(self) -> None:
        # Le quotazioni e i profili degli strumenti sono di tutti: cancellarli
        # insieme a una persona toglierebbe dati anche agli altri.
        di_troppo = set(TABELLE_PERSONALI) - set(PER_UTENTE)
        self.assertEqual(set(), di_troppo,
                         f"tabelle svuotate che non appartengono a una persona: {sorted(di_troppo)}")

    def test_nessun_doppione(self) -> None:
        self.assertEqual(len(TABELLE_PERSONALI), len(set(TABELLE_PERSONALI)))


if __name__ == "__main__":
    unittest.main()
