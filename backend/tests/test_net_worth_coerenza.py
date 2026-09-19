"""Panoramica e pagina Patrimonio devono dire lo stesso numero.

Sono due strade diverse per lo stesso patrimonio: se divergono, una delle due
sta mentendo e non si sa quale. Due difetti trovati proprio cosi': i saldi
letti a oggi invece che alla data del periodo, e il debito sommato invece che
sottratto - invisibile finche' le passivita' erano a zero.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import _net_worth_breakdown, balance_sheet_series, net_worth, net_worth_series
from app.database import Base
from app.models import Account, Transaction
from tests.categorie_fixture import categoria


def _tx(session: Session, giorno: date, tipo: str, importo: str, conto: str,
        destinazione: str | None = None) -> Transaction:
    # La categoria e' una riga: la fixture la crea e passa l'id.
    return Transaction(occurred_on=giorno, effective_on=giorno, transaction_type=tipo,
                       category_id=categoria(session, "Varie"), amount=Decimal(importo), account_type="Bank",
                       account_name=conto, destination_name=destinazione, is_recurring_template=False)


class PanoramicaEPatrimonioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(name="Conto", source_group="bank",
                    starting_balance=Decimal("1000"), current_balance=Decimal("1000"), counts_in_net_worth=True),
            Account(name="Casa", source_group="asset",
                    starting_balance=Decimal("100000"), current_balance=Decimal("100000"), counts_in_net_worth=True, is_liquid=False),
            # Un debito: saldo negativo, come lo scrive l'app.
            Account(name="Mutuo", source_group="liability",
                    starting_balance=Decimal("-40000"), current_balance=Decimal("-40000"), counts_in_net_worth=True),
            _tx(self.session, date(2026, 7, 10), "Income", "500", "Conto"),
            # Agosto: se la Panoramica leggesse i saldi di oggi invece che quelli
            # di luglio, questo movimento finirebbe dentro il patrimonio di luglio.
            _tx(self.session, date(2026, 8, 15), "Expenses", "300", "Conto"),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_stesso_patrimonio_da_entrambe_le_strade(self) -> None:
        for mese in (7, 8):
            panoramica = _net_worth_breakdown(self.session, 2026, mese)["total"]
            patrimonio = net_worth(2026, mese, 12, self.session)["totals"]["netWorth"]
            self.assertAlmostEqual(panoramica, patrimonio, places=2, msg=f"mese {mese}")

    def test_il_debito_abbassa_il_patrimonio(self) -> None:
        # 1000 + 500 (luglio) + 100000 - 40000 = 61500
        self.assertAlmostEqual(_net_worth_breakdown(self.session, 2026, 7)["total"], 61500.0, places=2)

    def test_il_patrimonio_segue_il_mese_scelto(self) -> None:
        luglio = _net_worth_breakdown(self.session, 2026, 7)["total"]
        agosto = _net_worth_breakdown(self.session, 2026, 8)["total"]
        self.assertAlmostEqual(luglio - agosto, 300.0, places=2)

    def test_il_patrimonio_liquido_esclude_asset_ma_sottrae_passivita(self) -> None:
        # Conto 1.000 + entrata 500, casa non liquida 100.000, debito 40.000.
        self.assertAlmostEqual(_net_worth_breakdown(self.session, 2026, 7)["liquid"], -38500.0, places=2)

    def test_i_totali_includono_la_liquidita(self) -> None:
        # La serie mensile non viaggia piu' nella risposta - la disegnava un
        # grafico che non esiste piu' - ma la liquidita' del periodo chiesto
        # si controlla ancora, ed e' lo stesso numero di prima.
        totali = net_worth(2026, 7, 12, self.session)["totals"]
        self.assertAlmostEqual(totali["liquid"], -38500.0, places=2)

    def test_il_dettaglio_conti_mostra_un_saldo_per_conto(self) -> None:
        # Il saldo per conto ora e' il livello piu' profondo del grafico del
        # bilancio, non piu' un endpoint suo: il numero controllato qui non e'
        # cambiato, e' cambiato solo da dove lo si chiede.
        conto = self.session.scalar(select(Account).where(Account.name == "Conto"))
        serie = balance_sheet_series(da="2026-01", a="2026-12", grain="month", level="component",
                                     side="assets", component="liquidity", session=self.session)
        luglio = next(row for row in serie["series"] if row["period"] == "2026-07")
        self.assertAlmostEqual(luglio["values"][f"account_{conto.id}"], 1500.0, places=2)


if __name__ == '__main__':
    unittest.main()


class ScorporoInvestimentiTests(unittest.TestCase):
    """`financial` e' una riga della vista, non un pezzo in piu' di patrimonio.

    I conti investimento vengono scorporati da `asset` per mostrarli su una
    linea propria: `asset` esce gia' al netto e `financial` porta la stessa
    cifra. La pagina Patrimonio conta invece su `bank + asset - liability`
    leggendo i conti veri, dove gli investimenti stanno ancora dentro `asset`.
    Le due strade coincidono solo finche' lo scorporo e' a somma zero: se un
    giorno `financial` diventasse un valore a se', il client conterebbe gli
    investimenti una volta e il server due.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(name="Conto", source_group="bank",
                    starting_balance=Decimal("2000"), current_balance=Decimal("2000"), counts_in_net_worth=True),
            Account(name="Casa", source_group="asset",
                    starting_balance=Decimal("80000"), current_balance=Decimal("80000"),
                    counts_in_net_worth=True, is_liquid=False),
            Account(name="Mutuo", source_group="liability",
                    starting_balance=Decimal("-30000"), current_balance=Decimal("-30000"), counts_in_net_worth=True),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_il_patrimonio_e_la_somma_dei_quattro_totali(self) -> None:
        totali = net_worth(2026, 7, 12, self.session)["totals"]
        atteso = totali["bank"] + totali["asset"] + totali["financial"] - totali["liability"]
        self.assertAlmostEqual(totali["netWorth"], atteso, places=2)

    def test_lo_scorporo_non_crea_ne_perde_denaro(self) -> None:
        # La formula del client - banca piu' attivita' meno passivita', con gli
        # investimenti ancora dentro le attivita' - deve dare lo stesso numero.
        totali = net_worth(2026, 7, 12, self.session)["totals"]
        attivita_intere = totali["asset"] + totali["financial"]
        self.assertAlmostEqual(totali["netWorth"],
                               totali["bank"] + attivita_intere - totali["liability"], places=2)

    def test_la_risposta_non_porta_piu_il_dettaglio_per_conto(self) -> None:
        # Era il 36% del corpo e non lo leggeva nessuno: l'elenco dei conti
        # viene dai conti veri, che portano anche nome, gruppo e pulsanti.
        self.assertNotIn("breakdown", net_worth(2026, 7, 12, self.session))
