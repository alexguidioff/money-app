"""I movimenti in un'altra valuta pesano in euro nei totali.

Un movimento su un conto in franchi e' in franchi: e' il numero che si legge
sulla riga di quel conto, e il suo saldo resta in franchi. Ma "quanto hai speso
questo mese" e' una cifra sola, in euro, e sommare cento franchi a cento euro
senza convertirli sarebbe la stessa bugia del patrimonio - solo piu' difficile da
vedere, perche' i due numeri sono vicini.

Il cambio e' quello **del giorno del movimento**: col cambio di oggi il passato
si riscriverebbe a ogni movimento del cambio, e una cena del 2024 costerebbe
domani una cifra diversa.

Tre casi che valgono piu' di una spiegazione:
- un conto in euro non legge nemmeno il listino: per chi ha solo conti in euro i
  numeri restano quelli di prima, cifra per cifra;
- un movimento di una valuta **senza** cambio pesa 1. E' sbagliato, ma e' il male
  minore: una conversione sbagliata si vede confrontando due mesi, un movimento
  che sparisce da un totale non si vede affatto;
- la valuta si scrive sul movimento quando nasce, e non si ricava a ogni
  lettura: il conto puo' cambiare valuta, ed e' una correzione - li' i movimenti
  si riallineano tutti insieme, e il riallineamento lo dice la risposta.

I cambi dei test sono scritti a mano nel listino: numeri tondi e inventati.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import (_movimenti_goal, _totali_per_categoria, budget_actual,
                             budget_actual_year, period_total, transaction_json, transactions)
from app.database import Base
from app.main import (AccountPayload, TransactionPayload, _report_period_total,
                      create_transaction, update_account)
from app.models import Account, Category, Goal, MarketPrice, Transaction


def _cambio(session: Session, codice: str, giorno: date, valore: str) -> None:
    """Un cambio nel listino, come lo scriverebbe lo scarico delle quotazioni."""
    session.add(MarketPrice(symbol=f"EUR{codice}=X", observed_on=giorno, price=Decimal(valore)))


class MovimentiInValutaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(name="Conto", source_group="bank", currency="EUR",
                    starting_balance=Decimal("0"), current_balance=Decimal("0"),
                    counts_in_net_worth=True, is_active=True),
            # Cento franchi non sono cento euro: e' tutto il punto di questi test.
            Account(name="Revolut", source_group="bank", currency="CHF",
                    starting_balance=Decimal("0"), current_balance=Decimal("0"),
                    counts_in_net_worth=True, is_active=True),
            Category(name="Spesa", scope="expense"),
            Category(name="Entrata", scope="income"),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    # -- attrezzi ----------------------------------------------------------

    def _categoria(self, nome: str) -> int:
        return self.session.scalars(select(Category.id).where(Category.name == nome)).one()

    def _movimento(self, giorno: date, importo: str, *,
                   conto: str = "Revolut", tipo: str = "Expenses",
                   categoria: str = "Spesa", valuta: str | None = None,
                   **extra) -> Transaction:
        """Un movimento come l'avrebbe scritto l'app, valuta del conto compresa."""
        valuta = valuta or self.session.scalars(
            select(Account.currency).where(Account.name == conto)).one()
        # Un giroconto non ha categoria: e' il vuoto che lo dice, non un nome.
        riga = Transaction(occurred_on=giorno, effective_on=giorno, transaction_type=tipo,
                           category_id=self._categoria(categoria) if categoria else None,
                           amount=Decimal(importo), account_name=conto, currency=valuta, **extra)
        self.session.add(riga)
        self.session.commit()
        return riga

    def _speso(self, anno: int, mese: int) -> float:
        return budget_actual(self.session, anno, mese, "Expenses")[self._categoria("Spesa")]

    # -- i totali ----------------------------------------------------------

    def test_una_spesa_in_franchi_pesa_il_suo_valore_in_euro(self) -> None:
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00")
        # 100 franchi a 1,10 franchi per euro sono 90,91 euro.
        self.assertAlmostEqual(90.91, self._speso(2026, 7), places=2)
        self.assertAlmostEqual(90.91, period_total(self.session, 2026, 7, "Expenses"), places=2)
        # La stessa cifra per le due strade SQL: la somma per categoria e
        # l'aggregato anno per anno.
        self.assertAlmostEqual(90.91, _totali_per_categoria(
            self.session, date(2026, 7, 1), date(2026, 7, 31), "Expenses")[self._categoria("Spesa")], places=2)
        self.assertAlmostEqual(90.91, budget_actual_year(self.session, 2026, "Expenses")[7][self._categoria("Spesa")], places=2)

    def test_in_euro_il_numero_non_si_muove(self) -> None:
        # Il percorso veloce: c'e' un cambio in listino, ma il movimento e' in
        # euro e non lo legge.
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00", conto="Conto")
        self.assertAlmostEqual(100.0, self._speso(2026, 7), places=2)
        self.assertAlmostEqual(100.0, budget_actual_year(self.session, 2026, "Expenses")[7][self._categoria("Spesa")], places=2)

    def test_senza_cambio_il_movimento_pesa_come_se_fosse_in_euro(self) -> None:
        # Nessun cambio per il franco: il movimento resta dentro il totale al
        # suo valore nominale, e il listino non inventa niente.
        self._movimento(date(2026, 7, 10), "100.00")
        self.assertAlmostEqual(100.0, self._speso(2026, 7), places=2)

    def test_ogni_mese_usa_il_cambio_del_suo_giorno(self) -> None:
        # A giugno un franco valeva 1,25 (100 franchi = 80 euro), a luglio 1,00.
        _cambio(self.session, "CHF", date(2026, 6, 30), "1.25")
        _cambio(self.session, "CHF", date(2026, 7, 31), "1.00")
        self.session.commit()
        self._movimento(date(2026, 6, 30), "100.00")
        self._movimento(date(2026, 7, 31), "100.00")
        self.assertAlmostEqual(80.0, self._speso(2026, 6), places=2)
        self.assertAlmostEqual(100.0, self._speso(2026, 7), places=2)

    def test_le_entrate_si_convertono_anche_loro(self) -> None:
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "500.00", tipo="Income", categoria="Entrata")
        # 500 franchi a 1,10 sono 454,55 euro.
        self.assertAlmostEqual(454.55, period_total(self.session, 2026, 7, "Income"), places=2)

    def test_un_giroconto_non_pesa_sul_budget(self) -> None:
        # Spostare denaro fra due conti non e' ne' una spesa ne' un'entrata, in
        # nessuna delle due valute.
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00", tipo="Transfers", categoria=None,
                        counts_in_budget=False, destination_name="Conto",
                        destination_amount=Decimal("90.00"))
        self.assertEqual({}, budget_actual(self.session, 2026, 7, "Expenses"))
        self.assertAlmostEqual(0.0, period_total(self.session, 2026, 7, "Expenses"), places=2)

    # -- il giroconto fra due valute --------------------------------------

    def test_un_giroconto_fra_due_valute_dice_quanto_arriva(self) -> None:
        """Le due cifre di un giroconto fra valute diverse, e la loro moneta.

        L'importo che arriva sta sul movimento; la valuta no, perche' e' quella
        del conto d'arrivo. Se la riga non le porta tutte e due, l'interfaccia
        scrive l'uscita e tace l'entrata - e il modulo di modifica, che riempie
        i campi da qui, salva un secondo importo vuoto: il saldo del conto
        d'arrivo cambia senza che nessuno l'abbia chiesto.
        """
        movimento = self._movimento(date(2026, 7, 10), "100.00", conto="Conto", tipo="Transfers",
                                    categoria=None, counts_in_budget=False, destination_name="Revolut",
                                    destination_amount=Decimal("90.00"))
        voce = transaction_json(movimento, self.session)
        self.assertEqual("EUR", voce["currency"])
        self.assertEqual(90.0, voce["destinationAmount"])
        self.assertEqual("CHF", voce["destinationCurrency"])
        # Chi serializza un elenco se la porta dietro gia' risolta, come la
        # categoria: e' il motivo per cui la funzione accetta il parametro.
        self.assertEqual("CHF", transaction_json(movimento, valuta_destinazione="CHF")["destinationCurrency"])
        # La pagina dei movimenti e' la strada da cui ci passa l'interfaccia.
        riga = next(item for item in transactions(100, self.session)["items"]
                    if item["id"] == voce["id"])
        self.assertEqual(90.0, riga["destinationAmount"])
        self.assertEqual("CHF", riga["destinationCurrency"])

    def test_senza_secondo_importo_la_riga_ha_una_cifra_sola(self) -> None:
        # Nessun cambio di mezzo - fra conti in pari valuta il server scrive il
        # vuoto, non la stessa cifra due volte: la seconda cifra non c'e', e non
        # c'e' nemmeno la moneta da cui leggerla.
        voce = transaction_json(self._movimento(
            date(2026, 7, 10), "100.00", conto="Conto", tipo="Transfers", categoria=None,
            counts_in_budget=False, destination_name="Revolut"), self.session)
        self.assertIsNone(voce["destinationAmount"])
        self.assertIsNone(voce["destinationCurrency"])

    def test_una_seconda_cifra_uguale_alla_prima_non_si_scrive_due_volte(self) -> None:
        # Un estratto conto importato scrive sempre la cifra d'arrivo, anche
        # quando e' identica a quella di partenza: la riga scriverebbe lo stesso
        # numero due volte, e la prima basta.
        voce = transaction_json(self._movimento(
            date(2026, 7, 10), "100.00", conto="Conto", tipo="Transfers", categoria=None,
            counts_in_budget=False, destination_name="Conto",
            destination_amount=Decimal("100.00")), self.session)
        self.assertIsNone(voce["destinationAmount"])
        self.assertIsNone(voce["destinationCurrency"])

    def test_il_report_del_mese_conta_in_euro(self) -> None:
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00")
        self.assertAlmostEqual(90.91, _report_period_total(self.session, 2026, 7, "Expenses"), places=2)

    # -- chi scrive la valuta ----------------------------------------------

    def test_il_movimento_nasce_con_la_valuta_del_suo_conto(self) -> None:
        risposta = create_transaction(TransactionPayload(
            occurred_on="2026-07-10", transaction_type="Expenses", category="Spesa",
            amount=100.0, account_name="Revolut"), self.session)
        self.assertEqual("CHF", risposta["currency"])
        riga = self.session.scalars(select(Transaction).where(Transaction.id == risposta["id"])).one()
        self.assertEqual("CHF", riga.currency)
        # E su un conto in euro la valuta e' l'euro, senza che nessuno la scriva.
        altra = create_transaction(TransactionPayload(
            occurred_on="2026-07-10", transaction_type="Expenses", category="Spesa",
            amount=50.0, account_name="Conto"), self.session)
        self.assertEqual("EUR", altra["currency"])

    def test_cambiare_valuta_riallinea_i_movimenti_e_lo_dice(self) -> None:
        conto = self.session.scalars(select(Account).where(Account.name == "Conto")).one()
        create_transaction(TransactionPayload(
            occurred_on="2026-07-10", transaction_type="Expenses", category="Spesa",
            amount=100.0, account_name="Conto"), self.session)
        create_transaction(TransactionPayload(
            occurred_on="2026-07-11", transaction_type="Expenses", category="Spesa",
            amount=50.0, account_name="Conto"), self.session)
        # I movimenti vecchi non restano in euro mentre il conto e' in franchi:
        # il saldo del conto sarebbe la somma di due valute diverse.
        risposta = update_account(conto.id, AccountPayload(
            name="Conto", source_group="bank", starting_balance=0, currency="CHF"), self.session)
        self.assertEqual(2, risposta["movementsRealigned"])
        self.assertEqual([], [riga.currency for riga in self.session.scalars(
            select(Transaction).where(Transaction.currency != "CHF")).all()])
        # La stessa richiesta con la stessa valuta non riallinea niente, e lo
        # dice con uno zero invece di tacere.
        ancora = update_account(conto.id, AccountPayload(
            name="Conto", source_group="bank", starting_balance=0, currency="CHF"), self.session)
        self.assertEqual(0, ancora["movementsRealigned"])

    # -- gli obiettivi ------------------------------------------------------

    def test_un_versamento_in_franchi_entra_convertito_nel_goal(self) -> None:
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.add(Goal(name="Vacanza", kind="contributions"))
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00", goal="Vacanza")
        goal = self.session.scalars(select(Goal).where(Goal.name == "Vacanza")).one()
        righe = _movimenti_goal(self.session, goal)
        self.assertAlmostEqual(90.91, float(righe[0][1]), places=2)

    def test_quello_che_arriva_nel_salvadanaio_si_converte_col_suo_cambio(self) -> None:
        # Il salvadanaio e' in franchi e il versamento parte da un conto in euro:
        # quello che entra sono 90 franchi, e valgono 81,82 euro - non 90.
        _cambio(self.session, "CHF", date(2026, 7, 1), "1.10")
        self.session.add(Goal(name="Vacanza", kind="contributions", target_account="Revolut"))
        self.session.commit()
        self._movimento(date(2026, 7, 10), "100.00", conto="Conto", tipo="Transfers", categoria=None,
                        counts_in_budget=False, goal="Vacanza", destination_name="Revolut",
                        destination_amount=Decimal("90.00"))
        goal = self.session.scalars(select(Goal).where(Goal.name == "Vacanza")).one()
        righe = _movimenti_goal(self.session, goal)
        self.assertAlmostEqual(81.82, float(righe[0][1]), places=2)


if __name__ == '__main__':
    unittest.main()
