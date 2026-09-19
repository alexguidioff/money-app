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
from datetime import datetime, timezone
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
from app.models import Account, ImportBatch, Transaction, User


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
        # La data si confronta in UTC, che e' l'ora con cui la riga nasce
        # (`func.now()`): `date.today()` e' l'ora locale, e fra mezzanotte e le
        # due in Italia e' il giorno prima - il test era rosso a quell'ora,
        # tutti i giorni, senza che niente fosse rotto.
        self.assertEqual(datetime.now(timezone.utc).date(), riga.imported_at.date())

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


class DoppioniAlSalvataggioTests(unittest.TestCase):
    """Il salvataggio ricontrolla i doppioni che l'anteprima aveva segnato.

    Fra le due schermate l'archivio puo' essere cambiato (un'altra sessione, un
    altro import): senza il ricontrollo una riga che li' era pulita entra due
    volte, e l'unica rete era la casella che l'utente non aveva motivo di
    guardare.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(name="Banca", source_group="bank", starting_balance=Decimal("1000")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def salva(self, righe: list[dict]) -> dict:
        return asyncio.run(main.save_pdf_transactions(righe, source="estratto.csv", session=self.session))

    def storico(self) -> list[ImportBatch]:
        return list(self.session.scalars(select(ImportBatch).order_by(ImportBatch.id)))

    def test_una_riga_che_l_archivio_ha_gia_non_entra_di_nuovo(self) -> None:
        self.assertEqual(1, self.salva([_riga()])["saved"])

        esito = self.salva([_riga()])

        self.assertEqual(0, esito["saved"])
        # Il movimento che le somiglia viaggia col rifiuto: e' quello che
        # l'anteprima mostra sotto la riga, e senza non si saprebbe con cosa si
        # sta parlando.
        self.assertEqual([{"index": 0, "code": "statementDuplicateRow",
                           "duplicateOf": {"id": 1, "date": "2026-09-10", "amount": 100.0, "description": "Affitto"}}],
                         esito["errors"])
        riga = self.storico()[-1]
        self.assertEqual((0, 1), (riga.rows_accepted, riga.rows_rejected))
        # Anche questo motivo si conta: "una riga saltata" da sola non direbbe
        # che e' stata saltata perche' c'era gia'.
        self.assertEqual('{"statementDuplicateRow": 1}', riga.rejected_reasons)

    def test_una_riga_segnata_duplicata_e_tenuta_si_salva(self) -> None:
        # La casella e' il consenso: chi importa l'ha vista piena, l'ha tenuta,
        # e il movimento entra una seconda volta perche' l'ha deciso lui.
        self.assertEqual(1, self.salva([_riga()])["saved"])

        esito = self.salva([{**_riga(), "duplicate": True}])

        self.assertEqual((1, []), (esito["saved"], esito["errors"]))

    def test_una_riga_rifiutata_si_puo_far_entrare(self) -> None:
        # Il giro completo di chi importa: la riga viene rifiutata, torna
        # all'anteprima col movimento che le somiglia, lui la spunta - cioe' la
        # tiene - e la riga entra. Se il rifiuto non lasciasse questa strada,
        # riconfermare la stessa lista la farebbe rifiutare di nuovo, per
        # sempre, ed e' una riga che si e' deciso di volere.
        self.assertEqual(1, self.salva([_riga()])["saved"])

        rifiuto = self.salva([_riga()])
        rimandata = {**_riga(), "duplicate": True, "duplicateOf": rifiuto["errors"][0]["duplicateOf"]}

        self.assertEqual((1, []), (self.salva([rimandata])["saved"], []))

    def test_due_righe_uguali_nello_stesso_file_entrano_entrambe(self) -> None:
        # Due caffe' da 1,50 nello stesso giorno sono due movimenti, non un
        # doppione: l'archivio si legge una volta sola, prima di scrivere,
        # quindi la seconda riga non trova la prima che si e' appena salvata.
        esito = self.salva([_riga(), _riga()])

        self.assertEqual((2, []), (esito["saved"], esito["errors"]))


class LegameColLottoTests(unittest.TestCase):
    """Ogni riga importata dice da quale import viene.

    Lo storico senza questo legame sa quanti movimenti ha portato un file, non
    quali: "guarda questo import" non e' una cosa che si puo' fare, e nemmeno
    "annullalo" il giorno in cui servira'.
    """

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

    def movimenti(self) -> list[Transaction]:
        return list(self.session.scalars(select(Transaction).order_by(Transaction.id)))

    def test_ogni_riga_salvata_rimanda_al_suo_import(self) -> None:
        self.salva([_riga()])
        primo = self.session.scalar(select(ImportBatch).order_by(ImportBatch.id.desc()))

        self.salva([_riga(description="Spesa", amount=50.0)])
        secondo = self.session.scalar(select(ImportBatch).order_by(ImportBatch.id.desc()))

        self.assertNotEqual(primo.id, secondo.id)
        self.assertEqual([primo.id, secondo.id], [riga.import_batch_id for riga in self.movimenti()])

    def test_un_import_scartato_non_lascia_righe_ma_lascia_il_lotto(self) -> None:
        esito = self.salva([_riga(accountName="Conto che non esiste")])

        self.assertEqual(0, esito["saved"])
        self.assertEqual([], self.movimenti())
        lotto = self.session.scalar(select(ImportBatch).order_by(ImportBatch.id.desc()))
        self.assertEqual((0, 1), (lotto.rows_accepted, lotto.rows_rejected))

    def test_la_riga_saltata_perche_gia_presente_non_porta_il_lotto(self) -> None:
        # Vale per il legame: il movimento che c'e' gia' non diventa figlio di
        # questo import, altrimenti lo storico direbbe che il file ha portato
        # una cosa che non ha portato.
        self.salva([_riga()])
        prima = self.movimenti()[0]

        self.salva([_riga()])

        self.assertEqual([prima.import_batch_id], [riga.import_batch_id for riga in self.movimenti()])


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

    def test_i_movimenti_che_c_erano_restano_senza_lotto(self) -> None:
        """Nessuno li ha importati da un file, e non si indovina.

        Vuoto e' la risposta giusta: un lotto indovinato direbbe che un import
        ha portato dei movimenti, e sarebbe falso.
        """
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE transactions (id INTEGER, amount NUMERIC)"))
            conn.execute(text("CREATE TABLE accounts (name TEXT, status TEXT, counts_in_net_worth BOOLEAN)"))
            conn.execute(text("INSERT INTO transactions (id, amount) VALUES (1, 42)"))
        tracked_changes(engine)
        # Ripetibile: al secondo giro la colonna c'e' gia'.
        tracked_changes(engine)
        with engine.connect() as conn:
            righe = [tuple(riga) for riga in conn.execute(
                text("SELECT id, amount, import_batch_id FROM transactions")).all()]
        self.assertEqual([(1, 42, None)], righe)
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
