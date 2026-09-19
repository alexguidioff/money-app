from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.calculation_engine import account_balances_at, debito_pianificato_al
from app.core_routes import accounts, budget_actual, movimenti_per_saldi
from app.database import Base
from app.main import LiabilityTransferPayload, TransactionPayload, create_liability_transfer, create_transaction, liabilities
from app.models import Account, AppSetting, LiabilityProfile, LiabilityTransactionDetail, Transaction
from tests.categorie_fixture import categoria


class LiabilityTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(source_group="bank", name="Banca", starting_balance=Decimal("1000"),
                    current_balance=Decimal("1000"), is_active=True),
            Account(source_group="liability", name="Mutuo", starting_balance=Decimal("-1000"),
                    current_balance=Decimal("-1000"), is_active=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_la_rata_e_un_solo_movimento_debt(self) -> None:
        # La categoria e' una riga: la fixture la crea e passa l'id. Il nome
        # resta solo nelle chiavi del budget, che adesso sono id.
        interessi = categoria(self.session, "Interessi")
        create_transaction(TransactionPayload(
            occurred_on="2026-09-08", transaction_type="Expenses", categoryId=interessi,
            amount=10, account_name="Mutuo", details="Interessi settembre"), self.session)
        result = create_liability_transfer(LiabilityTransferPayload(
            occurred_on="2026-09-08", source_account="Banca", destination_account="Mutuo",
            principal_amount=100, interest_amount=10, details="Rata settembre"), self.session)

        rows = self.session.scalars(select(Transaction).order_by(Transaction.id)).all()
        detail = self.session.scalar(select(LiabilityTransactionDetail)
                                     .where(LiabilityTransactionDetail.kind == "repayment"))
        balances = account_balances_at(self.session.scalars(select(Account)).all(),
                                       movimenti_per_saldi(self.session), date(2026, 9, 8))
        by_name = {row["name"]: row["balance"] for row in balances["accounts"]}

        debt_rows = [row for row in rows if row.transaction_type == "Debt"]
        self.assertEqual(1, len(debt_rows))
        self.assertEqual((Decimal("100.00"), Decimal("10.00")),
                         (detail.principal_amount, detail.interest_amount))
        self.assertEqual(890.0, by_name["Banca"])
        self.assertEqual(900.0, abs(by_name["Mutuo"]))
        # `budget_actual` chiave per id di categoria: la chiave e' l'id della
        # riga creata qui sopra, non piu' il nome.
        self.assertEqual({interessi: 10.0}, budget_actual(self.session, 2026, 9, "Expenses"))
        self.assertEqual([debt_rows[0].id], result["transactionIds"])

    def test_saldo_e_registro_includono_giroconti_e_oneri(self) -> None:
        oggi = date.today().isoformat()
        interessi = categoria(self.session, "Interessi")
        create_transaction(TransactionPayload(
            occurred_on=oggi, transaction_type="Transfers", amount=100,
            account_name="Banca", destination_name="Mutuo"), self.session)
        create_transaction(TransactionPayload(
            occurred_on=oggi, transaction_type="Expenses", amount=25,
            account_name="Mutuo", categoryId=interessi, debt_interest=25), self.session)
        dati = liabilities(self.session)
        voce = dati["items"][0]
        saldo = next(r["value"] for r in accounts(at=None, session=self.session)["items"]
                     if r["name"] == "Mutuo")
        self.assertEqual(925, saldo)
        self.assertEqual(saldo, voce["actualTotalDebt"])
        self.assertEqual(saldo, dati["summary"]["totalDebt"])
        self.assertEqual(-100, voce["reconciliationDifference"])
        self.assertEqual([-100, 25], sorted(r["effect"] for r in voce["movements"]))
        self.assertEqual({interessi: 25}, budget_actual(self.session, date.today().year,
                                                       date.today().month, "Expenses"))

    def test_confronto_e_grafico_arrivano_a_oggi(self) -> None:
        oggi = date.today()
        conto = self.session.scalar(select(Account).where(Account.name == "Mutuo"))
        inizio = oggi - timedelta(days=70)
        self.session.add(LiabilityProfile(
            account_id=conto.id, original_principal=1000, annual_rate=12,
            start_date=inizio, end_date=oggi + timedelta(days=365)))
        self.session.commit()
        voce = liabilities(self.session)["items"][0]
        previsto = debito_pianificato_al(voce["schedule"],
                    [{"occurredOn": inizio.isoformat(), "amount": 1000}], 12, oggi)
        self.assertEqual(float(previsto), voce["comparison"]["plannedDebt"])
        corrente = next(r for r in voce["trend"] if r["period"] == oggi.isoformat())
        self.assertEqual(voce["actualTotalDebt"], corrente["actualDebt"])
        self.assertTrue(all(r["actualDebt"] is None for r in voce["trend"]
                            if r["period"] > oggi.isoformat()))

    def test_data_effettiva_esclude_movimenti_futuri(self) -> None:
        result = create_transaction(TransactionPayload(
            occurred_on=date.today().isoformat(), transaction_type="Debt", amount=100,
            account_name="Banca", destination_name="Mutuo", debt_principal=100,
            debt_interest=0), self.session)
        tx = self.session.get(Transaction, result["id"])
        tx.effective_on = date.today() + timedelta(days=1)
        self.session.commit()
        voce = liabilities(self.session)["items"][0]
        self.assertEqual(1000, voce["actualTotalDebt"])
        self.assertEqual(0, voce["principalRepaid"])
        self.assertEqual([], voce["movements"])

    def test_stato_reale_senza_profilo_usa_solo_gli_oneri_registrati(self) -> None:
        create_transaction(TransactionPayload(
            occurred_on=date.today().isoformat(), transaction_type="Expenses",
            categoryId=categoria(self.session, "Interessi"),
            amount=25, account_name="Mutuo", details="Interessi capitalizzati",
            debt_interest=25), self.session)

        result = next(row for row in liabilities(self.session)["items"] if row["name"] == "Mutuo")

        self.assertEqual((1000.0, 25.0, 25.0, 1025.0),
                         (result["outstanding"], result["interestCharged"],
                          result["interestOutstanding"], result["actualTotalDebt"]))

    def test_un_addebito_non_dichiarato_non_conta_e_si_vede(self) -> None:
        """Una spesa sul conto del debito non e' un interesse finche' non lo dici.

        Prima bastava che fosse una spesa su quel conto: qualunque addebito
        estraneo - un piano di accumulo, un'imposta - entrava fra gli interessi
        e nessuno lo segnalava. Adesso resta fuori dai totali e compare fra
        quelli da classificare, dove si vede e si corregge.
        """
        create_transaction(TransactionPayload(
            occurred_on=date.today().isoformat(), transaction_type="Expenses",
            categoryId=categoria(self.session, "Varie"),
            amount=40, account_name="Mutuo", details="spesa da classificare"), self.session)

        dati = liabilities(self.session)
        voce = next(row for row in dati["items"] if row["name"] == "Mutuo")
        self.assertEqual(0.0, voce["interestCharged"], "un addebito non dichiarato non e' un interesse")
        self.assertEqual(1, voce["unclassified"]["count"])
        self.assertEqual(40.0, voce["unclassified"]["amount"])
        self.assertEqual(1, dati["summary"]["unclassifiedCount"])

    def test_la_divisione_deve_quadrare(self) -> None:
        with self.assertRaises(HTTPException) as error:
            create_transaction(TransactionPayload(
                occurred_on="2026-09-08", transaction_type="Debt", amount=100,
                account_name="Banca", destination_name="Mutuo",
                debt_principal=80, debt_interest=10), self.session)
        self.assertEqual("liabilityInvalidSplit", error.exception.detail)

    def test_un_transfer_non_entra_nel_registro_debito(self) -> None:
        for transaction_type, source, destination, amount in (
            ("Debt", "Mutuo", "Banca", 100),
            ("Debt", "Banca", "Mutuo", 30),
            ("Transfers", "Mutuo", "Banca", 10),
        ):
            create_transaction(TransactionPayload(
                occurred_on="2026-09-08", transaction_type=transaction_type, amount=amount,
                account_name=source, destination_name=destination,
                debt_principal=amount if transaction_type == "Debt" else None,
                debt_interest=0 if transaction_type == "Debt" else None), self.session)
        details = self.session.scalars(select(LiabilityTransactionDetail)).all()
        self.assertEqual(["drawdown", "repayment"], [row.kind for row in details])
        self.assertEqual(Decimal("100.00"), sum((row.principal_amount for row in details
                                                 if row.kind == "drawdown"), Decimal("0")))

    def test_serve_esattamente_un_conto_debito(self) -> None:
        self.session.add(Account(source_group="bank", name="Altra banca", starting_balance=0,
                                 current_balance=0, is_active=True))
        self.session.commit()
        with self.assertRaises(HTTPException) as error:
            create_liability_transfer(LiabilityTransferPayload(
                occurred_on="2026-09-08", source_account="Banca", destination_account="Altra banca",
                principal_amount=100), self.session)
        self.assertEqual({"code": "debtNeedsLiability"}, error.exception.detail)

    def test_interessi_e_transfer_non_creano_capitale_residuo(self) -> None:
        mutuo = self.session.scalars(select(Account).where(Account.name == "Mutuo")).one()
        mutuo.starting_balance = mutuo.current_balance = Decimal("0")
        self.session.commit()
        for source, destination, amount, principal, interest in (
            ("Mutuo", "Banca", 1000, 1000, 0),
            ("Banca", "Mutuo", 1000, 1000, 0),
            ("Banca", "Mutuo", 50, 0, 50),
        ):
            create_transaction(TransactionPayload(
                occurred_on="2026-09-08", transaction_type="Debt", amount=amount,
                account_name=source, destination_name=destination,
                debt_principal=principal, debt_interest=interest), self.session)
        create_transaction(TransactionPayload(
            occurred_on="2026-09-08", transaction_type="Transfers", amount=70,
            account_name="Banca", destination_name="Mutuo"), self.session)

        result = next(row for row in liabilities(self.session)["items"] if row["name"] == "Mutuo")
        self.assertEqual((0.0, 1000.0, 1000.0, 50.0),
                         (result["outstanding"], result["drawnPrincipal"],
                          result["principalRepaid"], result["interestPaid"]))

    def test_api_separa_debito_addebiti_e_pagamenti(self) -> None:
        """Capitale, interessi addebitati e interessi pagati restano distinti.

        Gli interessi non si ricavano piu' dal tasso: arrivano dagli addebiti
        registrati come spesa sul conto del debito. Prima questa prova si
        affidava al calcolo (36,5% su 1000 = un euro al giorno per dieci
        giorni) e per questo era rossa: i numeri attesi erano giusti, era il
        preparativo a contare su una funzione che non c'e' piu'.
        """
        today = date.today()
        mutuo = self.session.scalars(select(Account).where(Account.name == "Mutuo")).one()
        mutuo.starting_balance = mutuo.current_balance = Decimal("0")
        self.session.add(LiabilityProfile(
            account_id=mutuo.id, original_principal=Decimal("1000"), annual_rate=Decimal("36.5"),
            start_date=today - timedelta(days=10), repayment_start_date=today + timedelta(days=30),
            end_date=today + timedelta(days=365), planned_drawdowns=None))
        self.session.commit()
        create_transaction(TransactionPayload(
            occurred_on=(today - timedelta(days=10)).isoformat(), transaction_type="Debt", amount=1000,
            account_name="Mutuo", destination_name="Banca", debt_principal=1000, debt_interest=0), self.session)
        # L'addebito della banca: una spesa sul conto del debito.
        create_transaction(TransactionPayload(
            occurred_on=today.isoformat(), transaction_type="Expenses", amount=10,
            categoryId=categoria(self.session, "Interessi"), account_name="Mutuo",
            details="interessi passivi", debt_interest=10), self.session)
        create_transaction(TransactionPayload(
            occurred_on=today.isoformat(), transaction_type="Debt", amount=104,
            account_name="Banca", destination_name="Mutuo", debt_principal=100, debt_interest=4), self.session)

        result = next(row for row in liabilities(self.session)["items"] if row["name"] == "Mutuo")
        self.assertEqual((1000.0, 10.0, 100.0, 4.0, 900.0, 6.0, 906.0),
                         (result["drawnPrincipal"], result["interestCharged"], result["principalRepaid"],
                          result["interestPaid"], result["outstanding"], result["interestOutstanding"],
                          result["actualTotalDebt"]))


if __name__ == "__main__":
    unittest.main()
