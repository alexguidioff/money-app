"""Lo storico degli import: chi ha importato cosa, e com'e' andata.

Prima lo scriveva solo il ripristino di un backup, e nessuno lo leggeva. Ora
anche l'import di un estratto conto lascia una riga, con quante righe sono
entrate, quante sono state scartate e perche': e' quello che si guarda dopo un
import andato storto, quando la domanda e' "cosa e' rimasto fuori e per quale
motivo".

Database temporanei: non si tocca niente di reale.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app import main
from app.auth import TABELLE_PERSONALI
from app.database import Base, reset_current_user, set_current_user
from app.interchange import build_export
from app.interchange_import import import_data
from app.migrations import PER_UTENTE, tracked_changes
from app.models import Account, ImportBatch, User


def _riga(**campi) -> dict:
    """Una riga d'estratto conto come la manda l'anteprima del frontend."""
    values = {"date": "2026-09-10", "amount": 100.0, "transactionType": "Expenses",
              "accountName": "Banca", "category": "Casa", "description": "Affitto"}
    values.update(campi)
    return values


class StoricoImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(name="Banca", source_group="bank", starting_balance=Decimal("1000")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def salva(self, righe: list[dict], source: str = "estratto.csv") -> dict:
        return asyncio.run(main.save_pdf_transactions(righe, source=source, session=self.session))

    def storico(self) -> list[ImportBatch]:
        return list(self.session.scalars(select(ImportBatch).order_by(ImportBatch.id)))

    def test_un_import_di_estratto_conto_lascia_una_riga_nello_storico(self) -> None:
        esito = self.salva([_riga()])
        self.assertEqual((1, []), (esito["saved"], esito["errors"]))

        riga = self.storico()[-1]
        self.assertEqual("statement", riga.kind)
        self.assertEqual("estratto.csv", riga.source_name)
        self.assertIsNotNone(riga.imported_at)
        # Il registro e' dello stesso giorno dell'import, non del movimento.
        self.assertEqual(date.today(), riga.imported_at.date())

    def test_accettate_e_scartate_sono_quelle_che_la_rotta_ha_davvero_fatto(self) -> None:
        # Il conto esiste, il tipo no: la prima passa, la seconda no.
        esito = self.salva([_riga(), _riga(transactionType="Regalo", accountName="Banca"),
                            _riga(accountName="Banca")])
        self.assertEqual(2, esito["saved"])
        self.assertEqual(1, len(esito["errors"]))

        riga = self.storico()[-1]
        self.assertEqual(2, riga.rows_accepted)
        self.assertEqual(1, riga.rows_rejected)

    def test_i_motivi_di_scarto_arrivano_col_loro_conteggio(self) -> None:
        # Dodici righe scartate non dicono cosa sistemare; dodici righe senza
        # conto si'. Per questo i motivi si contano per codice.
        esito = self.salva([
            _riga(),
            _riga(accountName="Conto che non esiste"),
            _riga(accountName="Conto che non esiste"),
            _riga(transactionType="Regalo"),
        ])
        self.assertEqual(1, esito["saved"])

        riga = self.storico()[-1]
        self.assertEqual('{"statementAccountRequired": 2, "statementInvalidType": 1}', riga.rejected_reasons)

    def test_un_import_che_non_salva_niente_lascia_comunque_la_riga(self) -> None:
        # Zero accettate e' un'informazione - e' l'import andato storto - non un
        # non-evento: se la riga non ci fosse, l'elenco direbbe che non e'
        # successo niente.
        esito = self.salva([_riga(accountName="Conto che non esiste")], source="estratto-di-prova.csv")
        self.assertEqual((0, 1), (esito["saved"], len(esito["errors"])))

        riga = self.storico()[-1]
        self.assertEqual(("statement", "estratto-di-prova.csv", 0, 1),
                         (riga.kind, riga.source_name, riga.rows_accepted, riga.rows_rejected))

    def test_il_ripristino_di_un_backup_resta_un_ripristino(self) -> None:
        # Le due cose non si leggono allo stesso modo: qui contano le righe
        # tornate dentro, non accettate e scartate.
        origine = Session(create_engine("sqlite://"))
        Base.metadata.create_all(origine.get_bind())
        origine.add(Account(name="Banca", source_group="bank", starting_balance=Decimal("1000")))
        origine.commit()

        import_data(self.session, build_export(origine), source_name="backup.xlsx")
        riga = self.storico()[-1]
        self.assertEqual(("interchange", "backup.xlsx"), (riga.kind, riga.source_name))
        self.assertEqual(0, riga.rows_rejected)
        self.assertIsNone(riga.rejected_reasons)
        self.assertGreater(riga.rows_accepted, 0)

        origine.close()


class MigrazioneStoricoTests(unittest.TestCase):
    def test_le_righe_gia_esistenti_diventano_ripristini(self) -> None:
        """Le righe che c'erano sono tutte ripristini di un backup.

        `kind` nasce 'interchange', che e' quello che sono davvero: dedurlo
        riga per riga non si puo', e un valore nuovo da indovinare sarebbe una
        seconda verita' accanto a quella che si sa gia'.
        """
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE transactions (id INTEGER, amount NUMERIC)"))
            conn.execute(text("CREATE TABLE accounts (name TEXT, status TEXT, counts_in_net_worth BOOLEAN)"))
            conn.execute(text("CREATE TABLE import_batches (id INTEGER PRIMARY KEY, source_name VARCHAR(255))"))
            conn.execute(text("INSERT INTO import_batches (id, source_name) VALUES (7, 'backup.xlsx')"))
        tracked_changes(engine)
        # Ripetibile: al secondo giro non trova piu' niente da aggiungere.
        tracked_changes(engine)
        with engine.connect() as conn:
            riga = conn.execute(text("SELECT source_name, kind, rows_accepted, rows_rejected, "
                                     "rejected_reasons FROM import_batches")).one()
        self.assertEqual(("backup.xlsx", "interchange", 0, 0, None), tuple(riga))
        engine.dispose()


class ElencoTabelleTests(unittest.TestCase):
    def test_lo_storico_degli_import_e_di_chi_importa(self) -> None:
        """Senza la riga in `PER_UTENTE` il ripristino di una persona comparirebbe
        nell'elenco di un'altra. Qui si presidia l'elenco, perche' e' quello che
        ``accendi_isolamento`` legge: il comportamento vero lo prova il test con
        Postgres qui sotto.
        """
        self.assertIn("import_batches", PER_UTENTE)
        self.assertIn("import_batches", TABELLE_PERSONALI)


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoStoricoTests(unittest.TestCase):
    """Lo storico di una persona non deve comparire a un'altra.

    Stesso schema usa-e-getta di ``test_interchange_postgres``: nessuna tabella
    pubblica viene letta o cambiata.
    """

    def test_lo_storico_dell_utente_2_non_compara_all_utente_1(self) -> None:
        schema = "money_import_batches_test_" + uuid4().hex
        admin = create_engine(os.environ["DATABASE_ADMIN_URL"])
        limited = create_engine(os.environ["DATABASE_URL"], connect_args={"options": "-csearch_path=" + schema})
        owner = create_engine(os.environ["DATABASE_ADMIN_URL"], connect_args={"options": "-csearch_path=" + schema})
        role = admin.dialect.identifier_preparer.quote(limited.url.username)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            Base.metadata.create_all(owner)
            with owner.begin() as conn:
                conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
                conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {role}'))
                conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO {role}'))
                for table in Base.metadata.tables.values():
                    if "user_id" not in table.c or table.name == "user_sessions":
                        continue
                    conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
                    conn.execute(text(f"""CREATE POLICY isolate ON "{table.name}"
                        USING (user_id = nullif(current_setting('app.user_id', true), '')::integer)
                        WITH CHECK (user_id = nullif(current_setting('app.user_id', true), '')::integer)"""))
            with Session(owner) as session:
                session.add_all([User(id=101, username="source", display_name="Source"),
                                 User(id=202, username="target", display_name="Target")])
                session.commit()

            token = set_current_user(101)
            try:
                with Session(limited) as session:
                    session.add(ImportBatch(kind="statement", source_name="estratto.csv",
                                            rows_accepted=3, rows_rejected=1))
                    session.commit()
                    self.assertEqual(1, session.scalar(select(func.count()).select_from(ImportBatch)))
            finally:
                reset_current_user(token)

            token = set_current_user(202)
            try:
                with Session(limited) as session:
                    self.assertEqual([], list(session.scalars(select(ImportBatch))))
            finally:
                reset_current_user(token)
        finally:
            limited.dispose()
            Base.metadata.drop_all(owner)
            owner.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}"'))
            admin.dispose()


if __name__ == "__main__":
    unittest.main()
