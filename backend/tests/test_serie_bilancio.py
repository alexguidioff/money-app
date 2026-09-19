"""Il grafico del bilancio a profondita' variabile.

La proprieta' che tiene in piedi tutto e' una sola: a ogni livello la somma
delle voci deve fare il totale del livello sopra, in ogni singolo mese. Se si
rompe, scendere di un livello fa comparire o sparire soldi - e sembra una
scoperta invece di un errore, che e' il modo peggiore di sbagliare.

I due modi concreti in cui si romperebbe, entrambi coperti qui:
- gli investimenti sono dentro le attivita' (giusto) e hanno anche una voce
  loro: se la voce "altre attivita'" non li sottrae, il lato attivo raddoppia;
- gli accantonamenti (`counts_in_net_worth = false`) vanno esclusi a ogni
  livello, non solo in cima.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import balance_sheet_series, portfolio_timeline
from app.database import Base
from app.models import (Account, AccountValuation, InvestmentInstrument, InvestmentTransaction,
                        LiabilityTransactionDetail, MarketPrice, Transaction, TransactionLedgerLink)

TABELLE = [Account.__table__, AccountValuation.__table__, Transaction.__table__,
           LiabilityTransactionDetail.__table__,
           InvestmentInstrument.__table__, InvestmentTransaction.__table__,
           MarketPrice.__table__, TransactionLedgerLink.__table__]


class SerieBilancioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=TABELLE)
        self.session = Session(self.engine)
        self._conto("bank", "Conto", "2500")
        self._conto("bank", "Vuoto", "0")
        self._conto("asset", "Casa", "100000")
        self._conto("asset", "Titoli", "0")
        self._conto("liability", "Mutuo", "-80000")
        self._conto("asset", "Accantonato", "5000", patrimonio=False)
        self.session.add(InvestmentInstrument(name="Titolo", provider_symbol="TST"))
        self.session.add(InvestmentTransaction(name="Titolo", transaction_type="Buy",
                                               occurred_on=date(2024, 1, 5), units=Decimal("10"),
                                               amount=Decimal("1000"), price=Decimal("100")))
        self.session.add(MarketPrice(symbol="TST", observed_on=date(2024, 2, 28),
                                     price=Decimal("150"), currency="EUR"))
        self.session.flush()
        # "Titoli" diventa un conto di mercato solo qui: perche' un suo
        # movimento risulta collegato a un'operazione del ledger, non perche'
        # qualcuno gliel'ha scritto addosso alla nascita.
        self._collega("Titoli")
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _conto(self, gruppo: str, nome: str, iniziale: str, patrimonio: bool = True) -> None:
        self.session.add(Account(source_group=gruppo, name=nome, starting_balance=Decimal(iniziale),
                                 current_balance=Decimal(iniziale), counts_in_net_worth=patrimonio))

    def _broker(self, nome: str) -> None:
        conto = self.session.scalars(select(Account).where(Account.name == nome)).one()
        conto.is_broker = True
        self.session.flush()

    def _collega(self, conto: str) -> None:
        """Rende un conto "di mercato" nell'unico modo in cui lo si diventa:
        collegando un suo movimento a un'operazione del ledger. Il bonifico
        parte da una banca e arriva sul conto titoli, come nella realta'."""
        self._broker(conto)
        # PIANO-B3: un versamento sul conto titoli non ha categoria - la
        # vecchia stringa vuota adesso e' un id vuoto.
        movimento = Transaction(occurred_on=date(2024, 1, 5), effective_on=date(2024, 1, 5),
                                transaction_type="Investment", category_id=None, amount=Decimal("1000"),
                                account_name="Conto", destination_name=conto)
        self.session.add(movimento)
        self.session.flush()
        operazione = self.session.scalars(select(InvestmentTransaction)).first()
        self.session.add(TransactionLedgerLink(transaction_id=movimento.id, ledger_id=operazione.id))

    def _serie(self, **campi):
        base = {"da": "2024-01", "a": "2024-03", "grain": "month", "level": "networth",
                "side": None, "component": None, "session": self.session}
        return balance_sheet_series(**{**base, **campi})

    def test_le_voci_sommano_al_totale_a_ogni_livello(self) -> None:
        for risposta in (self._serie(),
                         self._serie(level="side", side="assets"),
                         self._serie(level="side", side="liabilities"),
                         self._serie(level="component", side="assets", component="liquidity")):
            for punto in risposta["series"]:
                self.assertAlmostEqual(punto["total"], sum(punto["values"].values()), places=2,
                                       msg=f"{risposta['level']} {punto['period']}")

    def test_scendere_non_cambia_il_totale(self) -> None:
        """Il livello 1 deve fare esattamente il livello 0, mese per mese."""
        alto = {p["period"]: p["values"]["assets"] for p in self._serie()["series"]}
        basso = self._serie(level="side", side="assets")["series"]
        for punto in basso:
            self.assertAlmostEqual(alto[punto["period"]], punto["total"], places=2, msg=punto["period"])

    def test_gli_investimenti_non_si_contano_due_volte(self) -> None:
        """Il caso che romperebbe tutto: sono un'attivita' E una voce a se'."""
        punto = self._serie(level="side", side="assets")["series"][-1]
        # 10 quote comprate a 1000 e quotate 1500: la voce investimenti vale il
        # mercato, e "altre attivita'" e' solo la casa - non la casa piu' i titoli.
        self.assertEqual(1500.0, punto["values"]["investments"])
        self.assertEqual(100000.0, punto["values"]["other"])
        # 2500 meno i 1000 bonificati sul conto titoli.
        self.assertEqual(1500.0, punto["values"]["liquidity"])

    def test_gli_accantonamenti_restano_fuori_scendendo(self) -> None:
        # 5000 su un conto fuori dal patrimonio: non devono comparire ne' in
        # cima ne' aprendo la voce che li conterrebbe.
        alto = self._serie()["series"][-1]["values"]["assets"]
        self.assertEqual(1500.0 + 1500.0 + 100000.0, alto)
        voci = self._serie(level="side", side="assets")["series"][-1]["values"]
        self.assertNotIn(5000.0, voci.values())
        conti = self._serie(level="component", side="assets", component="other")
        self.assertEqual(["Casa"], [k["label"] for k in conti["keys"]])

    def test_i_debiti_vanno_sotto_lo_zero(self) -> None:
        punto = self._serie()["series"][-1]
        # Il motore dei saldi li da' positivi; il grafico li disegna sotto.
        self.assertEqual(-80000.0, punto["values"]["liabilities"])
        self.assertAlmostEqual(23000.0, punto["total"], places=2)

    def test_le_linee_piatte_a_zero_non_si_disegnano(self) -> None:
        risposta = self._serie(level="component", side="assets", component="liquidity")
        self.assertEqual(["Conto"], [k["label"] for k in risposta["keys"]])
        self.assertEqual(1, risposta["hidden"])

    def test_gli_strumenti_mostrano_costo_e_mercato(self) -> None:
        punti = self._serie(level="instruments")["series"]
        self.assertEqual({"cost": 1000.0, "market": 1000.0, "gain": 0.0}, punti[0]["values"])
        # A febbraio arriva la quotazione: la distanza fra le due curve e' la
        # rivalutazione, che e' l'unica cosa che questo livello ha da dire.
        self.assertEqual({"cost": 1000.0, "market": 1500.0, "gain": 500.0}, punti[-1]["values"])

    def test_il_trimestre_raggruppa(self) -> None:
        mesi = self._serie(da="2024-01", a="2024-06")["series"]
        trimestri = self._serie(da="2024-01", a="2024-06", grain="quarter")["series"]
        self.assertEqual(6, len(mesi))
        self.assertEqual(2, len(trimestri))
        # Un trimestre vale quanto il suo ultimo mese: e' un saldo, non una somma.
        self.assertEqual(mesi[2]["total"], trimestri[0]["total"])
        self.assertEqual(mesi[5]["total"], trimestri[1]["total"])

    def test_periodo_e_livello_incoerenti_vengono_rifiutati(self) -> None:
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._serie(da="2024-06", a="2024-01")
        with self.assertRaises(HTTPException):
            self._serie(level="side")
        with self.assertRaises(HTTPException):
            self._serie(level="component", side="assets")

    def test_un_secondo_conto_non_regala_il_portafoglio_del_primo(self) -> None:
        """La domanda che ha fatto nascere questo test: se apro un conto nuovo e
        gli collego due operazioni, il patrimonio cresce di tutta la
        rivalutazione un'altra volta?

        No. Un conto vale il suo costo piu' il guadagno delle SUE operazioni.
        Collegarne una a un conto nuovo non gli da' una fetta del portafoglio di
        un altro: gli da' esattamente quella.
        """
        prima = self._serie(level="side", side="assets")["series"][-1]["values"]["investments"]
        self._conto("asset", "Titoli 2", "0")
        self.session.flush()
        # Il conto nuovo e' un broker: e' il ruolo che permette a un Investment
        # di puntarci, ed e' l'unico modo perche' il ledger gli attribuisca
        # qualcosa. Marcarlo non gli da' nessun valore da solo.
        self._broker("Titoli 2")
        self.session.add(InvestmentTransaction(name="Secondo", transaction_type="Buy",
                                               occurred_on=date(2024, 1, 6), units=Decimal("2"),
                                               amount=Decimal("200"), price=Decimal("100")))
        self.session.add(InvestmentInstrument(name="Secondo", provider_symbol="SEC"))
        self.session.add(MarketPrice(symbol="SEC", observed_on=date(2024, 2, 28),
                                     price=Decimal("120"), currency="EUR"))
        self.session.flush()
        secondo = self.session.scalars(
            select(InvestmentTransaction).where(InvestmentTransaction.name == "Secondo")).one()
        movimento = Transaction(occurred_on=date(2024, 1, 6), effective_on=date(2024, 1, 6),
                                transaction_type="Investment", category_id=None, amount=Decimal("200"),
                                account_name="Conto", destination_name="Titoli 2")
        self.session.add(movimento)
        self.session.flush()
        self.session.add(TransactionLedgerLink(transaction_id=movimento.id, ledger_id=secondo.id))
        self.session.commit()

        dopo = self._serie(level="side", side="assets")["series"][-1]["values"]["investments"]
        # 2 quote comprate a 100 e quotate 120: il conto nuovo porta 240, cioe'
        # i suoi 200 di costo piu' i suoi 40 di guadagno. Non 40 piu' i 500 del
        # primo conto, che e' quello che succedeva spartendo il totale a peso.
        self.assertAlmostEqual(prima + 240.0, dopo, places=2)

    def test_attribuite_tutte_il_conto_vale_il_mercato(self) -> None:
        """"Una volta assegnate tutte devono combaciare": qui tutte le
        operazioni del ledger sono attribuite a un conto solo, e quel conto deve
        valere esattamente il valore di mercato del portafoglio - non il costo,
        non una frazione."""
        punto = self._serie(level="side", side="assets")["series"][-1]
        linea = portfolio_timeline(self.session)
        self.assertAlmostEqual(linea[-1]["marketValue"], punto["values"]["investments"], places=2)

    def test_un_investment_senza_broker_viene_rifiutato(self) -> None:
        """La regola che rende vera tutta la catena: se un Investment potesse
        puntare ovunque, collegarlo al ledger attribuirebbe la rivalutazione a
        un conto qualunque."""
        from fastapi import HTTPException

        from app.transaction_rules import validate_movement
        movimento = Transaction(occurred_on=date(2024, 3, 1), effective_on=date(2024, 3, 1),
                                transaction_type="Investment", category_id=None, amount=Decimal("10"),
                                account_name="Conto", destination_name="Casa")
        with self.assertRaises(HTTPException) as errore:
            validate_movement(self.session, movimento)
        self.assertEqual("investmentNeedsBroker", errore.exception.detail["code"])
        # Con un broker su un lato passa, in tutte e due le direzioni.
        movimento.destination_name = "Titoli"
        validate_movement(self.session, movimento)
        movimento.account_name, movimento.destination_name = "Titoli", "Conto"
        validate_movement(self.session, movimento)


if __name__ == "__main__":
    unittest.main()
