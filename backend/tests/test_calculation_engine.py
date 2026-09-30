from datetime import date
from decimal import Decimal
from unittest import TestCase

from app.calculation_engine import amortization_schedule, cape_stock_weight, calculate_account_balance, effective_date, investment_positions, period_metrics, savings_rate


class CalculationEngineTests(TestCase):
    def test_effective_date_shifts_late_movements(self):
        self.assertEqual(effective_date(date(2026, 7, 27), "Income", "Active", 20), date(2026, 8, 1))
        self.assertEqual(effective_date(date(2026, 7, 19), "Income", "Active", 20), date(2026, 7, 19))
        # Anche le uscite: la spesa del 30 appartiene al mese dopo come lo
        # stipendio del 27, ed e' quello che rende leggibile l'elenco.
        self.assertEqual(effective_date(date(2026, 7, 27), "Expenses", "Active", 20), date(2026, 8, 1))
        self.assertEqual(effective_date(date(2026, 7, 27), "expenses", "Active", 20), date(2026, 8, 1))
        # Dicembre scivola sull'anno dopo, e il 31 dicembre e' il caso che lo
        # dimostra: senza, il mese di competenza sarebbe il 13.
        self.assertEqual(effective_date(date(2026, 12, 31), "Expenses", "Active", 20), date(2027, 1, 1))
        # Giroconti, investimenti e debiti no: non sono ne' spese ne' incassi.
        self.assertEqual(effective_date(date(2026, 7, 27), "Transfers", "Active", 20), date(2026, 7, 27))
        self.assertEqual(effective_date(date(2026, 7, 27), "Investment", "Active", 20), date(2026, 7, 27))
        self.assertEqual(effective_date(date(2026, 7, 27), "Income", "Inactive", 20), date(2026, 7, 27))

    def test_account_balance_matches_excel_sign_rules_and_transfers(self):
        transactions = [
            {"transaction_type": "income", "amount": 100, "account_name": "A", "destination_name": None},
            {"transaction_type": "Expenses", "amount": 30, "account_name": "A", "destination_name": None},
            {"transaction_type": "Transfers", "amount": 25, "account_name": "A", "destination_name": "B"},
            {"transaction_type": "Transfers", "amount": 10, "account_name": "b", "destination_name": "a"},
        ]
        self.assertEqual(calculate_account_balance(50, "A", transactions), Decimal("105.00"))
        self.assertEqual(calculate_account_balance(0, "B", transactions), Decimal("15.00"))

    def test_un_giroconto_fra_valute_diverse_muove_i_due_importi(self):
        # Mille euro che partono e novecentoventicinque franchi che arrivano: il
        # cambio se lo tiene la banca, e i due conti si muovono di due cifre
        # diverse. Con un importo solo, uno dei due saldi sarebbe in un'altra
        # valuta contata come se fosse la sua.
        transactions = [
            {"transaction_type": "Transfers", "amount": 1000, "account_name": "Euro",
             "destination_name": "Franchi", "destination_amount": Decimal("925.00")},
        ]
        self.assertEqual(calculate_account_balance(0, "Euro", transactions), Decimal("-1000.00"))
        self.assertEqual(calculate_account_balance(0, "Franchi", transactions), Decimal("925.00"))

    def test_anche_nella_stessa_valuta_i_due_importi_possono_differire(self):
        # Una banca puo' addebitare milleseicento e accreditarne milleseicentodue
        # e quaranta: il movimento porta tutte e due le cifre, e contarle uguali
        # farebbe sbagliare il conto d'arrivo di due e quaranta.
        transactions = [
            {"transaction_type": "Transfers", "amount": 1600, "account_name": "A",
             "destination_name": "B", "destination_amount": Decimal("1602.40")},
        ]
        self.assertEqual(calculate_account_balance(0, "A", transactions), Decimal("-1600.00"))
        self.assertEqual(calculate_account_balance(0, "B", transactions), Decimal("1602.40"))
        # Un movimento senza secondo importo muove la stessa cifra da tutte e due
        # le parti, come ha sempre fatto.
        self.assertEqual(calculate_account_balance(0, "B", [
            {"transaction_type": "Transfers", "amount": 1600, "account_name": "A", "destination_name": "B"},
        ]), Decimal("1600.00"))

    def test_savings_rate_is_what_is_left_of_income(self):
        self.assertEqual(savings_rate(1000, 600), Decimal("0.4000"))
        self.assertIsNone(savings_rate(0, 600))

    def test_period_metrics_tracks_budget_and_days(self):
        transactions = [
            {"effective_on": date(2026, 7, 1), "transaction_type": "Income", "amount": 1000},
            {"effective_on": date(2026, 7, 2), "transaction_type": "Expenses", "amount": 300},
            {"effective_on": date(2026, 7, 3), "transaction_type": "Savings", "amount": 200},
        ]
        budgets = [
            {"period": date(2026, 7, 1), "budget_type": "Expenses", "amount": 500},
            {"period": date(2026, 7, 1), "budget_type": "Savings", "amount": 250},
        ]
        result = period_metrics(transactions, budgets, 2026, 7, today=date(2026, 7, 10))
        self.assertEqual(result["tracking_balance"], Decimal("500.00"))
        self.assertEqual(result["budget_delta"]["expenses"], Decimal("200.00"))
        self.assertEqual(result["days_passed"], 10)

    def test_investment_positions_use_average_cost_and_realize_sales(self):
        rows = [
            {"id": 1, "occurred_on": date(2026, 1, 1), "name": "ETF", "transaction_type": "Buy", "amount": 100, "units": 10, "price": 10, "currency": "EUR"},
            {"id": 2, "occurred_on": date(2026, 2, 1), "name": "ETF", "transaction_type": "Buy", "amount": 60, "units": 5, "price": 12, "currency": "EUR"},
            {"id": 3, "occurred_on": date(2026, 3, 1), "name": "ETF", "transaction_type": "Sell", "amount": -52, "units": -4, "price": 13, "currency": "EUR"},
        ]
        position = investment_positions(rows, {1: 1}, {"ETF": 14})[0]
        self.assertEqual(position["units"], Decimal("11"))
        self.assertEqual(position["cost_basis"], Decimal("118.07"))
        self.assertEqual(position["realized_gain"], Decimal("9.07"))
        self.assertEqual(position["market_value"], Decimal("154.00"))
        self.assertEqual(position["total_gain"], Decimal("45.00"))

    def test_cape_weight_interpolates_and_clamps(self):
        self.assertEqual(cape_stock_weight(15, 17, 26, 37, 1, 0.8, 0), Decimal("1"))
        self.assertEqual(cape_stock_weight(26, 17, 26, 37, 1, 0.8, 0), Decimal("0.8000"))
        self.assertEqual(cape_stock_weight(40, 17, 26, 37, 1, 0.8, 0), Decimal("0"))

    def test_amortization_reaches_zero_and_keeps_interest_separate(self):
        rows = amortization_schedule(1200, 12, date(2026, 1, 1), date(2027, 1, 1))
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[-1]["remaining"], 0.0)
        self.assertGreater(sum(row["interest"] for row in rows), 0)
        self.assertAlmostEqual(sum(row["principal"] for row in rows), 1200, places=2)

    def test_staged_bullet_loan_accrues_each_tranche_from_its_date(self):
        drawdowns = [
            {"occurredOn": "2023-01-12", "amount": 10000},
            {"occurredOn": "2023-08-02", "amount": 10000},
            {"occurredOn": "2024-02-12", "amount": 10000},
            {"occurredOn": "2024-08-19", "amount": 10000},
        ]
        rows = amortization_schedule(40000, 1.5, date(2023, 1, 12), date(2026, 8, 19),
                                     "annual", "bullet", drawdowns, date(2024, 8, 19))
        self.assertEqual(rows[0]["principal"], 40000.0)
        self.assertEqual(rows[0]["interest"], 1675.48)
        self.assertEqual(rows[0]["payment"], 41675.48)
        self.assertEqual(rows[-1]["remaining"], 0.0)
