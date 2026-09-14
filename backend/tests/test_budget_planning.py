"""Le regole nuove del budget: quadratura, bisogni/piaceri, storico, riporto.

Girano su uno SQLite temporaneo, come gli altri aggregati: nessun database
reale, nessun mese "oggi" che cambia il risultato a seconda di quando lanci i
test.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import (_budget_balance, _needs_wants, _totali_mensili, budget_annual, budget_dashboard,
                             budget_suggestions, budget_trends, budget_trends_available_years, category_groups,
                             previous_month_leftover, settings, sync_savings_plan)
from app.database import Base
from app.models import AppSetting, BudgetPlan, Transaction
from app.notifications import _sforamenti_budget


def _tx(day: date, category: str, amount: str, tx_type: str = "Expenses") -> Transaction:
    return Transaction(occurred_on=day, effective_on=day, transaction_type=tx_type,
                       category=category, amount=Decimal(amount), account_type="Bank",
                       account_name="Conto", is_recurring_template=False)


def _rimborso(day: date, originale: Transaction, amount: str) -> Transaction:
    # I rimborsi nascono con `counts_in_budget = False`: non sono una spesa
    # dell'utente, e' denaro che rientra. Nettono l'originale via refund_of_id.
    return Transaction(occurred_on=day, effective_on=day, transaction_type="Expenses",
                       category=originale.category, amount=Decimal(amount), account_type="Bank",
                       account_name="Conto", is_recurring_template=False,
                       counts_in_budget=False, refund_of_id=originale.id)


def _piano(mese: int, category: str, amount: str, gruppo: str | None = None,
           tipo: str = "Expenses") -> BudgetPlan:
    return BudgetPlan(period=date(2026, mese, 1), budget_type=tipo, category=category,
                      category_group=gruppo, amount=Decimal(amount))


class BudgetBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()


class AnniBudgetTests(BudgetBase):
    def test_gli_anni_budget_arrivano_almeno_a_cinque_anni_da_oggi(self) -> None:
        ultimo = str(date.today().year + 5)
        dati = settings(self.session)
        self.assertIn(ultimo, dati["options"]["years"])
        self.assertTrue(all(ultimo in anni for anni in dati["budgetYearsByType"].values()))


class DashboardAnnualeTests(BudgetBase):
    def test_aggregazione_annuale_usa_tutti_i_mesi(self) -> None:
        self.session.add_all([
            _piano(1, "Casa", "100", "Needs"), _piano(2, "Casa", "200", "Needs"),
            _tx(date(2026, 1, 5), "Casa", "80"), _tx(date(2026, 2, 5), "Casa", "150"),
        ])
        self.session.commit()
        dati = budget_dashboard(2026, None, "Expenses", self.session)
        self.assertEqual((dati["periodMonth"], dati["plannedTotal"], dati["actualTotal"]), (None, 300.0, 230.0))
        self.assertEqual(dati["groups"][0]["actual"], 230.0)


class RisparmioDedottoTests(BudgetBase):
    """Il risparmio pianificato e' entrate meno spese: risulta, non si digita."""

    def test_il_risparmio_e_quello_che_avanza(self) -> None:
        self.session.add_all([
            _piano(9, "Stipendio", "3000", tipo="Income"),
            _piano(9, "Spesa", "2000"),
        ])
        self.session.commit()
        self.assertEqual(sync_savings_plan(self.session, date(2026, 9, 1)), Decimal("1000"))
        self.session.commit()
        riga = self.session.scalars(select(BudgetPlan).where(BudgetPlan.budget_type == "Savings")).one()
        self.assertEqual(riga.amount, Decimal("1000"))
        self.assertEqual(_budget_balance(self.session, 2026, 9)["savings"], 1000.0)

    def test_puo_venire_negativo(self) -> None:
        self.session.add_all([
            _piano(9, "Stipendio", "1000", tipo="Income"),
            _piano(9, "Spesa", "1500"),
        ])
        self.session.commit()
        self.assertEqual(sync_savings_plan(self.session, date(2026, 9, 1)), Decimal("-500"))
        self.session.commit()
        self.assertEqual(_budget_balance(self.session, 2026, 9)["savings"], -500.0)

    def test_una_riga_gia_scritta_a_mano_viene_riallineata(self) -> None:
        self.session.add_all([
            _piano(9, "Stipendio", "1000", tipo="Income"),
            _piano(9, "Spesa", "400"),
            _piano(9, "Savings", "999", tipo="Savings"),
        ])
        self.session.commit()
        sync_savings_plan(self.session, date(2026, 9, 1))
        self.session.commit()
        riga = self.session.scalars(select(BudgetPlan).where(BudgetPlan.budget_type == "Savings")).one()
        self.assertEqual(riga.amount, Decimal("600"))

    def test_un_mese_vuoto_non_si_inventa_una_riga(self) -> None:
        self.assertEqual(sync_savings_plan(self.session, date(2026, 9, 1)), Decimal(0))
        self.session.commit()
        self.assertEqual(self.session.scalars(select(BudgetPlan)).all(), [])

    def test_senza_entrate_pianificate_lo_dice(self) -> None:
        self.session.add(_piano(9, "Spesa", "2000"))
        self.session.commit()
        saldo = _budget_balance(self.session, 2026, 9)
        self.assertFalse(saldo["hasIncomePlan"])
        self.assertEqual(saldo["savings"], -2000.0)


class RisparmioAnnualeTests(BudgetBase):
    def test_somma_tutti_i_mesi_dell_anno(self) -> None:
        self.session.add_all([
            _piano(1, "Stipendio", "1000", tipo="Income"), _piano(2, "Stipendio", "1000", tipo="Income"),
            _piano(1, "Spesa", "700"), _piano(2, "Spesa", "900"),
        ])
        self.session.commit()
        self.assertEqual(_budget_balance(self.session, 2026)["savings"], 400.0)
        self.assertEqual(_budget_balance(self.session, 2026, 2)["savings"], 100.0)


class AvanzoDelMesePrecedenteTests(BudgetBase):
    """L'avanzo si legge, non si somma: i conti del mese restano il pianificato."""

    def setUp(self) -> None:
        super().setUp()
        self.session.add_all([
            _piano(7, "Spesa", "400"), _tx(date(2026, 7, 5), "Spesa", "300"),   # avanzano 100
            _piano(8, "Spesa", "400"), _tx(date(2026, 8, 5), "Spesa", "450"),   # mancano 50
            _piano(9, "Spesa", "400"),
        ])
        self.session.commit()

    def test_guarda_solo_il_mese_prima(self) -> None:
        # Settembre vede agosto (-50), non la somma di luglio e agosto (+50).
        self.assertEqual(previous_month_leftover(self.session, 2026, 9), {"spesa": -50.0})
        self.assertEqual(previous_month_leftover(self.session, 2026, 8), {"spesa": 100.0})

    def test_gennaio_non_guarda_indietro_e_vale_solo_per_le_spese(self) -> None:
        self.assertEqual(previous_month_leftover(self.session, 2026, 1), {})
        self.assertEqual(previous_month_leftover(self.session, 2026, 9, "Income"), {})

    def test_non_entra_nei_conti_del_mese(self) -> None:
        from app.core_routes import budgets
        dati = budgets(2026, 9, "Expenses", self.session)
        voce = dati["items"][0]
        self.assertEqual(voce["amount"], 400.0)
        self.assertEqual(voce["previousLeftover"], -50.0)
        self.assertEqual(dati["plannedTotal"], 400.0)

    def test_gli_avvisi_misurano_il_pianificato(self) -> None:
        # Con il riporto sommato al budget questo sforamento sparirebbe: non
        # deve sparire, il budget di settembre e' 400 e basta.
        self.session.add(_tx(date(2026, 9, 9), "Spesa", "500"))
        self.session.commit()
        avvisi = _sforamenti_budget(self.session, date(2026, 9, 15))
        self.assertEqual([avviso["params"]["planned"] for avviso in avvisi], [400.0])

    def test_il_rimborso_si_netta_dalla_categoria_originale(self) -> None:
        # Il setUp mette a agosto una spesa di 450. Aggiungo una spesa di 500
        # rimborsata di 200: agosto totale netto = 450 + 500 - 200 = 750.
        # Avanzo = 400 - 750 = -350. Senza netting sarebbe 400 - 950 = -550.
        spesa_agosto = _tx(date(2026, 8, 5), "Spesa", "500")
        self.session.add(spesa_agosto)
        self.session.flush()
        self.session.add(_rimborso(date(2026, 8, 20), spesa_agosto, "200"))
        self.session.commit()
        self.assertEqual(previous_month_leftover(self.session, 2026, 9), {"spesa": -350.0})

    def test_il_rimborso_netto_va_nel_mese_dell_originale(self) -> None:
        # Spesa a luglio (300 di setUp piu' 500 nuova) rimborsata a ottobre:
        # agosto vede l'avanzo di luglio gia' corretto: 400 - 800 = -400.
        # Senza netting vedrebbe 400 - 800 = -400, ma il fix conta anche 200 in
        # piu' dal rimborso di luglio (perche' il netting e' sul mese originale).
        spesa_luglio = _tx(date(2026, 7, 5), "Spesa", "500")
        self.session.add(spesa_luglio)
        self.session.flush()
        self.session.add(_rimborso(date(2026, 10, 1), spesa_luglio, "200"))
        self.session.commit()
        # Luglio netto = 300 + 500 - 200 = 600. Avanzo = 400 - 600 = -200.
        self.assertEqual(previous_month_leftover(self.session, 2026, 8), {"spesa": -200.0})


class AndamentoStoricoTests(BudgetBase):
    """Le tendenze mostrano i movimenti reali anche per anni senza budget."""

    def test_un_anno_senza_piano_mostra_comunque_lo_speso_reale(self) -> None:
        # 2023 non ha BudgetPlan ma ha transazioni: deve vedere lo speso reale.
        self.session.add_all([
            _tx(date(2023, 3, 10), "Spesa", "200"),
            _tx(date(2023, 7, 5), "Spesa", "350"),
        ])
        self.session.commit()
        totali = budget_annual(2023, "Expenses", self.session)["monthTotals"]
        # Gli `items` sono vuoti (nessun piano), ma i totali devono riflettere
        # lo speso reale: planned 0, actual quanto effettivamente speso.
        self.assertEqual(sum(row["planned"] for row in totali), 0.0)
        self.assertAlmostEqual(totali[2]["actual"], 200.0)
        self.assertAlmostEqual(totali[6]["actual"], 350.0)

    def test_le_tendenze_includono_anni_senza_piano(self) -> None:
        self.session.add(_tx(date(2023, 4, 10), "Spesa", "150"))
        self.session.add(_tx(date(2023, 9, 10), "Spesa", "80"))
        self.session.commit()
        dati = budget_trends(years="2023,2024", budget_type="Expenses", session=self.session)
        anno_2023 = next(item for item in dati["years"] if item["year"] == 2023)
        self.assertAlmostEqual(anno_2023["actualTotal"], 230.0)
        # Senza piano, plannedTotal e' zero: va bene, la linea "actual" deve
        # comunque essere visibile sui 230 euro reali.
        self.assertEqual(anno_2023["plannedTotal"], 0.0)

    def test_gli_anni_disponibili_solo_se_hanno_dati(self) -> None:
        # 2022 e 2024 hanno transazioni, 2023 no: deve uscire solo [2022, 2024].
        self.session.add_all([
            _tx(date(2022, 5, 5), "Spesa", "100"),
            _tx(date(2024, 6, 5), "Spesa", "200"),
            BudgetPlan(period=date(2025, 1, 1), budget_type="Expenses",
                       category="Spesa", amount=Decimal("300")),
        ])
        self.session.commit()
        anni = budget_trends_available_years(self.session)["years"]
        self.assertIn(2022, anni)
        self.assertIn(2024, anni)
        self.assertIn(2025, anni)
        self.assertNotIn(2023, anni)

    def test_il_multiselettore_ignora_anni_non_nella_lista(self) -> None:
        # L'utente sceglie anni specifici: il backend deve rispettare la lista.
        self.session.add(_tx(date(2023, 1, 5), "Spesa", "10"))
        self.session.add(_tx(date(2024, 2, 5), "Spesa", "20"))
        self.session.add(_tx(date(2025, 3, 5), "Spesa", "30"))
        self.session.commit()
        dati = budget_trends(years="2023,2025", budget_type="Expenses", session=self.session)
        anni_ritornati = sorted(item["year"] for item in dati["years"])
        self.assertEqual(anni_ritornati, [2023, 2025])


if __name__ == '__main__':
    unittest.main()


class RisparmioMensileTests(BudgetBase):
    """I totali mensili del risparmio si ricavano, non si sommano dai movimenti.

    I movimenti di tipo Savings erano la ribattitura a mano di entrate meno
    spese e non si scrivono piu': sommarli restituiva dodici mesi a zero, e con
    essi un grafico delle tendenze piatto che sembrava un guasto invece di un
    dato mancante. La formula viveva in due posti, e solo uno dei due la
    conosceva.
    """

    def _entrate_e_spese(self) -> None:
        self.session.add_all([
            _tx(date(2026, 1, 10), "Stipendio", "2000", "Income"),
            _tx(date(2026, 1, 20), "Casa", "1200"),
            _tx(date(2026, 2, 10), "Stipendio", "2000", "Income"),
            _tx(date(2026, 2, 20), "Casa", "1500"),
        ])
        self.session.commit()

    def test_i_totali_mensili_seguono_entrate_meno_spese(self) -> None:
        self._entrate_e_spese()
        mensili = _totali_mensili(self.session, 2026, "Savings")
        self.assertEqual((mensili[1], mensili[2]), (800.0, 500.0))

    def test_la_griglia_annuale_e_i_totali_mensili_concordano(self) -> None:
        # Le due strade per lo stesso numero: la riga per categoria e il
        # totale di mese. Divergevano, ed e' da li' che nasceva il guasto.
        self._entrate_e_spese()
        mensili = _totali_mensili(self.session, 2026, "Savings")
        totali = budget_annual(2026, "Savings", self.session)["monthTotals"]
        self.assertEqual([riga["actual"] for riga in totali[:2]], [mensili[1], mensili[2]])

    def test_le_tendenze_non_sono_piatte_a_zero(self) -> None:
        self._entrate_e_spese()
        tendenze = budget_trends("2026", 2024, 2027, "Savings", self.session)
        self.assertEqual(tendenze["years"][0]["actualTotal"], 1300.0)

    def test_l_effettivo_esiste_anche_nei_mesi_senza_piano(self) -> None:
        # Nessun piano di risparmio per gennaio: l'effettivo non dipende da
        # quello, altrimenti sparisce nei mesi non ancora pianificati.
        self._entrate_e_spese()
        dati = budget_dashboard(2026, 1, "Savings", self.session)
        self.assertEqual((dati["plannedTotal"], dati["actualTotal"]), (0, 800.0))
