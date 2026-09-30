"""Optional real-RLS regression, restricted to a disposable named schema.

Run with MONEY_TEST_POSTGRES=1 in the API test container. No public tables
are read or changed; all test tables are created in money_import_test_<uuid>.
"""
import os
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, set_current_user, reset_current_user
from app.interchange import build_export
from app.interchange_import import import_data
from app.models import (BudgetPlan, Category, User, Transaction, TransactionLedgerLink,
                        InvestmentTransaction, InvestmentTransactionDetail)
from tests.categorie_fixture import categoria
from tests.test_interchange_roundtrip import _populate


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class PostgresInterchangeTests(unittest.TestCase):
    def test_import_into_another_user_preserves_source_and_remaps_links(self):
        schema = "money_import_test_" + uuid4().hex
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
                    _populate(session)
                    tx = session.scalar(select(Transaction).where(Transaction.is_recurring_template.is_(False)))
                    ledger = session.scalar(select(InvestmentTransaction))
                    session.add(TransactionLedgerLink(transaction_id=tx.id, ledger_id=ledger.id))
                    session.commit()
                    source_ids = list(session.scalars(select(Transaction.id).order_by(Transaction.id)))
                    blob = build_export(session)
            finally:
                reset_current_user(token)
            token = set_current_user(202)
            try:
                with Session(limited) as session:
                    self.assertEqual(list(session.scalars(select(Transaction))), [])
                    import_data(session, blob)
                    txs = session.scalars(select(Transaction).order_by(Transaction.id)).all()
                    self.assertEqual(len(txs), 2)
                    self.assertTrue(all(t.user_id == 202 and t.id not in source_ids for t in txs))
                    self.assertEqual(txs[1].recurrence_parent_id, txs[0].id)
                    link = session.scalar(select(TransactionLedgerLink))
                    detail = session.scalar(select(InvestmentTransactionDetail))
                    self.assertEqual(link.transaction_id, txs[1].id)
                    self.assertEqual(link.ledger_id, detail.transaction_id)
                    # Sequence allocation still works after importing.
                    # PIANO-B3: la categoria e' una riga; qui serve solo che il
                    # movimento ne abbia una, per provare la sequenza dopo
                    # l'import.
                    session.add(Transaction(occurred_on=txs[1].occurred_on, effective_on=txs[1].effective_on,
                                            transaction_type="Income",
                                            category_id=categoria(session, "Test"), amount=1))
                    session.commit()
            finally:
                reset_current_user(token)
            token = set_current_user(101)
            try:
                with Session(limited) as session:
                    self.assertEqual(list(session.scalars(select(Transaction.id).order_by(Transaction.id))), source_ids)
                    self.assertEqual([float(t.amount) for t in session.scalars(select(Transaction).order_by(Transaction.id))], [42.5, 2000])
            finally:
                reset_current_user(token)
        finally:
            limited.dispose()
            Base.metadata.drop_all(owner)
            owner.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}"'))
            admin.dispose()


@unittest.skipUnless(os.getenv("MONEY_TEST_POSTGRES") == "1", "requires isolated PostgreSQL schema")
class PostgresBudgetCurrencyTests(unittest.TestCase):
    """Il vincolo che porta la valuta: due piani nella stessa categoria.

    Su SQLite non si prova - i vincoli di una tabella che esiste non si
    alterano - e la cosa da escludere non e' un'eccezione di troppo ma quella di
    meno: senza la valuta nel vincolo, il piano in franchi di una categoria che
    ha gia' quello in euro morirebbe con un 409, e dall'interfaccia non si
    ripara.
    """

    def setUp(self):
        self.schema = "money_import_test_" + uuid4().hex
        self.engine = create_engine(os.environ["DATABASE_ADMIN_URL"],
                                    connect_args={"options": "-csearch_path=" + self.schema})
        with self.engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def test_due_valute_convivono_e_la_terza_volta_no(self):
        with Session(self.engine) as session:
            session.add(Category(user_id=1, name="Casa", position=0, active=True))
            session.commit()
            categoria = session.scalar(select(Category.id))
            for valuta in ("EUR", "CHF"):
                session.add(BudgetPlan(user_id=1, period=date(2026, 1, 1), budget_type="Expenses",
                                       category_id=categoria, amount=Decimal("100"), currency=valuta))
            session.commit()
            self.assertEqual(sorted(session.scalars(select(BudgetPlan.currency)).all()), ["CHF", "EUR"])
            session.add(BudgetPlan(user_id=1, period=date(2026, 1, 1), budget_type="Expenses",
                                   category_id=categoria, amount=Decimal("1"), currency="EUR"))
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_il_vincolo_si_chiama_come_lo_cerca_la_migrazione(self):
        # Il nome scritto a mano: se cambiasse, la migrazione aggiungerebbe un
        # vincolo nuovo a ogni avvio su un database che ne ha gia' uno uguale.
        with self.engine.begin() as conn:
            nome = conn.scalar(text("SELECT conname FROM pg_constraint WHERE conrelid = 'budget_plans'::regclass"
                                    " AND contype = 'u'"))
        self.assertEqual(nome, "budget_plans_period_type_category_currency_key")
