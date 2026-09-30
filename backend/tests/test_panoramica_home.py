"""I due numeri della Panoramica che guardano a oggi invece che al periodo.

L'ultimo uso di ogni conto e le uscite degli ultimi giorni non dipendono dal
mese scelto in alto: si calcolano a parte, e sbagliarli non fa cadere niente -
la card mostra una cifra plausibile e sbagliata, che e' il modo peggiore. Qui
si presidiano le tre regole che li tengono in piedi: un conto si usa anche
quando il denaro ci arriva, un rimborso rientra nel giorno in cui rientra, e un
acquisto di ETF si conta una volta sola.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import GIORNI_SPESA_RECENTE, _spese_ultimi_giorni, _ultimo_utilizzo
from app.database import Base
from app.models import (Account, InvestmentTransaction, Transaction,
                        TransactionLedgerLink)
from tests.categorie_fixture import categoria

OGGI = date.today()


def _giorni_fa(giorni: int) -> date:
    """Un giorno dentro la finestra: 0 e' oggi, 6 e' il piu' vecchio che si vede."""
    return OGGI - timedelta(days=giorni)


def _tx(session: Session, day: date, tx_type: str, amount: str, *,
        account: str = "Conto", destinazione: str | None = None,
        template: bool = False, in_budget: bool = True,
        rimborso_di: int | None = None) -> Transaction:
    riga = Transaction(occurred_on=day, effective_on=day, transaction_type=tx_type,
                       category_id=None if tx_type != "Expenses" else categoria(session, "Groceries"),
                       amount=Decimal(amount), account_type="Bank", account_name=account,
                       destination_name=destinazione, is_recurring_template=template,
                       counts_in_budget=in_budget, refund_of_id=rimborso_di)
    session.add(riga)
    session.flush()
    return riga


class SpeseDegliUltimiGiorniTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)

    def per_giorno(self) -> dict[str, float]:
        return {riga["date"]: riga["amount"] for riga in _spese_ultimi_giorni(self.session)}

    def test_la_finestra_e_di_sette_giorni_e_finisce_oggi(self) -> None:
        righe = _spese_ultimi_giorni(self.session)
        self.assertEqual(len(righe), GIORNI_SPESA_RECENTE)
        self.assertEqual(righe[-1]["date"], OGGI.isoformat())
        self.assertEqual(righe[0]["date"], _giorni_fa(GIORNI_SPESA_RECENTE - 1).isoformat())

    def test_una_spesa_pesa_nel_giorno_in_cui_e_uscita(self) -> None:
        _tx(self.session, _giorni_fa(2), "Expenses", "40.00")
        _tx(self.session, _giorni_fa(1), "Expenses", "12.50")
        # Fuori finestra: non compare, e non sposta niente.
        _tx(self.session, _giorni_fa(GIORNI_SPESA_RECENTE), "Expenses", "999.00")
        # Un template descrive cosa succedera': non e' denaro uscito.
        _tx(self.session, _giorni_fa(3), "Expenses", "77.00", template=True)
        self.session.commit()
        per_giorno = self.per_giorno()
        self.assertEqual(per_giorno[_giorni_fa(2).isoformat()], 40.00)
        self.assertEqual(per_giorno[_giorni_fa(1).isoformat()], 12.50)
        self.assertEqual(per_giorno[_giorni_fa(3).isoformat()], 0.0)
        # Il settimo giorno indietro e' fuori: la finestra e' di sette giorni
        # compreso oggi, e il primo e' quello di sei giorni fa.
        self.assertNotIn(_giorni_fa(GIORNI_SPESA_RECENTE).isoformat(), per_giorno)

    def test_un_rimborso_rientra_nel_giorno_in_cui_rientra(self) -> None:
        originale = _tx(self.session, _giorni_fa(4), "Expenses", "200.00")
        # Non pesa sul budget (e' un rimborso), ma i soldi sono rientrati.
        _tx(self.session, _giorni_fa(2), "Expenses", "200.00", in_budget=False, rimborso_di=originale.id)
        self.session.commit()
        per_giorno = self.per_giorno()
        self.assertEqual(per_giorno[_giorni_fa(4).isoformat()], 200.00)
        self.assertEqual(per_giorno[_giorni_fa(2).isoformat()], -200.00)

    def test_un_acquisto_di_etf_conta_come_uscita(self) -> None:
        self.session.add(InvestmentTransaction(occurred_on=_giorni_fa(1), name="ETF World",
                                               transaction_type="Buy", amount=Decimal("300.00"),
                                               units=Decimal("3"), currency="EUR"))
        self.session.commit()
        self.assertEqual(self.per_giorno()[_giorni_fa(1).isoformat()], 300.00)

    def test_un_acquisto_gia_collegato_non_si_conta_due_volte(self) -> None:
        """Il movimento di banca e' gia' nella somma: contare anche la riga di
        portafoglio farebbe uscire una cifra che non c'e'."""
        banca = _tx(self.session, _giorni_fa(1), "Expenses", "300.00", account="Broker",
                    destinazione=None)
        acquisto = InvestmentTransaction(occurred_on=_giorni_fa(1), name="ETF World",
                                         transaction_type="Buy", amount=Decimal("300.00"),
                                         units=Decimal("3"), currency="EUR")
        self.session.add(acquisto)
        self.session.flush()
        self.session.add(TransactionLedgerLink(transaction_id=banca.id, ledger_id=acquisto.id))
        self.session.commit()
        self.assertEqual(self.per_giorno()[_giorni_fa(1).isoformat()], 300.00)

    def test_una_vendita_non_e_un_uscita(self) -> None:
        self.session.add(InvestmentTransaction(occurred_on=_giorni_fa(1), name="ETF World",
                                               transaction_type="Sell", amount=Decimal("300.00"),
                                               units=Decimal("3"), currency="EUR"))
        self.session.commit()
        self.assertEqual(self.per_giorno()[_giorni_fa(1).isoformat()], 0.0)


class UltimoUtilizzoDeiContiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)

    def test_vince_il_giorno_piu_recente(self) -> None:
        _tx(self.session, date(2026, 3, 2), "Expenses", "10.00", account="Conto")
        _tx(self.session, date(2026, 3, 9), "Expenses", "10.00", account="Conto")
        self.session.commit()
        self.assertEqual(_ultimo_utilizzo(self.session), {"Conto": "2026-03-09"})

    def test_un_conto_si_usa_anche_quando_il_denaro_ci_arriva(self) -> None:
        """Un versamento sul conto titoli e' un uso del conto titoli: guardando
        solo la partenza, quel conto risulterebbe fermo da mesi."""
        _tx(self.session, date(2026, 3, 2), "Investment", "500.00", account="Banca",
            destinazione="Broker")
        self.session.commit()
        ultimi = _ultimo_utilizzo(self.session)
        self.assertEqual(ultimi["Banca"], "2026-03-02")
        self.assertEqual(ultimi["Broker"], "2026-03-02")

    def test_un_template_non_e_un_uso(self) -> None:
        _tx(self.session, date(2026, 3, 2), "Expenses", "10.00", account="Conto")
        _tx(self.session, date(2026, 4, 1), "Expenses", "10.00", account="Conto", template=True)
        self.session.commit()
        self.assertEqual(_ultimo_utilizzo(self.session), {"Conto": "2026-03-02"})

    def test_un_conto_mai_usato_non_compare(self) -> None:
        self.session.add(Account(name="Nuovo", source_group="bank"))
        self.session.commit()
        self.assertEqual(_ultimo_utilizzo(self.session), {})


if __name__ == "__main__":
    unittest.main()
