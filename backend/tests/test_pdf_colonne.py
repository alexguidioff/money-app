"""Estratti conto PDF senza tabella disegnata: testo allineato in colonne.

I PDF si costruiscono qui con dati inventati, imitando le due impaginazioni
incontrate davvero: un importo con segno e descrizioni su piu' righe centrate
sulla riga dell'importo; colonne separate per entrate e uscite, con la data
spezzata su due righe e un riepilogo finale che non va letto.
"""

from __future__ import annotations

import tempfile
import unittest

from reportlab.pdfgen.canvas import Canvas

from app.pdf_importer import BankStatementParser


def _pdf(righe: list[tuple[float, float, str]]) -> list[dict]:
    """Scrive (x, y dall'alto, testo) in un PDF A4 e lo rilegge col parser."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as file:
        tela = Canvas(file.name, pagesize=(595, 842))
        tela.setFont("Helvetica", 9)
        for x, y, testo in righe:
            tela.drawString(x, 842 - y, testo)
        tela.save()
        return BankStatementParser.extract_transactions_from_pdf(file.name)


class ImportoConSegnoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.movimenti = _pdf([
            (23, 150, "Saldo iniziale"), (25, 170, "1000.00 €"),
            (23, 200, "Cod Transazione"), (123, 200, "Data"), (223, 200, "Descrizione"), (488, 200, "Importo"),
            (223, 222, "Hai ricevuto un bonifico da"), (223, 233, "MARIO ROSSI - iban mittente"),
            (23, 245, "10000001"), (123, 245, "2026-08-20"), (223, 245, "IT00X0000000000000000000000 - causale"), (533, 245, "250.00 €"),
            (223, 257, "regalo - CRO"), (223, 268, "000111222333"),
            (223, 281, "Hai effettuato un bonifico a favore di"), (223, 292, "Anna Bianchi - iban beneficiario"),
            (23, 298, "10000002"), (123, 298, "2026-08-07"), (524, 298, "-2800.00 €"),
            (223, 304, "IT11Y1111111111111111111111 - causale - CRO"), (223, 315, "444555666"),
            (23, 343, "10000003"), (123, 343, "2026-08-05"), (223, 343, "Addebito domiciliazione Palestra"), (535, 343, "-24.80 €"),
        ])

    def test_legge_importi_date_e_segno(self) -> None:
        self.assertEqual([(m["occurredOn"], m["amount"]) for m in self.movimenti],
                         [("2026-08-20", 250.0), ("2026-08-07", -2800.0), ("2026-08-05", -24.8)])

    def test_la_descrizione_su_piu_righe_resta_del_suo_movimento(self) -> None:
        primo, secondo, terzo = (m["description"] for m in self.movimenti)
        self.assertTrue(primo.startswith("Hai ricevuto un bonifico da MARIO ROSSI"))
        self.assertTrue(primo.endswith("000111222333"))
        self.assertTrue(secondo.startswith("Hai effettuato") and secondo.endswith("444555666"))
        self.assertEqual(terzo, "Addebito domiciliazione Palestra")

    def test_il_codice_non_finisce_nella_data_ne_nella_descrizione(self) -> None:
        self.assertFalse(any("1000000" in m["description"] for m in self.movimenti))


class EntrateEUsciteSeparateTests(unittest.TestCase):
    def setUp(self) -> None:
        intestazione = [(74, 0, "DATA"), (104, 0, "TIPO"), (155, 0, "DESCRIZIONE"),
                        (380, 0, "IN ENTRATA"), (440, 0, "IN USCITA"), (501, 0, "SALDO")]
        righe = [(x, 150, t) for x, _, t in intestazione]
        movimenti = [("01 lug", "Premio", "Cash reward", "2,84 €", None, "1.002,84 €"),
                     ("04 lug", "Transazione", "NEGOZIO DI PROVA 123", None, "16,99 €", "985,85 €"),
                     ("05 lug", "Bonifico", "Incoming transfer from Anna Bianchi", "1.200,00 €", None, "2.185,85 €")]
        for i, (giorno, tipo, descrizione, entrata, uscita, saldo) in enumerate(movimenti):
            y = 180 + i * 32
            righe += [(74, y - 4, giorno), (74, y + 4, "2026"), (104, y - 4, tipo), (155, y, descrizione), (484, y, saldo)]
            righe += [(385, y, entrata)] if entrata else [(445, y, uscita)]
        # Il riepilogo dopo l'elenco ha anch'esso date e importi: non sono movimenti.
        righe += [(74, 300, "PANORAMICA DEL SALDO"), (74, 330, "02 lug 2026"), (155, 330, "Fondo monetario"),
                  (385, 330, "11,95 €"), (445, 330, "1,00 €"), (484, 330, "11,95 €")]
        self.movimenti = _pdf(righe)

    def test_la_colonna_decide_il_segno(self) -> None:
        self.assertEqual([(m["occurredOn"], m["amount"], m["description"]) for m in self.movimenti], [
            ("2026-07-01", 2.84, "Cash reward"),
            ("2026-07-04", -16.99, "NEGOZIO DI PROVA 123"),
            ("2026-07-05", 1200.0, "Incoming transfer from Anna Bianchi"),
        ])


if __name__ == "__main__":
    unittest.main()
