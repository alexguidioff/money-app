"""Le colonne del CSV le sceglie chi importa.

L'euristica non sparisce: diventa una proposta che si puo' correggere. Quindi
la prima cosa da fissare e' che senza mappatura niente cambi, e la seconda che
una mappatura scelta a mano vinca davvero - anche su un file che l'euristica
non sa leggere.

Numeri tondi e inventati: questo repository e' pubblico.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from io import BytesIO

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.csv_importer import CSVStatementParser
from app.database import Base


ESTRATTO = ("Data;Descrizione;Importo\n"
            "06/09/2026;Stipendio;1.500,00\n"
            "31/08/2026;Spesa;-1.234,56\n")

# Un file con le colonne giuste e i nomi sbagliati: l'euristica riconosce la
# data e la descrizione, ma non che "Somma" e' l'importo.
SENZA_IMPORTO = ("Data;Descrizione;Somma\n"
                 "06/09/2026;Stipendio;1500\n"
                 "31/08/2026;Spesa;-1234\n")

# Dare e avere: due colonne separate, e nessuna delle due si chiama come i nomi
# che l'euristica cerca.
DARE_AVERE = ("Data;Causale;Dare;Avere\n"
              "01/03/2026;Stipendio;;2.000,00\n"
              "02/03/2026;Spesa;30,00;\n")

DATA_ILLEGGIBILE = ("Data;Causale;Somma\n"
                    "non-una-data;Spesa;30,00\n"
                    "03/03/2026;Buona;10,00\n")

# Niente che somigli a un estratto conto: nemmeno la data.
IGNOTO = "Colore;Taglia;Quantita\nRosso;M;3\nBlu;L;1\n"


class ColonneCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        # Tutto lo schema: l'anteprima legge anche le regole di categorizzazione.
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def upload(self, content: str) -> UploadFile:
        return UploadFile(filename="estratto.csv", file=BytesIO(content.encode()))

    def anteprima(self, content: str, mappatura: dict[str, int], delimitatore: str = ";") -> dict:
        return asyncio.run(main.import_csv_statement(
            self.upload(content), self.session, mapping=json.dumps(mappatura), delimiter=delimitatore))

    def test_intestazioni_e_cinque_righe_tornano_come_nel_file(self) -> None:
        righe = "".join(f"0{i}/01/2026;Riga {i};{i},00\n" for i in range(1, 8))
        colonne = asyncio.run(main.import_csv_columns(self.upload("Data;Descrizione;Importo\n" + righe)))
        self.assertEqual(["Data", "Descrizione", "Importo"], colonne["headers"])
        self.assertEqual(5, len(colonne["sample"]))
        self.assertEqual({"Data": "01/01/2026", "Descrizione": "Riga 1", "Importo": "1,00"}, colonne["sample"][0])

    def test_la_proposta_e_quella_che_si_otterrebbe_oggi(self) -> None:
        """Chi non tocca niente importa le stesse righe di prima, con le stesse
        colonne: la proposta dell'euristica e' quello che l'import fa da solo."""
        colonne = CSVStatementParser.colonne(ESTRATTO)
        self.assertEqual({"date_cols": 0, "desc_cols": 1, "amount_cols": 2}, colonne["mapping"])
        self.assertEqual(CSVStatementParser.extract_transactions_from_csv(ESTRATTO),
                         CSVStatementParser.extract_transactions_from_csv(
                             ESTRATTO, mapping=colonne["mapping"], delimiter=colonne["delimiter"]))

    def test_una_mappatura_esplicita_vince_sull_euristica(self) -> None:
        # Con la sola euristica l'importo non si trova e non resta nessuna riga.
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main.import_csv_statement(self.upload(SENZA_IMPORTO), self.session))
        self.assertEqual("statementEmpty", caught.exception.detail)
        # Dicendo qual e' l'importo, le righe ci sono e hanno il verso giusto.
        # L'anteprima mostra sempre importi positivi: il verso lo dice il tipo
        # del movimento, come per le righe lette da un PDF.
        esito = self.anteprima(SENZA_IMPORTO, {"date_cols": 0, "desc_cols": 1, "amount_cols": 2})
        self.assertEqual(2, esito["count"])
        self.assertEqual([1500.0, 1234.0], [riga["amount"] for riga in esito["transactions"]])
        self.assertEqual(["Income", "Expenses"], [riga["transactionType"] for riga in esito["transactions"]])

    def test_due_colonne_separate_per_uscita_e_entrata(self) -> None:
        esito = self.anteprima(DARE_AVERE, {"date_cols": 0, "desc_cols": 1, "debit_cols": 2, "credit_cols": 3})
        self.assertEqual(2, esito["count"])
        self.assertEqual([2000.0, 30.0], [riga["amount"] for riga in esito["transactions"]])
        self.assertEqual(["Income", "Expenses"], [riga["transactionType"] for riga in esito["transactions"]])

    def test_una_riga_illeggibile_non_ferma_le_altre(self) -> None:
        esito = self.anteprima(DATA_ILLEGGIBILE, {"date_cols": 0, "desc_cols": 1, "amount_cols": 2})
        self.assertEqual(2, esito["count"])
        self.assertEqual("statementDateInvalid", esito["transactions"][0]["errorCode"])
        self.assertIsNone(esito["transactions"][1].get("errorCode"))
        self.assertEqual("2026-03-03", esito["transactions"][1]["date"])

    def test_senza_intestazioni_riconoscibili_non_esplode(self) -> None:
        colonne = CSVStatementParser.colonne(IGNOTO)
        self.assertEqual(["Colore", "Taglia", "Quantita"], colonne["headers"])
        self.assertEqual({}, colonne["mapping"])
        self.assertEqual(2, len(colonne["sample"]))
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main.import_csv_statement(self.upload(IGNOTO), self.session))
        self.assertEqual("statementEmpty", caught.exception.detail)

    def test_una_mappatura_storta_non_si_accetta(self) -> None:
        """Gli indici arrivano dall'esterno: una chiave inventata o un indice che
        non e' un indice valgono come rifiuto, non come colonna a caso."""
        for storta in ['{"colonna_inventata": 0}', '{"date_cols": -1}', '{"date_cols": "0"}', "non json", "[0]"]:
            with self.subTest(mappatura=storta), self.assertRaises(HTTPException) as caught:
                asyncio.run(main.import_csv_statement(self.upload(ESTRATTO), self.session, mapping=storta))
            self.assertEqual("statementMappingInvalid", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
