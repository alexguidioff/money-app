"""Il budget in due valute: ogni scheda legge solo i movimenti della sua.

Il caso di partenza e' quello di chi ha un conto in franchi e spese nelle due
valute. Una spesa in franchi e una in euro sulla stessa categoria sono due
numeri che non si incontrano: il budget in franchi si misura in franchi, quello
in euro in euro, e nessuno dei due passa da un cambio.

Senza valuta dichiarata resta il comportamento di sempre - tutto convertito in
euro al cambio del giorno - ed e' il percorso delle pagine che sommano conti
diversi: la Panoramica, le analisi, il FIRE. Il primo test lo fissa, perche' e'
la promessa che tiene in piedi il resto: chi ha solo conti in euro non deve
accorgersi di niente.

I cambi e i numeri sono inventati e tondi: questo repository e' pubblico.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import (_budget_balance, _needs_wants, _totali_mensili, budget_actual,
                             budget_annual, budget_currencies, budget_dashboard, calculations,
                             previous_month_leftover, sync_savings_plan)
from app.database import Base
from app.main import BudgetBulkPayload, BudgetCopyPayload, bulk_budget, copy_budget
from app.models import Account, BudgetPlan, MarketPrice, Transaction
from app.notifications import _sforamenti_budget
from tests.categorie_fixture import categoria


def _cambio(session: Session, codice: str, giorno: date, valore: str) -> None:
    """Un cambio nel listino, come lo scriverebbe lo scarico delle quotazioni."""
    session.add(MarketPrice(symbol=f"EUR{codice}=X", observed_on=giorno, price=Decimal(valore)))


class ValuteDelBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(name="Conto", source_group="bank", currency="EUR", starting_balance=Decimal("0"),
                    current_balance=Decimal("0"), counts_in_net_worth=True, is_active=True),
            Account(name="Revolut", source_group="bank", currency="CHF", starting_balance=Decimal("0"),
                    current_balance=Decimal("0"), counts_in_net_worth=True, is_active=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    # -- attrezzi ----------------------------------------------------------

    def _movimento(self, giorno: date, importo: str, valuta: str, *,
                   categoria_nome: str = "Casa", tipo: str = "Expenses") -> Transaction:
        riga = Transaction(occurred_on=giorno, effective_on=giorno, transaction_type=tipo,
                           category_id=categoria(self.session, categoria_nome), amount=Decimal(importo),
                           account_name="Revolut" if valuta == "CHF" else "Conto", currency=valuta,
                           is_recurring_template=False)
        self.session.add(riga)
        self.session.commit()
        return riga

    def _piano(self, mese: int, importo: str, valuta: str, *, categoria_nome: str = "Casa",
               tipo: str = "Expenses", anno: int = 2026) -> BudgetPlan:
        riga = BudgetPlan(period=date(anno, mese, 1), budget_type=tipo, currency=valuta,
                          category_id=categoria(self.session, categoria_nome), amount=Decimal(importo))
        self.session.add(riga)
        self.session.commit()
        return riga

    def _casa(self, anno: int, mese: int, valuta: str | None = None) -> float:
        return budget_actual(self.session, anno, mese, "Expenses", valuta)[categoria(self.session, "Casa")]

    def _piani(self, valuta: str, tipo: str = "Expenses") -> list[BudgetPlan]:
        return list(self.session.scalars(select(BudgetPlan).where(
            BudgetPlan.currency == valuta, BudgetPlan.budget_type == tipo)).all())

    # -- la regola ---------------------------------------------------------

    def test_un_budget_in_franchi_non_si_mangia_la_spesa_in_euro(self) -> None:
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00", "EUR")
        self._movimento(date(2026, 7, 11), "100.00", "CHF")
        # Ogni scheda vede il suo: cento euro e cento franchi non si sommano.
        self.assertEqual(100.0, self._casa(2026, 7, "EUR"))
        self.assertEqual(100.0, self._casa(2026, 7, "CHF"))
        # Senza valuta dichiarata e' il numero di sempre: cento franchi a 1,10
        # sono 90,91 euro, e il totale e' 190,91.
        self.assertAlmostEqual(190.91, self._casa(2026, 7), places=2)

    def test_ogni_valuta_ha_il_suo_risparmio(self) -> None:
        self._movimento(date(2026, 7, 10), "1000.00", "CHF", categoria_nome="Stipendio", tipo="Income")
        self._movimento(date(2026, 7, 11), "400.00", "CHF")
        self._piano(7, "1000.00", "CHF", categoria_nome="Stipendio", tipo="Income")
        self._piano(7, "300.00", "CHF")
        self.session.commit()
        sync_savings_plan(self.session, date(2026, 7, 1), "CHF")
        # Il risparmio in franchi sono i franchi: entrate meno spese pianificate,
        # e il piano del risparmio non passa dal cambio.
        self.assertEqual([700.0], [float(riga.amount) for riga in self._piani("CHF", "Savings")])
        # L'effettivo della stessa scheda: mille franchi entrati, quattrocento
        # usciti. Il valore sta sotto la categoria del risparmio, che qui non
        # esiste ancora come riga e quindi arriva senza nome.
        self.assertEqual([600.0], list(budget_actual(self.session, 2026, 7, "Savings", "CHF").values()))

    def test_copiare_l_euro_non_cancella_il_franchio(self) -> None:
        self._piano(8, "500.00", "EUR")
        self._piano(9, "400.00", "CHF")
        self.session.commit()
        copy_budget(BudgetCopyPayload(source_year=2026, source_month=8, target_year=2026, target_month=9,
                                      currency="EUR"), self.session)
        # I franchi di settembre non erano nella copia e restano dove sono: e' il
        # danno piu' probabile di tutta la modifica, ed e' silenzioso.
        self.assertEqual([400.0], [float(riga.amount) for riga in self._piani("CHF")])
        self.assertEqual([500.0], [float(riga.amount) for riga in self._piani("EUR") if riga.period.month == 9])

    def test_bulk_scrive_solo_nella_sua_valuta(self) -> None:
        self._piano(9, "100.00", "EUR")
        self.session.commit()
        bulk_budget(BudgetBulkPayload(year=2026, months=[9, 10], categoryId=categoria(self.session, "Casa"),
                                      amount=250.0, currency="CHF"), self.session)
        self.assertEqual([100.0], [float(riga.amount) for riga in self._piani("EUR")])
        self.assertEqual([250.0, 250.0], sorted(float(riga.amount) for riga in self._piani("CHF")))

    def test_lo_sforamento_si_misura_nella_sua_valuta(self) -> None:
        oggi = date.today()
        self._piano(oggi.month, "100.00", "CHF", anno=oggi.year)
        self._piano(oggi.month, "100.00", "EUR", anno=oggi.year)
        self._movimento(date(oggi.year, oggi.month, 2), "150.00", "CHF")
        self._movimento(date(oggi.year, oggi.month, 3), "50.00", "EUR")
        avvisi = _sforamenti_budget(self.session, oggi)
        # Solo il piano in franchi e' sforato: quello in euro spende meta' di
        # quanto prevede, e i centocinquanta franchi non lo riguardano.
        self.assertEqual(1, len(avvisi))
        self.assertTrue(avvisi[0]["key"].endswith(":CHF"), avvisi[0]["key"])
        self.assertEqual(150.0, avvisi[0]["params"]["spent"])

    def test_le_valute_offerte_sono_quelle_che_usa(self) -> None:
        # I due conti, e non un elenco di divise del mondo.
        self.assertEqual(["EUR", "CHF"], budget_currencies(self.session))
        # Un database appena nato non ha nemmeno un conto: l'euro c'e' lo
        # stesso, perche' e' la scheda da cui si parte e senza di lui la
        # pagina non avrebbe nessuna linguetta selezionata.
        vuoto = create_engine("sqlite://")
        Base.metadata.create_all(vuoto)
        self.assertEqual(["EUR"], budget_currencies(Session(vuoto)))

    def test_la_quadratura_e_di_una_valuta(self) -> None:
        self._piano(9, "3000.00", "EUR", categoria_nome="Stipendio", tipo="Income")
        self._piano(9, "1000.00", "EUR")
        self._piano(9, "500.00", "CHF", categoria_nome="Stipendio", tipo="Income")
        self.session.commit()
        # Un piano in franchi accanto a uno in euro non quadra niente: le due
        # schede hanno ciascuna la sua quadratura.
        self.assertEqual(2000.0, _budget_balance(self.session, 2026, 9, "EUR")["savings"])
        self.assertEqual(500.0, _budget_balance(self.session, 2026, 9, "CHF")["savings"])

    def test_le_pagine_in_euro_non_sommano_i_piani_in_franchi(self) -> None:
        self._piano(9, "100.00", "EUR")
        self._piano(9, "900.00", "CHF")
        self.session.commit()
        # La ripartizione bisogni/piaceri legge in euro: il piano in franchi non
        # e' un piano in euro, e sommarlo farebbe crescere di novecento una cifra
        # che nessuno ha toccato. I gruppi si sommano invece di cercarne uno per
        # nome: dove finisca "Casa" e' una domanda di un altro test.
        righe = _needs_wants(self.session, 2026, 9)
        self.assertEqual(100.0, sum(riga["planned"] for riga in righe))

    def test_il_pianificato_della_griglia_segue_la_scheda(self) -> None:
        self._piano(9, "100.00", "EUR")
        self._piano(10, "200.00", "CHF")
        self._piano(11, "50.00", "CHF")
        self.session.commit()
        casa = categoria(self.session, "Casa")
        per_valuta = {}
        for valuta in ("EUR", "CHF"):
            annuale = budget_annual(2026, "Expenses", self.session, valuta)
            per_valuta[valuta] = next(item["plannedTotal"] for item in annuale["items"]
                                      if item["categoryId"] == casa)
        self.assertEqual({"EUR": 100.0, "CHF": 250.0}, per_valuta)

    def test_il_dashboard_mensile_porta_la_valuta_giusta(self) -> None:
        self._piano(9, "100.00", "CHF")
        self._movimento(date(2026, 9, 5), "40.00", "CHF")
        self._movimento(date(2026, 9, 6), "400.00", "EUR")
        self.session.commit()
        franchi = budget_dashboard(2026, 9, "Expenses", self.session, "CHF")
        self.assertEqual((100.0, 40.0), (franchi["plannedTotal"], franchi["actualTotal"]))
        # La scheda in euro non ha piani, ma la spesa in euro non sparisce: sta
        # nella ripartizione, col pianificato a zero e l'effettivo no. E' il
        # posto in cui si scopre che quel budget manca.
        euro = budget_dashboard(2026, 9, "Expenses", self.session, "EUR")
        self.assertEqual(0.0, euro["plannedTotal"])
        self.assertEqual(400.0, sum(riga["actual"] for riga in euro["groups"]))

    def test_il_riporto_del_mese_prima_segue_la_scheda(self) -> None:
        self._piano(8, "400.00", "EUR")
        self._piano(8, "500.00", "CHF")
        self._movimento(date(2026, 8, 5), "300.00", "CHF")
        self._movimento(date(2026, 8, 6), "450.00", "EUR")
        self.session.commit()
        casa = categoria(self.session, "Casa")
        # Le due schede hanno ciascuna il suo riporto, e il franco non entra
        # nella riga dell'euro ne' viceversa.
        self.assertEqual({casa: -50.0}, previous_month_leftover(self.session, 2026, 9, "Expenses", "EUR"))
        self.assertEqual({casa: 200.0}, previous_month_leftover(self.session, 2026, 9, "Expenses", "CHF"))

    def test_i_totali_mensili_seguono_la_valuta(self) -> None:
        self._movimento(date(2026, 9, 6), "400.00", "EUR")
        self._movimento(date(2026, 9, 7), "100.00", "CHF")
        self.session.commit()
        self.assertEqual(400.0, _totali_mensili(self.session, 2026, "Expenses", "EUR")[9])
        self.assertEqual(100.0, _totali_mensili(self.session, 2026, "Expenses", "CHF")[9])

    def test_il_tasso_di_risparmio_segue_la_scheda(self) -> None:
        self._movimento(date(2026, 9, 10), "1000.00", "CHF", categoria_nome="Stipendio", tipo="Income")
        self._movimento(date(2026, 9, 11), "400.00", "CHF")
        self.session.commit()
        # Il rapporto della scheda in franchi si fa sui franchi: con dentro gli
        # euro di un'altra scheda sarebbe il rapporto di nessuno.
        franchi = calculations(2026, 9, self.session, "CHF")
        self.assertEqual((1000.0, 400.0, 0.6),
                         (franchi["income"], franchi["expenses"], franchi["savingsRate"]))


if __name__ == "__main__":
    unittest.main()
