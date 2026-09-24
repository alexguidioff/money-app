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
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import mock

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import main
from app.calculation_engine import calculate_account_balance
from app.database import Base
from app.fastbudget_import import leggi_backup
from app.market_data import MarketDataError
from app.models import Account, Category, MarketPrice, Transaction

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

    def sporca(self, identificativo: int, valore: float) -> None:
        """Riscrive l'importo di una riga del file con un numero non tondo.

        E' quello che il file fa da solo sugli importi che ha calcolato: la
        colonna e' in virgola mobile, e il risultato di una conversione torna coi
        decimali che il binario gli lascia. Si riscrive la riga invece di
        aggiungerne una al file finto: il file finto e' la storia di tutti i
        test, e un importo in piu' cambierebbe i saldi che gli altri fissano.
        """
        conn = sqlite3.connect(self.percorso)
        conn.execute("UPDATE income_or_expense SET value = ? WHERE _id = ?", (valore, identificativo))
        conn.commit()
        conn.close()

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

    def test_una_cifra_calcolata_dal_file_arriva_ai_centesimi(self) -> None:
        # Due forme dello stesso guaio: i decimali che una conversione si lascia
        # dietro, e il numero tondo che cade una unita' sotto la sua
        # rappresentazione. Nessuna delle due e' una cifra di denaro.
        self.sporca(101, 30.0000002)
        self.sporca(103, 12.500000000000002)
        self.assertEqual(30.00, self.riga(101)["rawAmount"])
        self.assertEqual(-30.00, self.riga(101)["amount"])
        self.assertEqual(12.50, self.riga(103)["rawAmount"])

    def test_l_estensione_la_controlla_il_caricamento(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            asyncio.run(main.import_fastbudget_backup(
                UploadFile(filename="estratto.pdf", file=BytesIO(self.percorso.read_bytes())),
                "Europe/Zurich", self.session))
        self.assertEqual(400, errore.exception.status_code)
        self.assertEqual("uploadFormat", errore.exception.detail)


class SalvataggioBackupTests(ConUnBackup):
    """Il salvataggio: i conti che nascono, le categorie, il secondo import."""

    def setUp(self) -> None:
        super().setUp()
        # La pausa fra due richieste alla fonte non fa aspettare nessuno, e
        # nessun test esce dalla macchina: la fonte si sostituisce.
        pausa = mock.patch.object(main, "SOURCE_REQUEST_PAUSE_SECONDS", 0)
        pausa.start()
        self.addCleanup(pausa.stop)

    def anteprima(self) -> dict:
        return asyncio.run(main.import_fastbudget_backup(
            UploadFile(filename="fastbudget.bak", file=BytesIO(self.percorso.read_bytes())),
            "Europe/Zurich", self.session))

    def salva(self, anteprima: dict | None = None) -> dict:
        """Salva quello che l'anteprima ha mostrato, com'e' tornato indietro.

        Il blocco dei conti si rimanda intero, con i campi che il salvataggio non
        legge: e' quello che fa la pagina, ed e' il modo di sapere che i campi in
        piu' non lo disturbano.
        """
        anteprima = anteprima or self.anteprima()
        return main.import_fastbudget_transactions(
            main.FastBudgetImportPayload(transactions=anteprima["transactions"], accounts=anteprima["accounts"]),
            self.session)

    def conti(self) -> dict:
        return {conto.name: conto for conto in self.session.scalars(select(Account)).all()}

    def test_i_conti_nascono_con_la_valuta_e_il_saldo_iniziale_del_file(self) -> None:
        esito = self.salva()
        conti = self.conti()
        self.assertEqual({"Banca", "Conto2", "Franchi", "Vechio"}, set(conti))
        self.assertEqual(("EUR", Decimal("1000.00")), (conti["Banca"].currency, conti["Banca"].starting_balance))
        self.assertEqual("CHF", conti["Franchi"].currency)
        self.assertEqual(["Banca", "Conto2", "Franchi", "Vechio"],
                         [conto["name"] for conto in esito["accountsCreated"]])
        # La controparte che il file non ha piu' nasce fuori dal patrimonio: il
        # suo saldo viene da una gamba sola, e sommarlo sarebbe sommarlo due volte
        # a meta'.
        self.assertFalse(conti["Vechio"].counts_in_net_worth)
        self.assertTrue(conti["Banca"].counts_in_net_worth)

    def test_ogni_conto_arriva_dove_fastbudget_dice(self) -> None:
        # La prova che conta: il saldo che l'app calcola dai movimenti importati
        # e' quello che FastBudget mostra sui suoi tre conti.
        self.salva()
        movimenti = self.session.scalars(select(Transaction)).all()
        dichiarati = {nome: valore for nome, valore, _, _ in CONTI}
        # "Vechio" non e' nel file: quello che l'app puo' dire di lui e' solo
        # quello che le righe hanno mosso - settanta arrivati da Banca,
        # venticinque usciti verso Franchi. E' un saldo a una gamba: le partenze
        # e gli arrivi che il file non ha registrato non ci sono, e per questo
        # sta fuori dal patrimonio.
        dichiarati["Vechio"] = 45.00
        for nome, valore in dichiarati.items():
            conto = self.conti()[nome]
            self.assertEqual(Decimal(str(valore)).quantize(Decimal("0.01")),
                             calculate_account_balance(conto.starting_balance, nome, movimenti), nome)

    def test_una_cifra_non_tonda_non_fa_perdere_il_movimento(self) -> None:
        # Il movimento che il salvataggio rifiutava non arrivava mai: il conto
        # restava corto di quella cifra, e l'anteprima diceva "un errore" come se
        # il file fosse storto invece che il numero.
        self.sporca(101, 30.0000002)
        esito = self.salva()
        self.assertEqual([], esito["errors"])
        riga = self.session.scalar(select(Transaction).where(Transaction.source_row == 101))
        self.assertEqual(Decimal("30.00"), riga.amount)

    def test_il_giroconto_si_salva_con_i_due_importi(self) -> None:
        self.salva()
        cambio = self.session.scalar(select(Transaction).where(Transaction.source_row == 112))
        self.assertEqual(("Banca", "Franchi", "Transfers"),
                         (cambio.account_name, cambio.destination_name, cambio.transaction_type))
        self.assertEqual((Decimal("200.00"), Decimal("180.00")), (cambio.amount, cambio.destination_amount))
        # Un movimento normale non porta un secondo importo: il campo vuoto vuol
        # dire "lo stesso importo", che e' il comportamento di sempre.
        self.assertIsNone(self.session.scalar(select(Transaction).where(Transaction.source_row == 101)).destination_amount)

    def test_il_secondo_import_non_aggiunge_niente(self) -> None:
        self.salva()
        primi = list(self.session.scalars(select(Transaction.id).order_by(Transaction.id)))
        secondo = self.salva()
        self.assertEqual(13, secondo["alreadyImported"])
        self.assertEqual(0, secondo["saved"])
        # Non sono scartate: le righe c'erano gia', e l'import e' andato bene.
        self.assertEqual([], secondo["errors"])
        self.assertEqual(primi, list(self.session.scalars(select(Transaction.id).order_by(Transaction.id))))

    def test_l_anteprima_di_un_file_gia_importato_lo_dice(self) -> None:
        self.assertFalse(any(riga.get("alreadyImported") for riga in self.anteprima()["transactions"]))
        self.salva()
        righe = self.anteprima()["transactions"]
        self.assertTrue(all(riga["alreadyImported"] for riga in righe))
        # E dice anche con quale movimento: sono la stessa riga, e chi importa la
        # vede accanto a quella che gia' c'e'.
        self.assertTrue(all(riga["duplicateOf"]["id"] for riga in righe))

    def test_una_categoria_nuova_nasce_sotto_la_radice_mappata(self) -> None:
        self.salva()
        tasse = self.session.scalar(select(Category).where(Category.name == "Tasse"))
        padre = self.session.get(Category, tasse.parent_id)
        self.assertEqual("Other Expenses", padre.name)
        # Il verso lo decide il padre: una voce nata sotto le spese e' una spesa.
        self.assertEqual("expense", tasse.scope)
        # La sottocategoria mappata invece non nasce radice: sta sotto la sua.
        ristoranti = self.session.scalar(select(Category).where(Category.name == "Restaurants"))
        self.assertEqual("Food & Dining", self.session.get(Category, ristoranti.parent_id).name)
        # Un nome che l'app ha gia' non si duplica.
        self.assertEqual(1, len(self.session.scalars(select(Category).where(Category.name == "Restaurants")).all()))

    def test_un_conto_che_esiste_gia_si_riusa_e_la_sua_valuta_non_si_tocca(self) -> None:
        self.session.add(Account(source_group="bank", name="Franchi", currency="EUR",
                                 starting_balance=Decimal("10.00"), current_balance=Decimal("10.00"),
                                 is_active=True))
        self.session.commit()
        anteprima = self.anteprima()
        esistente = [conto for conto in anteprima["accounts"] if conto["name"] == "Franchi"][0]
        # L'anteprima lo dice prima di salvare: la valuta del file e' un'altra, e
        # dopo sarebbe una correzione su ogni movimento che ci e' finito dentro.
        self.assertEqual(("CHF", "EUR"), (esistente["currency"], esistente["existingCurrency"]))
        esito = self.salva(anteprima)
        self.assertEqual([{"name": "Franchi", "code": "accountCurrencyMismatch",
                           "currency": "EUR", "fileCurrency": "CHF"}], esito["warnings"])
        franchi = [conto for conto in self.conti().values() if conto.name == "Franchi"]
        self.assertEqual(1, len(franchi))
        self.assertEqual(("EUR", Decimal("10.00")), (franchi[0].currency, franchi[0].starting_balance))
        # Il movimento va sul conto che c'e' gia': la valuta del conto resta la
        # sua, e la riga dice che il file ne dichiarava un'altra.
        cambio = self.session.scalar(select(Transaction).where(Transaction.source_row == 112))
        self.assertEqual("Franchi", cambio.destination_name)

    def test_la_valuta_nuova_porta_il_suo_cambio(self) -> None:
        with mock.patch.object(main, "fetch_price_history",
                               return_value=[(date(2026, 1, 31), 0.95, "CHF")]) as scarico:
            esito = self.salva()
        self.assertEqual([], esito["fxMissing"])
        self.assertEqual("EURCHF=X", scarico.call_args.args[0])
        prezzi = self.session.scalars(select(MarketPrice).where(MarketPrice.symbol == "EURCHF=X")).all()
        self.assertEqual(1, len(prezzi))

    def test_una_valuta_senza_cambio_si_dichiara_e_l_import_riesce(self) -> None:
        # Senza storico il franco non si converte, e convertirlo a 1 - un franco
        # per un euro - sarebbe un numero sbagliato che non si lamenta. L'import
        # entra lo stesso, e la risposta dice cosa manca.
        with mock.patch.object(main, "fetch_price_history",
                               side_effect=MarketDataError("irraggiungibile", code="unreachable")):
            esito = self.salva()
        self.assertEqual(13, esito["saved"])
        self.assertEqual(["CHF"], esito["fxMissing"])


if __name__ == "__main__":
    unittest.main()
