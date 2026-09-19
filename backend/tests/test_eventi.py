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
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core_routes import (EventPayload, TransactionEventPayload, create_event, delete_event, event_detail,
                             events, set_transaction_event, transactions, update_event)
from app.database import Base, reset_current_user, set_current_user
from app.interchange import build_export
from app.interchange_import import import_data
from app.main import delete_transaction
from app.models import Event, Transaction, TransactionEvent
from app.transaction_rules import set_refund


def _chiavi_esterne_accese(connessione, _record) -> None:
    """Su sqlite le chiavi esterne nascono spente, e il cascade non si vedrebbe.

    In produzione il database e' PostgreSQL e le applica sempre: qui vanno
    accese a mano, o il test degli agganci che spariscono passerebbe anche se
    il database non li togliesse affatto.
    """
    connessione.execute("PRAGMA foreign_keys=ON")


def _motore_con_chiavi_esterne():
    motore = create_engine("sqlite://")
    event.listen(motore, "connect", _chiavi_esterne_accese)
    return motore


def _evento(nome: str = "Viaggio a Lisbona", **kwargs) -> Event:
    return Event(name=nome, **kwargs)


def _categoria(movimento: Transaction) -> str:
    """La categoria di un movimento, letta dal campo che esiste (PIANO-B3)."""
    return movimento.category if "category" in Transaction.__table__.columns else ""


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
        self.engine = _motore_con_chiavi_esterne()
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

        # E dalla rotta si vede lo stesso: zero movimenti, totali a zero.
        creato = create_event(EventPayload(name="Trasloco"), self.session)
        riga = next(voce for voce in events(self.session)["items"] if voce["id"] == creato["id"])
        self.assertEqual({"movimenti": 0, "spese": 0.0, "entrate": 0.0, "netto": 0.0},
                         {chiave: riga[chiave] for chiave in ("movimenti", "spese", "entrate", "netto")})

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
    """Un viaggio con i suoi movimenti deve sopravvivere a export e ritorno.

    Con le chiavi esterne accese l'import deve anche svuotare le tabelle
    nell'ordine giusto: gli agganci prima dei movimenti e degli eventi che
    nominano, o la cancellazione non passerebbe.
    """

    def setUp(self):
        self.engines = [_motore_con_chiavi_esterne(), _motore_con_chiavi_esterne()]
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


class ConteggiEventiTests(unittest.TestCase):
    """I numeri di un evento, visti dalle rotte e non dalle tabelle.

    I casi sono quelli del §8 di PIANO-B5-EVENTI: qui stanno i conteggi e le
    regole delle rotte, sopra le tabelle e il giro dell'export.
    """

    def setUp(self):
        self.engine = _motore_con_chiavi_esterne()
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.utente = set_current_user(1)

    def tearDown(self):
        reset_current_user(self.utente)
        self.session.close()
        self.engine.dispose()

    def _salva_movimento(self, **kwargs: Any) -> Transaction:
        movimento = _movimento(**kwargs)
        self.session.add(movimento)
        self.session.commit()
        return movimento

    def _evento_creato(self, nome: str = "Viaggio a Lisbona", **kwargs: Any) -> dict[str, Any]:
        return create_event(EventPayload(name=nome, **kwargs), self.session)

    def _aggancia(self, movimento: Transaction, evento: dict[str, Any]) -> None:
        set_transaction_event(movimento.id, TransactionEventPayload(event_id=evento["id"]), self.session)

    def _riga(self, evento: dict[str, Any]) -> dict[str, Any]:
        return next(voce for voce in events(self.session)["items"] if voce["id"] == evento["id"])

    def _agganci(self) -> int:
        return self.session.scalar(select(func.count()).select_from(TransactionEvent)) or 0

    def test_un_movimento_agganciato_fa_il_suo_importo(self):
        viaggio = self._evento_creato()
        pranzo = self._salva_movimento(importo="80.00")
        self._aggancia(pranzo, viaggio)

        riga = self._riga(viaggio)
        self.assertEqual(80.0, riga["spese"])
        self.assertEqual(0.0, riga["entrate"])
        self.assertEqual(-80.0, riga["netto"])
        self.assertEqual(1, riga["movimenti"])
        # E il movimento dice a quale evento appartiene: e' la pastiglia che si
        # vede nella lista.
        in_lista = next(voce for voce in transactions(100, self.session)["items"] if voce["id"] == str(pranzo.id))
        self.assertEqual({"id": viaggio["id"], "name": "Viaggio a Lisbona", "closed": False}, in_lista["event"])

    def test_spese_ed_entrate_restano_separate_e_il_netto_e_la_differenza(self):
        viaggio = self._evento_creato()
        self._aggancia(self._salva_movimento(importo="300.00"), viaggio)
        self._aggancia(self._salva_movimento(tipo="Income", importo="100.00"), viaggio)

        riga = self._riga(viaggio)
        self.assertEqual(300.0, riga["spese"])
        self.assertEqual(100.0, riga["entrate"])
        self.assertEqual(-200.0, riga["netto"])
        # Un rimborso non e' un introito e la spesa non sparisce: sommare tutto
        # in un numero solo nasconderebbe meta' della storia.
        self.assertEqual(2, riga["movimenti"])

    def test_un_movimento_sta_in_un_evento_solo(self):
        primo = self._evento_creato("Trasloco")
        secondo = self._evento_creato()
        spesa = self._salva_movimento(importo="80.00")
        self._aggancia(spesa, primo)
        self._aggancia(spesa, secondo)

        self.assertEqual(1, self._agganci())
        self.assertEqual(0, self._riga(primo)["movimenti"])
        self.assertEqual(80.0, self._riga(secondo)["spese"])

    def test_sganciare_lascia_il_movimento_dov_era(self):
        viaggio = self._evento_creato()
        spesa = self._salva_movimento(importo="80.00")
        prima = _categoria(spesa)
        self._aggancia(spesa, viaggio)
        set_transaction_event(spesa.id, TransactionEventPayload(event_id=None), self.session)

        self.assertEqual(0, self._agganci())
        self.assertEqual(0, self._riga(viaggio)["movimenti"])
        dopo = self.session.get(Transaction, spesa.id)
        self.assertIsNotNone(dopo, "sganciare non deve toccare il movimento")
        self.assertEqual(prima, _categoria(dopo))
        self.assertEqual([None], [voce["event"] for voce in transactions(100, self.session)["items"]])

    def test_cancellare_l_evento_lascia_i_movimenti(self):
        viaggio = self._evento_creato()
        spesa = self._salva_movimento(importo="80.00")
        self._aggancia(spesa, viaggio)
        delete_event(viaggio["id"], self.session)

        self.assertEqual(0, self._agganci())
        self.assertEqual(1, self.session.scalar(select(func.count()).select_from(Transaction)))
        self.assertEqual([], events(self.session)["items"])

    def test_cancellare_il_movimento_porta_via_l_aggancio(self):
        viaggio = self._evento_creato()
        spesa = self._salva_movimento(importo="80.00")
        self._aggancia(spesa, viaggio)
        delete_transaction(spesa.id, self.session)

        # Non c'e' codice che cancelli l'aggancio: lo fa la chiave esterna, ed e'
        # per questo che il test accende le chiavi esterne su sqlite.
        self.assertEqual(0, self._agganci())
        self.assertEqual({"movimenti": 0, "spese": 0.0},
                         {chiave: self._riga(viaggio)[chiave] for chiave in ("movimenti", "spese")})

    def test_un_movimento_fuori_dalle_date_si_aggancia_lo_stesso(self):
        viaggio = self._evento_creato(start_date=date(2026, 4, 1), end_date=date(2026, 4, 8))
        acconto = self._salva_movimento(quando=date(2026, 1, 15), importo="120.00")
        self._aggancia(acconto, viaggio)

        # Le date propongono i movimenti del periodo, non decidono chi ne fa
        # parte: l'acconto pagato a gennaio e' del viaggio di aprile.
        self.assertEqual(120.0, self._riga(viaggio)["spese"])
        self.assertEqual(1, transactions(100, self.session, event_id=viaggio["id"])["total"])

    def test_i_movimenti_che_non_contano_nel_budget_non_contano_neanche_qui(self):
        viaggio = self._evento_creato()
        spesa = self._salva_movimento(importo="200.00")
        rimborso = self._salva_movimento(tipo="Income", importo="50.00")
        set_refund(self.session, rimborso, spesa.id)
        fuori_budget = self._salva_movimento(importo="999.00")
        fuori_budget.counts_in_budget = False
        self.session.commit()
        self._aggancia(spesa, viaggio)
        self._aggancia(fuori_budget, viaggio)
        self._aggancia(rimborso, viaggio)

        riga = self._riga(viaggio)
        # Il rimborso non e' un introito: toglie 50 dalle spese, che erano 200.
        # Il movimento fuori budget non entra in nessuno dei due numeri, come non
        # entra nel budget.
        self.assertEqual(150.0, riga["spese"])
        self.assertEqual(0.0, riga["entrate"])
        self.assertEqual(-150.0, riga["netto"])
        # Il numero di movimenti invece li conta tutti e tre: dice quanto
        # materiale c'e' dentro, e l'elenco li mostra tutti.
        self.assertEqual(3, riga["movimenti"])

        dettaglio = event_detail(viaggio["id"], self.session)
        self.assertEqual(150.0, round(sum(voce["spese"] for voce in dettaglio["categories"]), 2))
        self.assertEqual(0.0, round(sum(voce["entrate"] for voce in dettaglio["categories"]), 2))
        self.assertEqual(3, len(dettaglio["movements"]))

    def test_lo_stesso_nome_si_rifiuta_e_il_nome_vuoto_anche(self):
        self._evento_creato("Trasloco")
        with self.assertRaises(HTTPException) as errore:
            self._evento_creato("Trasloco")
        self.assertEqual("eventDuplicate", errore.exception.detail)
        with self.assertRaises(HTTPException) as vuoto:
            self._evento_creato("   ")
        self.assertEqual("eventNameRequired", vuoto.exception.detail)
        # Un evento che non c'e' non si rinomina ne' si cancella.
        for chiamata in (lambda: update_event(999, EventPayload(name="X"), self.session),
                         lambda: delete_event(999, self.session)):
            with self.assertRaises(HTTPException) as mancante:
                chiamata()
            self.assertEqual("eventNotFound", mancante.exception.detail)

    def test_gli_eventi_chiusi_scendono_in_fondo(self):
        aperto = self._evento_creato("Trasloco", start_date=date(2026, 5, 1))
        chiuso = self._evento_creato("Viaggio a Lisbona", start_date=date(2026, 4, 1))
        update_event(chiuso["id"], EventPayload(name="Viaggio a Lisbona", closed=True), self.session)

        # La card li mostra cosi', e la tendina del modulo prende solo gli
        # aperti: un evento finito non e' piu' qualcosa a cui stai lavorando.
        self.assertEqual([aperto["id"], chiuso["id"]], [voce["id"] for voce in events(self.session)["items"]])
        self.assertEqual([aperto["id"]], [voce["id"] for voce in transactions(100, self.session)["events"]
                                          if not voce["closed"]])


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
