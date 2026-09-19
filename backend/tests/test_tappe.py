"""Le tappe di un obiettivo: un obiettivo piu' piccolo dentro quello grande.

Una tappa non e' un obiettivo a se': e' un punto sulla strada dello stesso
obiettivo, con lo stesso importo come unita' di misura. Lo stato lo calcola la
stessa funzione dello stato dell'obiettivo - e' l'unico modo perche' i due
numeri non finiscano per non concordare.

Qui c'e' l'isolamento fra persone e le regole delle rotte; i numeri sono tondi
e inventati: questo repository e' pubblico e i valori veri di chi usa l'app non
ci entrano.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from app.auth import TABELLE_PERSONALI
from app.core_routes import (GoalMilestonePayload, _stato_goal, create_goal_milestone,
                             delete_goal_milestone, goals)
from app.database import Base, reset_current_user, set_current_user
from app.main import delete_goal
from app.migrations import PER_UTENTE
from app.models import Goal, GoalMilestone, Transaction


def _chiavi_esterne_accese(connessione, _record) -> None:
    """Su sqlite le chiavi esterne nascono spente, e il CASCADE non si vedrebbe.

    In produzione il database e' PostgreSQL e le applica sempre: qui vanno
    accese a mano, o il test della cancellazione passerebbe anche se il
    database non togliesse affatto le tappe.
    """
    connessione.execute("PRAGMA foreign_keys=ON")


class TappeRotteTests(unittest.TestCase):
    """Le regole di una tappa, provate dalle rotte vere.

    L'obiettivo e' a meta' strada: cento giorni fatti, cento che mancano, e
    meta' del denaro. Cosi' "in linea" e "in ritardo" sono due risposte
    possibili e il test le distingue, invece di verificare sempre lo stesso
    verdetto.
    """

    def setUp(self) -> None:
        motore = create_engine("sqlite://")
        event.listen(motore, "connect", _chiavi_esterne_accese)
        Base.metadata.create_all(motore)
        self.session = Session(motore)
        self.oggi = date.today()
        self.inizio = self.oggi - timedelta(days=100)
        self.fine = self.oggi + timedelta(days=100)
        self.obiettivo = Goal(name="Vacanza", kind="contributions",
                              starting_amount=Decimal("1000"), target_amount=Decimal("10000"),
                              start_date=self.inizio, target_date=self.fine)
        self.session.add(self.obiettivo)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _versa(self, importo: str) -> None:
        """Un versamento taggato col goal: e' cosi' che il valore corrente sale."""
        self.session.add(Transaction(
            occurred_on=self.oggi, effective_on=self.oggi, transaction_type="Transfers",
            category_id=None, amount=Decimal(importo), account_type="Bank",
            account_name="Conto", goal="Vacanza", is_recurring_template=False))
        self.session.commit()

    def _tappa(self, nome: str, importo: str, **kwargs) -> dict[str, Any]:
        return create_goal_milestone(self.obiettivo.id, GoalMilestonePayload(
            name=nome, target_amount=Decimal(importo), **kwargs), self.session)

    def _tappe(self) -> list[dict[str, Any]]:
        voce = next(v for v in goals(self.session)["items"] if v["name"] == "Vacanza")
        return voce["milestones"]

    def _rifiuto(self, nome: str, importo: str, **kwargs) -> str:
        with self.assertRaises(HTTPException) as errore:
            self._tappa(nome, importo, **kwargs)
        self.assertEqual(422, errore.exception.status_code)
        return str(errore.exception.detail)

    def test_una_tappa_raggiunta_e_completata_anche_senza_data(self) -> None:
        # Il traguardo e' passato: quello si vede, e non serve sapere quando
        # sarebbe stato raggiunto.
        self._versa("4000")
        tappa = self._tappa("Biglietti", "5000")
        self.assertIsNone(tappa["targetDate"])
        self.assertEqual("completed", tappa["status"])

    def test_lo_stato_della_tappa_lo_calcola_la_funzione_dell_obiettivo(self) -> None:
        # Stessi ingressi, stesso stato: e' l'unico modo perche' una tappa non
        # dica "in ritardo" dove l'obiettivo dice "in linea".
        self._versa("4000")
        for nome, importo in (("Vicina", "5000"), ("Media", "7000"), ("Lontana", "9999")):
            self._tappa(nome, importo, target_date=self.fine)
        lette = self._tappe()
        self.assertEqual(["completed", "on_track", "slightly_behind"], [t["status"] for t in lette])
        for tappa in lette:
            atteso = _stato_goal(5000.0, 1000.0, tappa["targetAmount"], self.inizio, self.fine,
                                 5000.0 >= tappa["targetAmount"], self.oggi)["status"]
            self.assertEqual(atteso, tappa["status"], tappa["name"])

    def test_una_tappa_oltre_l_obiettivo_non_e_una_tappa(self) -> None:
        self.assertEqual("milestoneAboveGoal", self._rifiuto("Mondiale", "10001"))
        self.assertEqual([], self._tappe())

    def test_una_tappa_dopo_la_scadenza_non_e_una_tappa(self) -> None:
        self.assertEqual("milestoneAfterGoal",
                         self._rifiuto("Dopo", "5000", target_date=self.fine + timedelta(days=1)))
        self.assertEqual([], self._tappe())

    def test_le_tappe_escono_in_ordine_di_importo(self) -> None:
        # L'ordine di inserimento racconta quando le hai scritte, non la strada:
        # chi aggiunge una tappa vicina dopo una lontana la vuole vedere al suo
        # posto, che e' dove sta.
        for nome, importo in (("Ultima", "9000"), ("Prima", "3000"), ("Meta", "6000")):
            self._tappa(nome, importo)
        self.assertEqual(["Prima", "Meta", "Ultima"], [t["name"] for t in self._tappe()])

    def test_cancellare_l_obiettivo_porta_via_le_tappe(self) -> None:
        self._tappa("Prima", "3000")
        self._tappa("Meta", "6000")
        delete_goal(self.obiettivo.id, self.session)
        self.assertEqual(0, self.session.scalar(select(func.count()).select_from(GoalMilestone)))

    def test_cancellare_una_tappa_lascia_l_obiettivo(self) -> None:
        prima = self._tappa("Prima", "3000")
        self._tappa("Meta", "6000")
        delete_goal_milestone(self.obiettivo.id, prima["id"], self.session)
        self.assertEqual(1, self.session.scalar(select(func.count()).select_from(GoalMilestone)))
        self.assertEqual(["Meta"], [t["name"] for t in self._tappe()])

    def test_la_tappa_di_un_altro_obiettivo_non_si_cancella_da_qui(self) -> None:
        # Il numero di una tappa non dice di chi sia: senza questo controllo,
        # l'id sbagliato nell'indirizzo cancellerebbe la tappa di un altro
        # obiettivo.
        altro = Goal(name="Auto", kind="contributions", starting_amount=Decimal("0"),
                     target_amount=Decimal("5000"))
        self.session.add(altro)
        self.session.commit()
        tappa = create_goal_milestone(altro.id, GoalMilestonePayload(
            name="Anticipo", target_amount=Decimal("1000")), self.session)
        with self.assertRaises(HTTPException) as errore:
            delete_goal_milestone(self.obiettivo.id, tappa["id"], self.session)
        self.assertEqual(404, errore.exception.status_code)
        self.assertEqual(1, self.session.scalar(select(func.count()).select_from(GoalMilestone)))


class TappePersonaliTests(unittest.TestCase):
    """Le tappe sono di chi ha scritto l'obiettivo, e si cancellano con lui."""

    def test_le_tappe_sono_nell_isolamento_fra_utenti(self) -> None:
        """Senza la riga in `PER_UTENTE` le tappe di uno comparirebbero all'altro.

        Due persone possono avere un obiettivo con lo stesso nome, e le tappe
        sotto quell'obiettivo si leggerebbero come proprie di chi guarda. Qui si
        presidia l'elenco, perche' e' quello che ``accendi_isolamento`` legge:
        il comportamento vero lo prova il test con Postgres qui sotto.
        """
        self.assertIn("goal_milestones", PER_UTENTE)
        self.assertIn("goal_milestones", TABELLE_PERSONALI)


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class IsolamentoTappeTests(unittest.TestCase):
    """Gli obiettivi e le tappe di una persona non devono comparire a un'altra.

    Stesso schema usa-e-getta di ``test_interchange_postgres``: nessuna tabella
    pubblica viene letta o cambiata.
    """

    def test_le_tappe_dell_utente_2_non_compaiono_all_utente_1(self):
        schema = "money_milestones_test_" + uuid4().hex
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
                    obiettivo = _obiettivo("Vacanza")
                    session.add(obiettivo)
                    session.flush()
                    session.add(GoalMilestone(goal_id=obiettivo.id, name="Biglietti", target_amount=Decimal("500")))
                    session.commit()
            finally:
                reset_current_user(token)
            token = set_current_user(2)
            try:
                with Session(limited) as session:
                    self.assertEqual([], list(session.scalars(select(Goal))))
                    self.assertEqual([], list(session.scalars(select(GoalMilestone))))
                    self.assertEqual(0, session.scalar(select(func.count()).select_from(GoalMilestone)))
                    # E il nome di un altro resta libero anche qui: l'obiettivo
                    # e la sua tappa si scrivono senza toccare l'utente 1.
                    obiettivo = _obiettivo("Vacanza")
                    session.add(obiettivo)
                    session.flush()
                    session.add(GoalMilestone(goal_id=obiettivo.id, name="Biglietti", target_amount=Decimal("500")))
                    session.commit()
                    self.assertEqual(1, session.scalar(select(func.count()).select_from(GoalMilestone)))
            finally:
                reset_current_user(token)
        finally:
            limited.dispose()
            Base.metadata.drop_all(owner)
            owner.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}"'))
            admin.dispose()


def _obiettivo(nome: str) -> Goal:
    return Goal(name=nome, kind="contributions", starting_amount=Decimal("0"),
                target_amount=Decimal("2000"), start_date=date(2026, 1, 1), target_date=date(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
