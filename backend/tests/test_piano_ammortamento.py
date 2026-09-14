"""Il piano teorico di un debito.

Tre cose sorvegliate, tutte e tre nate da difetti veri trovati su un prestito
vero (40.000 in quattro tranche, rimborso trimestrale dopo l'ultima):

- la rata costante deve essere costante: si calcolava la rata col tasso
  periodale e gli interessi coi giorni effettivi, due convenzioni che non
  chiudono, e lo scarto finiva tutto sull'ultima rata (376 euro);
- il preammortamento deve avere le sue scadenze: non esistendo, tutti gli
  interessi maturati prima del rimborso piombavano sulla prima rata (482 euro
  su 886 totali);
- un piano che non si puo' costruire deve dire perche': sei motivi diversi
  tornavano tutti una lista vuota, e la pagina restava bianca.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from app.calculation_engine import debito_pianificato_al, piano_ammortamento, stato_debito_registrato

TRANCHE = [
    {"occurredOn": "2023-01-12", "amount": 10000.0},
    {"occurredOn": "2023-08-02", "amount": 10000.0},
    {"occurredOn": "2024-02-01", "amount": 10000.0},
    {"occurredOn": "2024-08-01", "amount": 10000.0},
]


def _prestito(**campi):
    base = dict(principal=Decimal("40000"), annual_rate=Decimal("1.13"),
                start=date(2023, 1, 12), end=date(2026, 8, 19), frequency="quarterly",
                structure="amortizing", drawdowns=TRANCHE, repayment_start=date(2024, 8, 20))
    return piano_ammortamento(**{**base, **campi})


class RataCostanteTests(unittest.TestCase):
    def test_le_rate_di_rimborso_sono_uguali(self) -> None:
        piano, motivo = _prestito()
        self.assertIsNone(motivo)
        rate = [r for r in piano if r["principal"] > 0]
        # L'ultima assorbe l'arrotondamento: qualche centesimo, non 376 euro.
        for r in rate[:-1]:
            self.assertEqual(rate[0]["payment"], r["payment"])
        self.assertLess(abs(rate[-1]["payment"] - rate[0]["payment"]), 1.0)

    def test_il_capitale_rimborsato_e_quello_erogato(self) -> None:
        piano, _ = _prestito()
        self.assertAlmostEqual(40000.0, sum(r["principal"] for r in piano), places=2)
        self.assertEqual(0.0, piano[-1]["remaining"])


class PreammortamentoTests(unittest.TestCase):
    def test_ha_le_sue_scadenze(self) -> None:
        piano, _ = _prestito()
        prima_del_rimborso = [r for r in piano if r["dueOn"] < "2024-08-20"]
        self.assertGreater(len(prima_del_rimborso), 1)
        # Nessuna di quelle rate rimborsa capitale, e nessuna concentra tutto.
        self.assertTrue(all(r["principal"] == 0 for r in prima_del_rimborso))
        self.assertLess(max(r["interest"] for r in prima_del_rimborso), 100)

    def test_capitalizzati_il_debito_cresce_prima_di_scendere(self) -> None:
        pagati, _ = _prestito(grace_interest="paid")
        capitalizzati, _ = _prestito(grace_interest="capitalised")
        # Pagando, il preammortamento costa ogni scadenza; capitalizzando no.
        self.assertGreater(sum(r["payment"] for r in pagati if r["principal"] == 0), 0)
        self.assertEqual(0.0, sum(r["payment"] for r in capitalizzati if r["principal"] == 0))
        # E capitalizzando si rimborsa piu' capitale: gli interessi ci sono dentro.
        self.assertGreater(sum(r["principal"] for r in capitalizzati), 40000.0)


class MotiviTests(unittest.TestCase):
    def test_ogni_rifiuto_dice_il_suo_motivo(self) -> None:
        casi = {
            "frequenzaSconosciuta": dict(frequency="weekly"),
            "strutturaSconosciuta": dict(structure="boh"),
            "erogazioniNonValide": dict(drawdowns=[{"occurredOn": "2023-01-12", "amount": 0}]),
            "scadenzaPrimaDellErogazione": dict(end=date(2022, 1, 1)),
            "rimborsoDopoLaScadenza": dict(repayment_start=date(2027, 1, 1)),
            "erogazioniDopoIlRimborso": dict(repayment_start=date(2023, 9, 1)),
        }
        for atteso, campi in casi.items():
            piano, motivo = _prestito(**campi)
            self.assertEqual(atteso, motivo, msg=str(campi))
            self.assertEqual([], piano)

    def test_un_piano_valido_non_ha_motivo(self) -> None:
        self.assertIsNone(_prestito()[1])


class StruttureTests(unittest.TestCase):
    def test_interest_only_rimborsa_tutto_alla_fine(self) -> None:
        piano, _ = _prestito(structure="interest_only")
        with_principal = [r for r in piano if r["principal"] > 0]
        self.assertEqual(1, len(with_principal))
        self.assertEqual(piano[-1], with_principal[0])

    def test_capitale_costante_ha_quote_uguali(self) -> None:
        piano, _ = _prestito(structure="constant_principal")
        quote = [r["principal"] for r in piano if r["principal"] > 0]
        self.assertLess(max(quote) - min(quote), 1.0)

    def test_bullet_ignora_l_inizio_del_rimborso(self) -> None:
        """`repayment_start` dice quando cominciano le RATE, e un bullet non ne
        ha: il pagamento resta alla scadenza."""
        piano, _ = _prestito(structure="bullet")
        self.assertEqual(1, len(piano))
        self.assertEqual("2026-08-19", piano[0]["dueOn"])

    def test_debito_tutto_compreso_chiude_col_piano(self) -> None:
        piano, _ = _prestito(structure="bullet")
        self.assertGreater(debito_pianificato_al(piano, TRANCHE, Decimal("1.13"), date(2026, 8, 18)),
                           Decimal("40000"))
        self.assertEqual(Decimal("0.00"),
                         debito_pianificato_al(piano, TRANCHE, Decimal("1.13"), date(2026, 8, 19)))

    def test_a_tasso_zero_non_ci_sono_interessi(self) -> None:
        piano, _ = _prestito(annual_rate=Decimal("0"))
        self.assertEqual(0.0, sum(r["interest"] for r in piano))
        self.assertAlmostEqual(40000.0, sum(r["principal"] for r in piano), places=2)


class DebitoRegistratoTests(unittest.TestCase):
    """Il debito reale viene dai movimenti registrati, non da un calcolo.

    Prima `stato_debito_effettivo` prendeva il tasso e ricavava gli interessi
    maturati giorno per giorno, capitalizzazione compresa. Ora
    `stato_debito_registrato` prende gli addebiti: dichiara quanto la banca ha
    chiesto e quanto e' stato pagato, niente di piu'. Le due prove sulla
    maturazione calcolata sono uscite con la funzione - quel conto vive ancora,
    ma solo dentro `piano_ammortamento`, dove confrontarlo col reale ha senso.

    Questo file non girava affatto: l'import puntava al nome vecchio, pytest si
    fermava alla raccolta, e con lui restavano ferme anche le altre undici
    prove sul piano di ammortamento.
    """

    def test_separa_capitale_addebiti_e_pagamenti(self) -> None:
        stato = stato_debito_registrato(
            [{"occurredOn": "2023-01-01", "amount": 1000}],
            [{"occurredOn": "2024-01-01", "principal": 400, "interest": 80}],
            [{"occurredOn": "2023-12-31", "amount": 100}],
            date(2024, 1, 1))
        self.assertEqual((Decimal("600.00"), Decimal("100.00"), Decimal("80.00"),
                          Decimal("20.00"), Decimal("620.00")),
                         (stato["principalOutstanding"], stato["interestCharged"],
                          stato["interestPaid"], stato["interestOutstanding"], stato["totalDebt"]))

    def test_ignora_cio_che_viene_dopo_la_data(self) -> None:
        # La pagina mostra lo stato "a oggi": un addebito del prossimo anno non
        # deve entrare fra quelli gia' maturati.
        stato = stato_debito_registrato(
            [{"occurredOn": "2023-01-01", "amount": 1000}], [],
            [{"occurredOn": "2023-06-01", "amount": 50},
             {"occurredOn": "2024-06-01", "amount": 50}],
            date(2023, 12, 31))
        self.assertEqual(Decimal("50.00"), stato["interestCharged"])

    def test_pagare_piu_del_dovuto_non_crea_un_credito(self) -> None:
        # Capitale e interessi residui si fermano a zero: un debito estinto in
        # eccesso non deve comparire come un avere.
        stato = stato_debito_registrato(
            [{"occurredOn": "2023-01-01", "amount": 1000}],
            [{"occurredOn": "2023-06-01", "principal": 2000, "interest": 200}],
            [{"occurredOn": "2023-03-01", "amount": 100}],
            date(2023, 12, 31))
        self.assertEqual((Decimal("0.00"), Decimal("0.00")),
                         (stato["principalOutstanding"], stato["interestOutstanding"]))
