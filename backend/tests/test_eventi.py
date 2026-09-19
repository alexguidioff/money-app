"""Gli eventi: quanto e' costato il viaggio, tutto compreso.

Un evento non e' una categoria: la taglia. Un viaggio non sta solo in
"Viaggi", ci sono i ristoranti, i trasporti e la benzina di quei giorni.
L'appartenenza e' una riga in ``transaction_events``, una sola per movimento, e
le date dell'evento servono a proporre i movimenti del periodo, non a decidere
chi ne fa parte: l'acconto pagato tre mesi prima della partenza e' parte del
viaggio lo stesso.

Qui c'e' quello che sta sotto alle rotte: le due tabelle, il nome unico per
utente, l'isolamento fra persone e il giro export/reimportazione, che deve
riportare gli agganci sugli id nuovi. I conteggi si provano dalle rotte.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, reset_current_user, set_current_user
from app.interchange import build_export
from app.interchange_import import import_data
from app.models import Event, Transaction, TransactionEvent


def _evento(nome: str = "Viaggio a Lisbona", **kwargs) -> Event:
    return Event(name=nome, **kwargs)


def _movimento(quando: date = date(2026, 4, 3), tipo: str = "Expenses", importo: str = "80.00",
               categoria: str = "Ristoranti", identificativo: int | None = None) -> Transaction:
    """Un movimento minimo, con la categoria nel campo che esiste.

    Il campo della categoria sta cambiando sotto a questo lavoro (PIANO-B3):
    finche' c'e' la colonna col nome va riempita, perche' li' e' obbligatoria -
    e un nome vuoto non passa il giro, dato che l'export scrive vuoto e la
    rilettura lo prende per un campo mancante. Quando sparira', queste due
    righe non fanno piu' niente e si tolgono.
    """
    movimento = Transaction(occurred_on=quando, effective_on=quando, transaction_type=tipo,
                            amount=Decimal(importo))
    if "category" in Transaction.__table__.columns:
        movimento.category = categoria
    movimento.id = identificativo
    return movimento


class EventiTests(unittest.TestCase):
    """Le due tabelle e le loro regole, senza passare dalle rotte."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.utente = set_current_user(1)

    def tearDown(self):
        reset_current_user(self.utente)
        self.session.close()
        self.engine.dispose()

    def test_un_evento_nasce_vuoto(self):
        evento = _evento()
        self.session.add(evento)
        self.session.commit()

        self.assertEqual(0, self.session.scalar(select(func.count()).select_from(TransactionEvent)))
        self.assertEqual([], self.session.scalars(
            select(TransactionEvent).where(TransactionEvent.event_id == evento.id)).all())
        # Nessuna data e nessuna chiusura decise al posto di chi lo crea: un
        # evento appena nato e' aperto e senza periodo.
        self.assertIsNone(evento.start_date)
        self.assertIsNone(evento.end_date)
        self.assertFalse(evento.closed)

    def test_due_eventi_con_lo_stesso_nome_per_lo_stesso_utente_sono_rifiutati(self):
        self.session.add_all([_evento("Trasloco"), _evento("Trasloco")])
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

        self.session.add(_evento("Trasloco"))
        self.session.commit()
        # Lo stesso nome resta libero per un'altra persona: il vincolo e' per
        # utente, e due persone possono aver traslocato tutte e due.
        set_current_user(2)
        self.session.add(_evento("Trasloco"))
        self.session.commit()
        self.assertEqual([1, 2], sorted(self.session.scalars(select(Event.user_id)).all()))


class RoundtripEventiTests(unittest.TestCase):
    """Un viaggio con i suoi movimenti deve sopravvivere a export e ritorno."""

    def setUp(self):
        self.engines = [create_engine("sqlite://"), create_engine("sqlite://")]
        for engine in self.engines:
            Base.metadata.create_all(engine)
        self.partenza, self.arrivo = (Session(engine) for engine in self.engines)
        self.utente = set_current_user(1)

    def tearDown(self):
        reset_current_user(self.utente)
        self.partenza.close()
        self.arrivo.close()
        for engine in self.engines:
            engine.dispose()

    def test_un_evento_con_due_movimenti_torna_con_gli_agganci_rimappati(self):
        viaggio = _evento(start_date=date(2026, 4, 1), end_date=date(2026, 4, 8), notes="Lisbona")
        self.partenza.add(viaggio)
        self.partenza.flush()
        # Id scelti a mano e diversi da quelli che prendera' chi importa: su
        # sqlite due database appena creati numerano tutti e due da 1, e senza
        # questa differenza un aggancio non rimappato passerebbe il controllo
        # per pura coincidenza.
        pranzo = _movimento(date(2026, 4, 2), importo="80.00", identificativo=11)
        treno = _movimento(date(2026, 4, 8), importo="20.00", identificativo=12)
        self.partenza.add_all([pranzo, treno])
        self.partenza.flush()
        self.partenza.add_all([TransactionEvent(transaction_id=pranzo.id, event_id=viaggio.id),
                               TransactionEvent(transaction_id=treno.id, event_id=viaggio.id)])
        self.partenza.commit()

        import_data(self.arrivo, build_export(self.partenza))

        eventi = self.arrivo.scalars(select(Event)).all()
        self.assertEqual(["Viaggio a Lisbona"], [e.name for e in eventi])
        self.assertEqual(date(2026, 4, 1), eventi[0].start_date)
        self.assertEqual(date(2026, 4, 8), eventi[0].end_date)
        movimenti = {m.id: m for m in self.arrivo.scalars(select(Transaction)).all()}
        agganci = self.arrivo.scalars(select(TransactionEvent)).all()
        self.assertEqual(2, len(agganci))
        # Gli id di arrivo sono nuovi: gli agganci devono puntare a quelli, o
        # l'evento arriva vuoto in un database che i movimenti li ha.
        self.assertEqual({eventi[0].id}, {a.event_id for a in agganci})
        self.assertEqual(set(movimenti), {a.transaction_id for a in agganci})
        self.assertEqual(set(), {a.transaction_id for a in agganci} & {11, 12})


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoEventiTests(unittest.TestCase):
    """Gli eventi di una persona non devono comparire a un'altra.

    Stesso schema usa-e-getta di ``test_interchange_postgres``: nessuna tabella
    pubblica viene letta o cambiata.
    """

    def test_gli_eventi_dell_utente_2_non_compaiono_all_utente_1(self):
        schema = "money_events_test_" + uuid4().hex
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
            token = set_current_user(1)
            try:
                with Session(limited) as session:
                    session.add(_evento("Trasloco"))
                    session.commit()
            finally:
                reset_current_user(token)
            token = set_current_user(2)
            try:
                with Session(limited) as session:
                    self.assertEqual([], list(session.scalars(select(Event))))
                    self.assertEqual(0, session.scalar(select(func.count()).select_from(TransactionEvent)))
                    # E il nome di un altro resta libero anche qui: il vincolo
                    # e' per utente, non per tutta la tabella.
                    session.add(_evento("Trasloco"))
                    session.commit()
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
