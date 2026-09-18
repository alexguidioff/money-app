"""Gli split azionari nel ledger.

Dopo un frazionamento le quote cambiano e il costo no: chi le corregge a mano
sbaglia il prezzo medio, e da li' in poi valore e rendimento mentono. La riga
porta il rapporto nelle quote (2 = due nuove per una vecchia, 0,1 = un
raggruppamento) e zero nell'importo: uno split non si paga.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.calculation_engine import investment_positions
from app.core_routes import investments_ledger
from app.database import Base
from app.models import InvestmentTransaction, InvestmentTransactionDetail


def _riga(id: int, giorno: int, tipo: str, *, nome: str = "ETF", importo: str = "0",
          quote: str | None = None, prezzo: str | None = None) -> dict:
    return {"id": id, "occurred_on": date(2026, giorno, 1), "name": nome, "ticker": None,
            "transaction_type": tipo, "amount": Decimal(importo),
            "units": Decimal(quote) if quote is not None else None,
            "price": Decimal(prezzo) if prezzo is not None else None, "currency": "EUR"}


def _posizione(righe: list[dict], nome: str = "ETF") -> dict:
    return next(posizione for posizione in investment_positions(righe) if posizione["name"] == nome)


def _prezzo_medio(posizione: dict) -> Decimal:
    return (posizione["cost_basis"] / posizione["units"]).quantize(Decimal("0.01"))


class SplitTests(unittest.TestCase):
    def test_split_due_a_uno_raddoppia_le_quote_e_lascia_il_costo(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100", prezzo="10"),
            _riga(2, 5, "Split", quote="2"),
        ])
        self.assertEqual(Decimal("200.00000000"), posizione["units"])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])
        # Il prezzo medio e' costo diviso quote: si e' dimezzato da solo.
        self.assertEqual(Decimal("5.00"), _prezzo_medio(posizione))

    def test_raggruppamento_uno_a_dieci(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100", prezzo="10"),
            _riga(2, 5, "Split", quote="0.1"),
        ])
        self.assertEqual(Decimal("10.00000000"), posizione["units"])
        self.assertEqual(Decimal("1000.00"), posizione["cost_basis"])
        self.assertEqual(Decimal("100.00"), _prezzo_medio(posizione))

    def test_lo_split_moltiplica_le_quote_di_prima_e_non_quelle_di_dopo(self) -> None:
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100", prezzo="10"),
            _riga(2, 5, "Split", quote="2"),
            _riga(3, 10, "Buy", importo="2000", quote="100", prezzo="20"),
        ])
        self.assertEqual(Decimal("300.00000000"), posizione["units"])
        self.assertEqual(Decimal("3000.00"), posizione["cost_basis"])

    def test_l_ordine_delle_righe_non_cambia_il_risultato(self) -> None:
        # Il database non garantisce l'ordine: se lo split finisse prima
        # dell'acquisto che lo precede, moltiplicherebbe zero quote e la
        # posizione resterebbe sbagliata senza che si veda niente.
        ordinato = [
            _riga(1, 1, "Buy", importo="1000", quote="100", prezzo="10"),
            _riga(2, 5, "Split", quote="2"),
            _riga(3, 10, "Buy", importo="2000", quote="100", prezzo="20"),
        ]
        sparso = _posizione([ordinato[2], ordinato[0], ordinato[1]])
        self.assertEqual(Decimal("300.00000000"), sparso["units"])
        self.assertEqual(Decimal("3000.00"), sparso["cost_basis"])

    def test_uno_split_senza_quote_non_cambia_niente(self) -> None:
        # La riga senza rapporto non si applica: moltiplicare per zero
        # azzererebbe la posizione.
        posizione = _posizione([
            _riga(1, 1, "Buy", importo="1000", quote="100", prezzo="10"),
            _riga(2, 5, "Split"),
        ])
        self.assertEqual(Decimal("100.00000000"), posizione["units"])


class ValidazioneSplitTests(unittest.TestCase):
    """Il rapporto si valida in scrittura: una riga accettata e poi scartata
    dal motore farebbe credere di aver corretto una posizione ancora sbagliata."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[InvestmentTransaction.__table__,
                                                      InvestmentTransactionDetail.__table__])
        self.session = Session(self.engine)
        self.session.add(InvestmentTransaction(name="ETF", transaction_type="Buy", amount=Decimal("1000"),
                                               units=Decimal("100"), price=Decimal("10"),
                                               occurred_on=date(2026, 1, 1)))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _payload(self, **campi) -> main.InvestmentTxPayload:
        dati = {"name": "ETF", "transaction_type": "Split", "amount": 0,
                "occurred_on": "2026-06-01", "units": 2}
        return main.InvestmentTxPayload(**{**dati, **campi})

    def _rifiuta(self, **campi) -> str:
        with self.assertRaises(HTTPException) as errore:
            main.create_investment_tx(self._payload(**campi), session=self.session)
        self.assertEqual(422, errore.exception.status_code)
        return errore.exception.detail["code"]

    def test_lo_split_valido_si_salva(self) -> None:
        creato = main.create_investment_tx(self._payload(), session=self.session)
        self.assertEqual("Split", creato["transactionType"])

    def test_rapporto_fuori_scala_non_si_salva(self) -> None:
        for rapporto in (0, -2, 1000):
            with self.subTest(rapporto=rapporto):
                self.assertEqual("ledgerSplitRatioNonValido", self._rifiuta(units=rapporto))

    def test_uno_split_non_muove_denaro(self) -> None:
        self.assertEqual("ledgerSplitAmountNonZero", self._rifiuta(amount=25))

    def test_uno_split_su_uno_strumento_mai_comprato_non_si_salva(self) -> None:
        self.assertEqual("ledgerSplitStrumentoInesistente", self._rifiuta(name="Mai comprato"))


class QuoteProgressiveTests(unittest.TestCase):
    """Le quote progressive del ledger sono quelle che l'utente legge riga per
    riga: uno split le moltiplica invece di sottrarre il rapporto."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_uno_split_moltiplica_le_quote_progressive(self) -> None:
        self.session.add_all([
            InvestmentTransaction(name="ETF", transaction_type="Buy", amount=Decimal("1000"),
                                  units=Decimal("100"), price=Decimal("10"),
                                  occurred_on=date(2026, 1, 10)),
            InvestmentTransaction(name="ETF", transaction_type="Split", amount=Decimal("0"),
                                  units=Decimal("2"), occurred_on=date(2026, 2, 10)),
            InvestmentTransaction(name="ETF", transaction_type="Buy", amount=Decimal("500"),
                                  units=Decimal("50"), price=Decimal("10"),
                                  occurred_on=date(2026, 3, 10)),
        ])
        self.session.commit()
        progressive = {riga["occurredOn"]: riga["runningUnits"]
                       for riga in investments_ledger(self.session)["items"]}
        self.assertEqual({"2026-01-10": 100.0, "2026-02-10": 200.0, "2026-03-10": 250.0}, progressive)


if __name__ == "__main__":
    unittest.main()
