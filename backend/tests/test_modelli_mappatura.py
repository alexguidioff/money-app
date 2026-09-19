"""I modelli di mappatura: si scelgono una volta, si salvano con un nome.

Lo stesso estratto conto torna ogni mese con le stesse colonne, e senza un
modello si ricorreggerebbe da capo ogni volta. Il modello non e' una strada
parallela per leggere un file: passa dalle stesse regole dell'import, e la
prova che conta e' che riapplicarlo dia la stessa anteprima di una mappatura
scritta a mano li' per li'.

Database temporanei: non si tocca niente di reale.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from decimal import Decimal
from io import BytesIO

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import TABELLE_PERSONALI
from app.database import Base
from app.main import ImportTemplatePayload, create_import_template, import_csv_statement, list_import_templates
from app.migrations import PER_UTENTE
from app.models import Account

# Un estratto conto scritto qui, con numeri tondi e inventati.
CSV = ("Data;Descrizione;Importo\n"
       "01/02/2026;Stipendio;1.000,00\n"
       "02/02/2026;Spesa;-25,00\n").encode()


class ModelliMappaturaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(name="Banca", source_group="bank", starting_balance=Decimal("1000")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def anteprima(self, mappatura: dict[str, int], delimitatore: str) -> dict:
        return asyncio.run(import_csv_statement(
            UploadFile(filename="estratto.csv", file=BytesIO(CSV)), session=self.session,
            mapping=json.dumps(mappatura), delimiter=delimitatore))

    def salva(self, nome: str, mappatura: dict[str, int], delimitatore: str = ";") -> dict:
        return create_import_template(ImportTemplatePayload(name=nome, mapping=json.dumps(mappatura),
                                                            delimiter=delimitatore), self.session)

    def test_un_modello_riapplicato_da_la_stessa_anteprima_di_una_mappatura_scritta_a_mano(self) -> None:
        mappatura = {"date_cols": 0, "desc_cols": 1, "amount_cols": 2}
        a_mano = self.anteprima(mappatura, ";")

        self.salva("Estratto della banca", mappatura)
        modello = list_import_templates(self.session)["items"][0]

        self.assertEqual(a_mano, self.anteprima(modello["mapping"], modello["delimiter"]))
        # Le righe lette sono due, e con la descrizione giusta: senza, due
        # anteprime vuote sarebbero uguali e il confronto non direbbe niente.
        self.assertEqual(["Stipendio", "Spesa"], [riga["description"] for riga in a_mano["transactions"]])

    def test_il_taglio_viaggia_con_la_mappatura(self) -> None:
        # Gli indici valgono solo nella divisione da cui sono stati contati: un
        # modello salvato senza il suo taglio riapplicherebbe indici sbagliati
        # leggendo le colonne sbagliate, e non lo direbbe.
        self.salva("Estratto della banca", {"date_cols": 0, "amount_cols": 1}, delimitatore=",")
        modello = list_import_templates(self.session)["items"][0]

        self.assertEqual(",", modello["delimiter"])

    def test_un_nome_gia_usato_non_si_salva(self) -> None:
        self.salva("Estratto della banca", {"date_cols": 0})

        with self.assertRaises(HTTPException) as errore:
            self.salva("estratto DELLA banca", {"date_cols": 1})

        self.assertEqual(409, errore.exception.status_code)
        self.assertEqual("statementTemplateNameTaken", errore.exception.detail)

    def test_un_nome_vuoto_non_si_salva(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            self.salva("   ", {"date_cols": 0})

        self.assertEqual(422, errore.exception.status_code)
        self.assertEqual("statementTemplateNameRequired", errore.exception.detail)

    def test_una_mappatura_vuota_o_inventata_non_diventa_un_modello(self) -> None:
        # Un modello vuoto non e' un modello: sarebbe una voce nell'elenco che
        # riapplicata non cambia niente. E una chiave inventata deve fermarsi
        # qui, come si ferma quando arriva a mano all'import.
        for mappatura in ({}, {"colonna_inventata": 0}, {"date_cols": -1}):
            with self.subTest(mappatura=mappatura), self.assertRaises(HTTPException) as errore:
                self.salva("Estratto della banca", mappatura)
            self.assertEqual("statementMappingInvalid", errore.exception.detail)

        self.assertEqual([], list_import_templates(self.session)["items"])

    def test_un_modello_con_un_indice_fuori_dal_file_non_fa_esplodere_l_anteprima(self) -> None:
        # Il file puo' essere cambiato: una colonna in meno, e l'indice salvato
        # non esiste piu'. Le righe arrivano segnate, come sempre, invece di un
        # errore che non dice cosa sistemare.
        self.salva("Estratto della banca", {"date_cols": 0, "desc_cols": 1, "amount_cols": 9})
        modello = list_import_templates(self.session)["items"][0]

        anteprima = self.anteprima(modello["mapping"], modello["delimiter"])

        self.assertEqual(2, anteprima["count"])
        self.assertTrue(all(riga.get("errorCode") for riga in anteprima["transactions"]))

    def test_i_modelli_stanno_negli_elenchi_delle_tabelle_personali(self) -> None:
        """Senza le due voci, il modello di una persona comparirebbe a un'altra
        e la cancellazione di un account lo lascerebbe orfano.
        """
        self.assertIn("import_templates", PER_UTENTE)
        self.assertIn("import_templates", TABELLE_PERSONALI)


if __name__ == "__main__":
    unittest.main()
