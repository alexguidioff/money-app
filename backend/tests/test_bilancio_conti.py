"""Il bilancio della pagina Conti deve quadrare: attivo = passivo + capitale proprio.

Il capitale proprio non e' un gruppo di conti da compilare, e' un residuo. Se le
due colonne non danno lo stesso numero la pagina sta mentendo, ed e' esattamente
quello che faceva prima: gli investimenti stavano fra i finanziatori invece che
fra le attivita', e mancavano centocinquantamila euro da una parte.

L'altra meta' e' il valore del portafoglio: il conto 'financial' vale quanto ci
hai versato (il costo), non quanto vale oggi. Quel numero lo sa solo il ledger
investimenti, e senza chiederglielo la pagina Conti e la pagina Patrimonio
raccontano due patrimoni diversi.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.calculation_engine import account_balances_at
from app.core_routes import accounts as endpoint_conti
from app.core_routes import (movimenti_per_saldi, rivalutazioni_per_conto,
                             valutazioni_per_conto)
from app.database import Base
from app.models import (Account, AccountValuation, InvestmentInstrument, InvestmentTransaction,
                        LiabilityTransactionDetail, MarketPrice, Transaction, TransactionLedgerLink)

TABELLE = [Account.__table__, AccountValuation.__table__, Transaction.__table__,
           LiabilityTransactionDetail.__table__,
           InvestmentInstrument.__table__, InvestmentTransaction.__table__,
           MarketPrice.__table__, TransactionLedgerLink.__table__]


class BilancioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _conto(self, gruppo: str, nome: str, iniziale: str = "0", patrimonio: bool = True) -> Account:
        riga = Account(source_group=gruppo, name=nome, starting_balance=Decimal(iniziale),
                       current_balance=Decimal(iniziale), counts_in_net_worth=patrimonio)
        self.session.add(riga)
        self.session.flush()
        return riga

    def _broker(self, nome: str) -> None:
        riga = self.session.scalars(select(Account).where(Account.name == nome)).one()
        riga.is_broker = True
        self.session.flush()

    def _collega(self, conto: str) -> None:
        """L'unico modo in cui un conto diventa "di mercato": un suo movimento
        risulta collegato a un'operazione del ledger."""
        movimento = Transaction(occurred_on=date(2024, 1, 5), effective_on=date(2024, 1, 5),
                                transaction_type="Investment", category="", amount=Decimal("0"),
                                account_name="Conto", destination_name=conto)
        self._broker(conto)
        self.session.add(movimento)
        self.session.flush()
        operazione = self.session.scalars(select(InvestmentTransaction)).first()
        self.session.add(TransactionLedgerLink(transaction_id=movimento.id, ledger_id=operazione.id))
        self.session.commit()

    def _totali(self) -> dict[str, float]:
        """Gli stessi conti che fa la pagina, con le stesse esclusioni."""
        risposta = endpoint_conti(at=None, session=self.session)
        self.risposta = risposta

        # `value` e' quanto vale il conto oggi: lo stesso numero che usa la
        # pagina Patrimonio, valutazioni applicate e debiti positivi.
        def somma(gruppo: str) -> float:
            return sum(voce["value"] for voce in risposta["items"]
                       if voce["group"] == gruppo and voce["countsInNetWorth"] is not False)

        # Il versato viene dai conti, non da una seconda fonte: e' la somma dei
        # loro costi. Il ledger direbbe lo stesso numero, ma due fonti per la
        # stessa cifra prima o poi divergono - e infatti divergevano di un
        # centesimo.
        investimenti = sum(voce["value"] for voce in risposta["items"]
                           if voce["valuedByLedger"] and voce["countsInNetWorth"] is not False)
        versati = sum(voce["calculatedBalance"] for voce in risposta["items"]
                      if voce["valuedByLedger"] and voce["countsInNetWorth"] is not False)
        attivo = somma("bank") + somma("asset")
        passivo = somma("liability")
        return {"attivo": round(attivo, 2), "passivo": round(passivo, 2),
                "capitale": round(attivo - passivo, 2), "versati": round(versati, 2),
                "rivalutazione": round(investimenti - versati, 2),
                "resto": round(attivo - investimenti - passivo, 2)}

    def _assert_scomposizione_torna(self, totali: dict[str, float]) -> None:
        # Le tre voci della card devono ridare il totale stampato sopra: se
        # avanza un pezzo, la scomposizione sta nascondendo qualcosa.
        self.assertAlmostEqual(totali["capitale"],
                               totali["versati"] + totali["rivalutazione"] + totali["resto"], places=2)

    def test_quadra_senza_investimenti(self) -> None:
        self._conto("bank", "Conto", "1000")
        self._conto("liability", "Carta", "-300")
        self.session.commit()
        totali = self._totali()
        self.assertEqual(1000.0, totali["attivo"])
        self.assertEqual(300.0, totali["passivo"])   # positivo, come nel patrimonio
        self.assertEqual(700.0, totali["capitale"])
        self._assert_scomposizione_torna(totali)

    def test_gli_accantonamenti_restano_fuori_dai_totali(self) -> None:
        self._conto("bank", "Conto", "1000")
        self._conto("asset", "Da investire", "400", patrimonio=False)
        self.session.commit()
        # Un accantonamento segna dove sono destinati soldi che stanno gia'
        # altrove: contarlo li conterebbe due volte.
        self.assertEqual(1000.0, self._totali()["capitale"])

    def test_il_portafoglio_vale_il_mercato_non_il_versato(self) -> None:
        self._conto("bank", "Conto", "500")
        self._conto("asset", "Soldi investiti", "1000")
        self.session.add(InvestmentInstrument(name="Titolo", provider_symbol="TST"))
        self.session.add(InvestmentTransaction(name="Titolo", transaction_type="Buy",
                                               occurred_on=date(2024, 1, 5), units=Decimal("10"),
                                               amount=Decimal("1000"), price=Decimal("100")))
        self.session.add(MarketPrice(symbol="TST", observed_on=date(2024, 1, 31),
                                     price=Decimal("150"), currency="EUR"))
        self.session.commit()
        self._collega("Soldi investiti")
        totali = self._totali()
        # Dieci quote a 150: il conto ne dichiara 1000 di costo, il mercato 1500.
        self.assertEqual(1000.0, totali["versati"])
        self.assertEqual(500.0, totali["rivalutazione"])
        self.assertEqual(2000.0, totali["attivo"])   # 500 di banca + 1500 di mercato
        self.assertEqual(500.0, totali["rivalutazione"])
        self._assert_scomposizione_torna(totali)

    def test_niente_investimenti_senza_conti_collegati(self) -> None:
        self._conto("bank", "Conto", "100")
        self.session.commit()
        totali = self._totali()
        # Chi non investe non deve vedersi comparire una rivalutazione a zero.
        self.assertEqual(0.0, totali["versati"])
        self.assertEqual(0.0, totali["rivalutazione"])
        self.assertEqual(totali["capitale"], totali["resto"])

    def test_il_capitale_proprio_e_il_patrimonio_netto(self) -> None:
        """Due strade diverse per lo stesso numero: se divergono, una mente."""
        self._conto("bank", "Conto", "2500")
        self._conto("asset", "Casa", "100000")
        self._conto("liability", "Mutuo", "-80000")
        self.session.add(Transaction(occurred_on=date(2024, 3, 1), effective_on=date(2024, 3, 1), transaction_type="Expenses",
                                     category="Casa", amount=Decimal("500"), account_name="Conto"))
        self.session.commit()
        conti = [c for c in self.session.scalars(select(Account)).all() if c.counts_in_net_worth]
        saldi = account_balances_at(conti, movimenti_per_saldi(self.session), date.today())
        patrimonio = round(saldi["totals"]["bank"] + saldi["totals"]["asset"]
                           - saldi["totals"]["liability"], 2)
        self.assertEqual(patrimonio, self._totali()["capitale"])

    def test_conti_e_patrimonio_dicono_lo_stesso_numero(self) -> None:
        """La pagina Conti e la pagina Patrimonio non devono poter divergere.

        Erano due calcoli diversi della stessa cosa, e potevano allontanarsi per
        quattro motivi: la data, le stime scritte a mano, il mercato degli
        investimenti e il segno dei debiti. Qui si mettono in campo tutti e
        quattro insieme e si pretende lo stesso numero, conto per conto.
        """
        self._conto("bank", "Conto", "2500")
        casa = self._conto("asset", "Casa", "100000")
        casa.needs_manual_valuation = True
        self._conto("asset", "Titoli", "1000")
        self._conto("liability", "Mutuo", "-80000")
        self._conto("asset", "Accantonato", "700", patrimonio=False)
        self.session.add(AccountValuation(account_id=casa.id, observed_on=date(2024, 1, 1),
                                          value=Decimal("130000")))
        self.session.add(InvestmentInstrument(name="Titolo", provider_symbol="TST"))
        self.session.add(InvestmentTransaction(name="Titolo", transaction_type="Buy",
                                               occurred_on=date(2024, 1, 5), units=Decimal("10"),
                                               amount=Decimal("1000"), price=Decimal("100")))
        self.session.add(MarketPrice(symbol="TST", observed_on=date(2024, 1, 31),
                                     price=Decimal("150"), currency="EUR"))
        self.session.commit()
        self._collega("Titoli")

        conti = self.session.scalars(select(Account)).all()
        atteso = account_balances_at(conti, movimenti_per_saldi(self.session), date.today(),
                                     valutazioni_per_conto(self.session),
                                     rivalutazioni_per_conto(self.session))
        per_id = {riga["account_id"]: riga["balance"] for riga in atteso["accounts"]}
        for voce in endpoint_conti(at=None, session=self.session)["items"]:
            self.assertAlmostEqual(per_id[voce["id"]], voce["value"], places=2, msg=voce["name"])

        # E i tre casi speciali devono essere davvero speciali, altrimenti il
        # test passerebbe anche con due formule sbagliate nello stesso modo.
        per_nome = {voce["name"]: voce for voce in endpoint_conti(at=None, session=self.session)["items"]}
        self.assertEqual(130000.0, per_nome["Casa"]["value"])      # la stima, non i bonifici
        self.assertEqual(1500.0, per_nome["Titoli"]["value"])      # il mercato, non il costo
        self.assertEqual(1000.0, per_nome["Titoli"]["calculatedBalance"])
        self.assertEqual(80000.0, per_nome["Mutuo"]["value"])      # positivo, da sottrarre

    def test_i_saldi_seguono_la_data_chiesta(self) -> None:
        """La pagina unica ha un selettore di periodo: l'elenco dei conti deve
        dire quello che diceva allora, non quello che dice oggi.

        E deve dire la STESSA cosa della serie del patrimonio a quella data,
        altrimenti le due meta' della stessa pagina si contraddicono."""
        self._conto("bank", "Conto", "1000")
        self.session.add(Transaction(occurred_on=date(2024, 6, 1), effective_on=date(2024, 6, 1),
                                     transaction_type="Expenses", category="Casa",
                                     amount=Decimal("400"), account_name="Conto"))
        self.session.commit()

        prima = {v["name"]: v["value"] for v in endpoint_conti(at="2024-05-31", session=self.session)["items"]}
        dopo = {v["name"]: v["value"] for v in endpoint_conti(at="2024-07-31", session=self.session)["items"]}
        self.assertEqual(1000.0, prima["Conto"])
        self.assertEqual(600.0, dopo["Conto"])

        conti = self.session.scalars(select(Account)).all()
        atteso = account_balances_at(conti, movimenti_per_saldi(self.session), date(2024, 5, 31),
                                     valutazioni_per_conto(self.session),
                                     rivalutazioni_per_conto(self.session))
        per_id = {r["account_id"]: r["balance"] for r in atteso["accounts"]}
        for voce in endpoint_conti(at="2024-05-31", session=self.session)["items"]:
            self.assertAlmostEqual(per_id[voce["id"]], voce["value"], places=2, msg=voce["name"])

    def test_la_riconciliazione_non_segue_la_data(self) -> None:
        # Confronta il dichiarato con TUTTI i movimenti: non ha una data, e
        # farla seguire il periodo la riempirebbe di differenze inventate.
        self._conto("bank", "Conto", "1000")
        self.session.add(Transaction(occurred_on=date(2024, 6, 1), effective_on=date(2024, 6, 1),
                                     transaction_type="Expenses", category="Casa",
                                     amount=Decimal("400"), account_name="Conto"))
        self.session.commit()
        for quando in ("2024-05-31", "2024-07-31"):
            voce = next(v for v in endpoint_conti(at=quando, session=self.session)["items"]
                        if v["name"] == "Conto")
            self.assertEqual(600.0, voce["calculatedBalance"], msg=quando)


if __name__ == "__main__":
    unittest.main()
