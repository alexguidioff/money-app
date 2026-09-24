"""Il backup di FastBudget letto come se fosse un estratto conto.

Il file vero di chi lo carica non entra qui: il repository e' pubblico, e quei
movimenti sono suoi. Il `.bak` lo costruisce il test, con numeri tondi e
inventati, e le tre tabelle di FastBudget con le colonne che il lettore legge -
se un giorno ne chiedera' una in piu', il file finto non ce l'ha e il test lo
dice invece di leggere un campo vuoto e tirare avanti.

Quello che si fissa qui sono le tre cose che il file non dice e il lettore
deduce: le due gambe di un trasferimento sono due righe da ricomporre, la
controparte puo' non esistere piu', le categorie sono un albero italiano da
mappare. Piu' il fuso: FastBudget scrive un istante in millisecondi, e la data
che si vede e' quella dell'orologio di chi guarda.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.database import Base
from app.fastbudget_import import leggi_backup

# I tre conti del file finto. Il valore dichiarato e' quello che le righe del
# file dicono conto per conto, contando ogni riga sul suo conto soltanto: e' il
# numero con cui FastBudget chiude, ed e' l'unico confronto che dice se il
# lettore ha tradotto bene.
CONTI = [
    ("Banca", 704.50, 1000.00, "EUR - €"),
    ("Conto2", 250.00, 0.0, "EUR - €"),
    ("Franchi", 205.00, 0.0, "CHF - CHF"),
]

CATEGORIE = [
    (1, "Spesa", None),
    (2, "Cibo/Bevande", None),
    (3, "Pranzi/Cene", 2),
    (4, "Altro (Spese)", None),
    (5, "Tasse", 4),
    (6, "Salario", None),
]

TRASFERIMENTO = "Transfer between accounts"


def ms(anno: int, mese: int, giorno: int, ora: int = 10) -> int:
    """L'istante che FastBudget scrive: millisecondi dall'epoca, in UTC.

    Le dieci del mattino UTC sono le stesse del mattino in Svizzera: e' l'ora in
    cui un movimento si registra, e serve a non far dipendere la data dal fuso
    quando il fuso non c'entra.
    """
    return int(datetime(anno, mese, giorno, ora, tzinfo=timezone.utc).timestamp() * 1000)


# (_id, verso 0/1, importo, categoria, conto, istante, controparte, nota)
MOVIMENTI = [
    (101, 0, 30.00, "Spesa", "Banca", ms(2026, 3, 1), None, "spesa vera"),
    (102, 1, 250.00, "Salario", "Banca", ms(2026, 3, 2), None, ""),
    (103, 0, 12.50, "Pranzi/Cene", "Banca", ms(2026, 3, 3), None, ""),
    (104, 0, 8.00, "Tasse", "Banca", ms(2026, 3, 4), None, ""),
    # Un pranzo registrato in entrata: la categoria e' quella delle spese, il
    # verso no. Non puo' finire nell'albero delle spese.
    (105, 1, 40.00, "Pranzi/Cene", "Banca", ms(2026, 3, 5), None, ""),
    # Le due gambe, distanti due giorni, con lo stesso importo.
    (110, 0, 100.00, TRASFERIMENTO, "Banca", ms(2026, 3, 6), "Conto2", ""),
    (111, 1, 100.00, TRASFERIMENTO, "Conto2", ms(2026, 3, 8), "Banca", ""),
    # Il giro che passa dal cambio: escono 200,00 euro e ne arrivano 180,00
    # franchi. Fra valute diverse gli importi non si possono confrontare.
    (112, 0, 200.00, TRASFERIMENTO, "Banca", ms(2026, 3, 10), "Franchi", ""),
    (113, 1, 180.00, TRASFERIMENTO, "Franchi", ms(2026, 3, 11), "Banca", ""),
    # Stessa valuta e stessa cifra a dieci giorni di distanza: e' lo stesso giro
    # di soldi registrato in ritardo, e accoppiarlo e' quello che tiene il saldo
    # di Banca dove FastBudget dice che e'.
    (114, 0, 50.00, TRASFERIMENTO, "Banca", ms(2026, 3, 20), "Conto2", ""),
    (115, 1, 50.00, TRASFERIMENTO, "Conto2", ms(2026, 3, 30), "Banca", ""),
    # "Vechio" non e' piu' fra i conti del file: la sua gamba di partenza non
    # c'e' piu', e i soldi sono usciti lo stesso.
    (116, 0, 70.00, TRASFERIMENTO, "Banca", ms(2026, 4, 1), "Vechio", ""),
    (117, 1, 25.00, TRASFERIMENTO, "Franchi", ms(2026, 4, 2), "Vechio", ""),
    # Due importi diversi nella stessa valuta: non sono lo stesso giro di soldi,
    # e ognuno resta la riga che il file scrive sul suo conto.
    (118, 0, 40.00, TRASFERIMENTO, "Banca", ms(2026, 5, 1), "Conto2", ""),
    (119, 1, 60.00, TRASFERIMENTO, "Conto2", ms(2026, 5, 2), "Banca", ""),
    # Le 00:30 a Zurigo sono le 22:30 del giorno prima in UTC: senza il fuso di
    # chi guarda, questo movimento cambierebbe giorno.
    (120, 0, 15.00, "Spesa", "Banca", ms(2026, 5, 31, ora=22) + 30 * 60 * 1000, None, ""),
]


class ConUnBackup(unittest.TestCase):
    """Un backup finto su disco, e una sessione per l'anteprima."""

    def setUp(self) -> None:
        self.cartella = tempfile.TemporaryDirectory()
        self.percorso = Path(self.cartella.name) / "fastbudget.bak"
        self._scrivi_backup()
        self.engine = create_engine("sqlite://")
        # Tutto lo schema: l'anteprima legge anche le regole di categorizzazione.
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.cartella.cleanup()

    def _scrivi_backup(self) -> None:
        """Un `.bak` con dentro le tre tabelle di FastBudget e le colonne lette."""
        conn = sqlite3.connect(self.percorso)
        conn.executescript(
            "CREATE TABLE account (_id INTEGER PRIMARY KEY, name TEXT, value REAL,"
            " initial_funds REAL, currency TEXT);"
            "CREATE TABLE categories_table (_id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER);"
            "CREATE TABLE income_or_expense (_id INTEGER PRIMARY KEY, i_e INTEGER, value REAL,"
            " category TEXT, account TEXT, date INTEGER, from_or_to TEXT, notes TEXT);")
        conn.executemany("INSERT INTO account VALUES (?,?,?,?,?)",
                         [(indice, nome, valore, iniziale, valuta)
                          for indice, (nome, valore, iniziale, valuta) in enumerate(CONTI, start=1)])
        conn.executemany("INSERT INTO categories_table VALUES (?,?,?)", CATEGORIE)
        conn.executemany("INSERT INTO income_or_expense VALUES (?,?,?,?,?,?,?,?)", MOVIMENTI)
        conn.commit()
        conn.close()

    def leggi(self, tz: str = "Europe/Zurich") -> dict:
        return leggi_backup(str(self.percorso), tz)

    def righe(self, tz: str = "Europe/Zurich") -> list[dict]:
        return self.leggi(tz)["transactions"]

    def riga(self, identificativo: int, tz: str = "Europe/Zurich") -> dict:
        trovate = [riga for riga in self.righe(tz) if riga["sourceRow"] == identificativo]
        self.assertEqual(1, len(trovate), f"riga {identificativo}")
        return trovate[0]


class BackupFastBudgetTests(ConUnBackup):
    def test_il_backup_non_si_tocca(self) -> None:
        # E' l'unica copia della storia di chi lo carica: il lettore apre in sola
        # lettura, e questo e' il modo di sapere che non ha scritto niente.
        prima = self.percorso.read_bytes()
        self.leggi()
        self.assertEqual(prima, self.percorso.read_bytes())

    def test_le_due_gambe_diventano_un_trasferimento_con_i_due_importi(self) -> None:
        trasferimento = self.riga(110)
        self.assertEqual("Transfers", trasferimento["transactionType"])
        self.assertEqual("Banca", trasferimento["accountName"])
        self.assertEqual("Conto2", trasferimento["destinationName"])
        self.assertEqual(100.00, trasferimento["rawAmount"])
        self.assertEqual(100.00, trasferimento["destinationAmount"])
        # La gamba di arrivo non e' rimasta una riga a se': sarebbe lo stesso
        # giro di soldi contato due volte.
        self.assertNotIn(111, [riga["sourceRow"] for riga in self.righe()])

    def test_il_giro_con_il_cambio_porta_i_due_importi(self) -> None:
        trasferimento = self.riga(112)
        self.assertEqual("Franchi", trasferimento["destinationName"])
        self.assertEqual(200.00, trasferimento["rawAmount"])
        self.assertEqual(180.00, trasferimento["destinationAmount"])

    def test_stessa_cifra_e_stessa_valuta_si_accoppiano_anche_a_dieci_giorni(self) -> None:
        # Fra l'uscita e la registrazione dell'arrivo puo' passare piu' di una
        # settimana: lasciarle separate muoverebbe due volte i soldi di Banca.
        trasferimento = self.riga(114)
        self.assertEqual("2026-03-20", trasferimento["occurredOn"])
        self.assertEqual(50.00, trasferimento["destinationAmount"])
        self.assertNotIn(115, [riga["sourceRow"] for riga in self.righe()])

    def test_importi_diversi_nella_stessa_valuta_restano_due_righe(self) -> None:
        uscita, entrata = self.riga(118), self.riga(119)
        self.assertEqual(("Banca", "Conto2"), (uscita["accountName"], uscita["destinationName"]))
        self.assertEqual(40.00, uscita["rawAmount"])
        self.assertIsNone(uscita["destinationAmount"])
        # L'arrivo senza partenza si scrive al contrario: i soldi sono arrivati
        # su Conto2 da Banca, e il file lo dice.
        self.assertEqual(("Banca", "Conto2"), (entrata["accountName"], entrata["destinationName"]))
        self.assertEqual(60.00, entrata["rawAmount"])

    def test_la_controparte_che_non_esiste_piu(self) -> None:
        uscita = self.riga(116)
        self.assertEqual("Vechio", uscita["destinationName"])
        self.assertIsNone(uscita["destinationAmount"])
        # L'arrivo da un conto che non c'e' piu' si legge al contrario.
        entrata = self.riga(117)
        self.assertEqual("Vechio", entrata["accountName"])
        self.assertEqual("Franchi", entrata["destinationName"])
        conti = {conto["name"]: conto for conto in self.leggi()["accounts"]}
        self.assertFalse(conti["Vechio"]["inFile"])
        self.assertTrue(conti["Franchi"]["inFile"])
        self.assertEqual("CHF", conti["Franchi"]["currency"])

    def test_i_saldi_del_file_tornano_dalle_righe_importate(self) -> None:
        # La prova che conta: ogni conto arriva dove FastBudget dice che e'
        # arrivato. Un accoppiamento sbagliato muove due volte i soldi, e qui si
        # vede subito.
        letto = self.leggi()
        # Anche i conti che il file nomina e non ha piu' - "Vechio" - tengono un
        # saldo: le righe ci passano sopra, e senza di loro il conto girato
        # intorno a loro non tornerebbe.
        saldi = {conto["name"]: conto["startingBalance"] or 0.0 for conto in letto["accounts"]}
        for riga in letto["transactions"]:
            if riga["type"] == "transfer":
                saldi[riga["accountName"]] -= riga["rawAmount"]
                saldi[riga["destinationName"]] += riga["destinationAmount"] or riga["rawAmount"]
            else:
                segno = 1 if riga["transactionType"] == "Income" else -1
                saldi[riga["accountName"]] += segno * riga["rawAmount"]
        for nome, dichiarato, _, _ in CONTI:
            self.assertEqual(dichiarato, saldi[nome], nome)

    def test_le_categorie_prendono_il_nome_dell_albero_dell_app(self) -> None:
        self.assertEqual("Shopping", self.riga(101)["category"])
        self.assertIsNone(self.riga(101)["categoryParent"])
        # La sottocategoria mappata: "Pranzi/Cene" e' un ristorante.
        self.assertEqual("Restaurants", self.riga(103)["category"])
        self.assertEqual("Food & Dining", self.riga(103)["categoryParent"])
        # Un nome che nell'app non esiste tiene il suo, e nasce sotto la radice
        # mappata della voce di cui era figlio: "Tasse" sotto "Altro (Spese)".
        self.assertEqual("Tasse", self.riga(104)["category"])
        self.assertEqual("Other Expenses", self.riga(104)["categoryParent"])

    def test_una_spesa_in_entrata_non_finisce_nell_albero_delle_spese(self) -> None:
        entrata = self.riga(105)
        self.assertEqual("Income", entrata["transactionType"])
        self.assertEqual("Other Income", entrata["category"])
        self.assertIsNone(entrata["categoryParent"])
        self.assertEqual("Salary", self.riga(102)["category"])
        self.assertEqual("Employment", self.riga(102)["categoryParent"])

    def test_la_data_e_quella_dell_orologio_di_chi_guarda(self) -> None:
        # Le 00:30 a Zurigo sono ancora il giorno prima in UTC: leggere in UTC
        # sposterebbe indietro ogni movimento fatto dopo le 23.
        self.assertEqual("2026-06-01", self.riga(120, "Europe/Zurich")["occurredOn"])
        self.assertEqual("2026-05-31", self.riga(120, "")["occurredOn"])


class AnteprimaBackupTests(ConUnBackup):
    """La rotta: le righe dell'anteprima, piu' i conti che il file nomina."""

    def anteprima(self, contenuto: bytes | None = None, tz: str = "Europe/Zurich") -> dict:
        return asyncio.run(main.import_fastbudget_backup(
            UploadFile(filename="fastbudget.bak", file=BytesIO(contenuto or self.percorso.read_bytes())),
            tz, self.session))

    def test_l_anteprima_porta_le_righe_e_i_conti(self) -> None:
        anteprima = self.anteprima()
        self.assertTrue(anteprima["success"])
        self.assertEqual(13, anteprima["count"])
        conti = {conto["name"]: conto for conto in anteprima["accounts"]}
        self.assertEqual("CHF", conti["Franchi"]["currency"])
        self.assertEqual(0.0, conti["Franchi"]["startingBalance"])
        self.assertEqual(205.00, conti["Franchi"]["declaredBalance"])
        self.assertEqual(1000.00, conti["Banca"]["startingBalance"])

    def test_le_righe_portano_quello_che_sa_solo_il_backup(self) -> None:
        righe = self.anteprima()["transactions"]
        trasferimento = [riga for riga in righe if riga["sourceRow"] == 112][0]
        self.assertEqual(180.00, trasferimento["destinationAmount"])
        self.assertFalse(trasferimento["duplicate"])
        tassa = [riga for riga in righe if riga["sourceRow"] == 104][0]
        self.assertEqual("Other Expenses", tassa["categoryParent"])

    def test_un_file_che_non_e_un_backup_non_si_legge(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            self.anteprima(b"non e' un database")
        self.assertEqual(422, errore.exception.status_code)
        self.assertEqual("statementParseFailed", errore.exception.detail)

    def test_l_estensione_la_controlla_il_caricamento(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            asyncio.run(main.import_fastbudget_backup(
                UploadFile(filename="estratto.pdf", file=BytesIO(self.percorso.read_bytes())),
                "Europe/Zurich", self.session))
        self.assertEqual(400, errore.exception.status_code)
        self.assertEqual("uploadFormat", errore.exception.detail)


if __name__ == "__main__":
    unittest.main()
