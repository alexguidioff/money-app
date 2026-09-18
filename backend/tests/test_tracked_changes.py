"""Approved movement rules, exercised only on temporary databases."""
import unittest
from datetime import date
from decimal import Decimal
from sqlalchemy import create_engine, select, event, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.database import Base
from app.models import Account, BudgetPlan, Transaction
from app import main, core_routes
from app.transaction_rules import missing_fields
from app.interchange import build_export
from app.interchange_import import import_data
from app.migrations import tracked_changes


class TrackedChangesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.s = Session(self.engine)
        self.s.add_all([Account(name="Bank", source_group="bank", starting_balance=100),
                       Account(name="Closed", source_group="bank", is_active=False, starting_balance=50)])
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()

    def tx(self, **changes):
        values = dict(occurred_on=date(2026, 9, 7), effective_on=date(2026, 9, 7),
                      transaction_type="Expenses", category="Food", account_name="Bank",
                      amount=Decimal("12.50"), details="Spesa Intermarché!")
        values.update(changes)
        row = Transaction(**values)
        self.s.add(row)
        self.s.commit()
        return row

    def test_duplicates_exact_decimal_normalized_and_one_query(self):
        tx = self.tx()
        tx_id = tx.id
        queries = []
        def count(*args): queries.append(args[2])
        event.listen(self.engine, "before_cursor_execute", count)
        rows = [{"occurredOn": day, "amount": amount, "description": "  SPESA   Intermarché "}
                for day, amount in [("2026-09-04", "12.50"), ("2026-09-10", "12.50"),
                                    ("2026-09-11", "12.50"), ("2026-09-07", "12.51")]]
        result = main.statement_preview(rows, self.s)["transactions"]
        event.remove(self.engine, "before_cursor_execute", count)
        # I duplicati vengono letti una volta sola, non per riga. Le regole di
        # categorizzazione sono la seconda lettura dell'anteprima, anche loro
        # una volta sola per import: il numero non deve crescere con le righe.
        self.assertEqual(len(queries), 2)
        self.assertEqual(sum('FROM transactions' in query for query in queries), 1)
        # Il movimento esistente vale per una riga sola: la seconda da 12,50 e'
        # un'altra spesa, e l'importo diverso non e' un doppione.
        self.assertEqual([r["duplicate"] for r in result], [True, False, False, False])
        self.assertEqual(result[0]["duplicateOf"]["id"], tx_id)

    def test_completeness_types_and_database_filter(self):
        for kind in ["Income", "Expenses", "Investment", "Transfers"]:
            # Investment sposta denaro come un giroconto: due conti, nessuna
            # categoria. Il tipo Savings non esiste piu'.
            tx = self.tx(transaction_type=kind, category="" if kind in {"Transfers", "Investment"} else "Food",
                         destination_name="Closed" if kind in {"Transfers", "Investment"} else None)
            self.assertEqual(missing_fields(tx), [])
            tx.account_name = None
            self.s.commit()
            self.assertIn("account", missing_fields(tx))
        tx = self.tx(transaction_type="Transfers", destination_name=None)
        self.assertIn("destination", missing_fields(tx))
        result = core_routes.transactions(1, self.s, incomplete=True, ids_only=True)
        self.assertEqual(len(result["ids"]), 5)

    def test_bulk_atomic_and_scope(self):
        rows = [self.tx() for _ in range(4)]
        ids = [t.id for t in rows[:3]]
        result = main.bulk_transactions(main.BulkTransactionsPayload(ids=ids, changes={"category": "Home"}), self.s)
        self.assertEqual(result["updated"], 3)
        self.assertEqual([t.category for t in rows], ["Home"] * 3 + ["Food"])
        with self.assertRaises(HTTPException):
            main.bulk_transactions(main.BulkTransactionsPayload(ids=ids, changes={"category": "Oops", "transaction_type": "Transfers"}), self.s)
        self.assertEqual([t.category for t in rows], ["Home"] * 3 + ["Food"])
        self.assertTrue(all(t.transaction_type == "Expenses" for t in rows))

    def test_excluded_budget_not_cash_and_inactive_history(self):
        tx = self.tx(counts_in_budget=False, account_name="Closed")
        self.assertEqual(core_routes.period_total(self.s, 2026, 9, "Expenses"), 0)
        account = next(a for a in core_routes.accounts(at=None, session=self.s)["items"] if a["name"] == "Closed")
        self.assertFalse(account["isActive"])
        self.assertEqual(account["calculatedBalance"], 37.5)
        self.assertEqual(core_routes.transactions(10, self.s)["total"], 1)
        payload = main.TransactionPayload(occurred_on="2026-09-07", transaction_type="Expenses",
                     category="Food", amount=12.5, account_name="Closed")
        with self.assertRaises(HTTPException):
            main.create_transaction(payload, self.s)
        self.s.rollback()
        main.update_transaction(tx.id, payload, self.s)

    def test_required_account_and_positive_amount(self):
        for kind in ["Income", "Expenses", "Investment", "Transfers"]:
            with self.assertRaises(HTTPException):
                main.create_transaction(main.TransactionPayload(occurred_on="2026-09-07",
                    transaction_type=kind, category="Food", amount=1), self.s)
            self.s.rollback()
        for amount in [0, -1, float("nan")]:
            with self.assertRaises(HTTPException):
                main.create_transaction(main.TransactionPayload(occurred_on="2026-09-07",
                    transaction_type="Expenses", category="Food", account_name="Bank", amount=amount), self.s)
            self.s.rollback()

    def test_partial_refunds_work_in_both_directions_and_roundtrip(self):
        self.s.add(BudgetPlan(period=date(2026, 9, 1), budget_type="Expenses",
                              category="Housing", amount=Decimal("500.00")))
        self.s.commit()
        expense = self.tx(category="Housing", amount=Decimal("915.00"))
        payload = main.TransactionPayload(occurred_on="2026-09-08", transaction_type="Income",
                  category="Refund", amount=305, account_name="Bank", refund_of_id=expense.id)
        refund = main.create_transaction(payload, self.s)
        self.assertTrue(expense.counts_in_budget)
        self.assertFalse(refund["countsInBudget"])
        self.assertEqual(core_routes.budget_actual(self.s, 2026, 9, "Expenses")["housing"], 610)
        summary = core_routes.summary_breakdown(2026, 9, self.s)["sections"]["expenses"]
        self.assertEqual(summary["actualTotal"], 610)
        self.assertEqual(core_routes._summary_core(self.s, 2026, 9)["control"]["overBudgetCategories"], 1)
        second = main.create_transaction(main.TransactionPayload(occurred_on="2026-09-09", transaction_type="Income",
            category="Refund", amount=100, account_name="Bank", refund_of_id=expense.id), self.s)
        self.assertEqual(core_routes.budget_actual(self.s, 2026, 9, "Expenses")["housing"], 510)
        income = self.tx(transaction_type="Income", category="Salary", amount=Decimal("100.00"))
        paid = main.create_transaction(main.TransactionPayload(occurred_on="2026-09-09", transaction_type="Expenses",
            category="Refund", amount=20, account_name="Bank", refund_of_id=income.id), self.s)
        self.assertFalse(paid["countsInBudget"])
        self.assertEqual(core_routes.budget_actual(self.s, 2026, 9, "Income")["salary"], 80)
        with self.assertRaises(HTTPException):
            main.create_transaction(main.TransactionPayload(occurred_on="2026-09-09", transaction_type="Income",
                category="Refund", amount=600, account_name="Bank", refund_of_id=expense.id), self.s)
        self.s.rollback()
        target = create_engine("sqlite://")
        Base.metadata.create_all(target)
        with Session(target) as other:
            import_data(other, build_export(self.s), source_name="roundtrip.xlsx")
            received = other.get(Transaction, refund["id"])
            self.assertFalse(received.counts_in_budget)
            self.assertEqual(other.get(Transaction, received.refund_of_id).category, "Housing")
            self.assertFalse(other.scalar(select(Account).where(Account.name == "Closed")).is_active)
        target.dispose()
        # Una spesa non puo' diventare un Investment in blocco: le mancherebbe
        # la destinazione, che per un Investment e' obbligatoria.
        for changes in [{"transaction_type": "Investment"}]:
            with self.assertRaises(HTTPException):
                main.bulk_transactions(main.BulkTransactionsPayload(ids=[expense.id], changes=changes), self.s)
        payload.refund_of_id = None
        main.update_transaction(refund["id"], payload, self.s)
        self.assertTrue(expense.counts_in_budget)
        self.assertTrue(self.s.get(Transaction, refund["id"]).counts_in_budget)

    def test_transfer_is_one_positive_movement(self):
        self.s.add(Account(name="Other", source_group="bank", starting_balance=0))
        self.s.commit()
        main.create_transfer(main.TransferPayload(occurred_on="2026-09-07", amount=10,
                             source_account="Bank", destination_account="Other"), self.s)
        rows = self.s.scalars(select(Transaction)).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].transaction_type, rows[0].amount), ("Transfers", Decimal("10")))
        balances = {a["name"]: a["calculatedBalance"] for a in core_routes.accounts(at=None, session=self.s)["items"]}
        self.assertEqual((balances["Bank"], balances["Other"]), (90, 10))

    def test_migration_idempotent_preserves_history(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE transactions (id INTEGER, amount NUMERIC)"))
            conn.execute(text("INSERT INTO transactions VALUES (1, 123.45)"))
            conn.execute(text("CREATE TABLE accounts (name TEXT, status TEXT, account_type TEXT, counts_in_net_worth BOOLEAN)"))
            for name, status in [("A", "closed"), ("B", "chiusa"), ("C", "v"), ("soldi da investire", "")]:
                conn.execute(text("INSERT INTO accounts (name, status, counts_in_net_worth) VALUES (:name,:status,false)"), dict(name=name, status=status))
        tracked_changes(engine)
        tracked_changes(engine)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT is_active FROM accounts")).scalars().all(), [0,0,1,0])
            self.assertIn("notes", {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)"))})
            self.assertNotIn("account_type", {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)"))})
            self.assertEqual(conn.execute(text("SELECT amount, counts_in_budget, refund_of_id FROM transactions")).one(), (123.45,1,None))
        engine.dispose()
