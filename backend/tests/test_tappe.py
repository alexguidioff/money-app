"""Le tappe di un obiettivo: un obiettivo piu' piccolo dentro quello grande.

Una tappa non e' un obiettivo a se': e' un punto sulla strada dello stesso
obiettivo, con lo stesso importo come unita' di misura. Lo stato lo calcola la
stessa funzione dello stato dell'obiettivo - e' l'unico modo perche' i due
numeri non finiscano per non concordare.

Qui c'e' l'isolamento fra persone; i conteggi e lo stato si provano dalle rotte.
I numeri sono tondi e inventati: questo repository e' pubblico e i valori veri
di chi usa l'app non ci entrano.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.auth import TABELLE_PERSONALI
from app.database import Base, reset_current_user, set_current_user
from app.migrations import PER_UTENTE
from app.models import Goal, GoalMilestone


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
