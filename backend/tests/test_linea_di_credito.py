"""Uno scoperto non e' un prestito a rate, e non va misurato come tale.

Un conto con fido ha un saldo che galleggia e interessi che ci si aggiungono:
non ha tranche, scadenze ne' un residuo teorico. Applicargli il piano di
ammortamento produceva un "dovresti ancora 7.090" che la banca non ha mai
chiesto, e un confronto col piano che non voleva dire niente.

Il limite e' facoltativo di proposito: uno scoperto che cresce con gli
interessi non ne ha uno, e inventarne uno per poter mostrare una percentuale
di utilizzo direbbe una cosa che non sappiamo.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import LiabilityPayload, TransactionPayload, create_transaction, liabilities, save_liability
from app.models import Account, LiabilityProfile


class LineaDiCreditoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(source_group="bank", name="Banca", starting_balance=Decimal("5000"),
                    current_balance=Decimal("5000"), is_active=True),
            Account(source_group="liability", name="Fido", starting_balance=Decimal("0"),
                    current_balance=Decimal("0"), is_active=True),
        ])
        self.session.commit()
        self.fido = self.session.scalars(select(Account).where(Account.name == "Fido")).one().id
        self.session.add(LiabilityProfile(
            account_id=self.fido, kind="credit_line", original_principal=Decimal("0"),
            annual_rate=Decimal("1.6"), start_date=date(2026, 1, 1), end_date=date(2030, 12, 31)))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _voce(self) -> dict:
        return next(row for row in liabilities(self.session)["items"] if row["name"] == "Fido")

    def _preleva(self, giorno: str, importo: float) -> None:
        create_transaction(TransactionPayload(
            occurred_on=giorno, transaction_type="Debt", amount=importo,
            account_name="Fido", destination_name="Banca",
            debt_principal=importo, debt_interest=0), self.session)

    def test_non_produce_un_piano_ne_un_residuo_teorico(self) -> None:
        self._preleva("2026-02-01", 1000)
        voce = self._voce()
        self.assertEqual("credit_line", voce["kind"])
        for campo in ("schedule", "theoreticalRemaining", "difference", "nextPayment",
                      "drawnPrincipal", "principalRepaid", "interestOutstanding"):
            self.assertNotIn(campo, voce, f"una linea non ha {campo}")

    def test_l_esposizione_e_il_saldo_del_conto(self) -> None:
        self._preleva("2026-02-01", 1000)
        self.assertEqual(1000.0, self._voce()["exposure"])

    def test_picco_iniziale_e_tasso_medio_includono_la_linea(self) -> None:
        conto = self.session.get(Account, self.fido)
        conto.starting_balance = Decimal("-1000")
        self.session.commit()
        dati = liabilities(self.session)
        voce = next(r for r in dati["items"] if r["accountId"] == self.fido)
        self.assertEqual(1000, voce["peakExposure"])
        self.assertEqual(1000, voce["exposure"])
        self.assertEqual(1.6, dati["summary"]["weightedRate"])
        self.assertEqual(1000, voce["trend"][-1]["exposure"])

    def test_il_picco_conta_anche_i_giroconti(self) -> None:
        # Il giroconto sul conto passa e il saldo lo muove: escluderlo era il
        # punto cieco che faceva divergere la pagina dal saldo del conto.
        self._preleva("2026-02-01", 1000)
        create_transaction(TransactionPayload(
            occurred_on="2026-02-10", transaction_type="Transfers", amount=500,
            account_name="Fido", destination_name="Banca"), self.session)
        create_transaction(TransactionPayload(
            occurred_on="2026-02-20", transaction_type="Transfers", amount=1500,
            account_name="Banca", destination_name="Fido"), self.session)
        voce = self._voce()
        # Rientrato a zero dentro lo stesso mese, ma il picco resta.
        self.assertEqual(0.0, voce["exposure"])
        self.assertEqual(1500.0, voce["peakExposure"], "il picco infra-mese non deve perdersi")

    def test_gli_interessi_dell_anno_sono_solo_quelli_dichiarati(self) -> None:
        self._preleva("2026-01-02", 1000)
        for giorno, importo, dichiara in (("2026-03-01", 40, True),
                                          ("2026-06-01", 25, False),
                                          ("2025-03-01", 99, True)):
            create_transaction(TransactionPayload(
                occurred_on=giorno, transaction_type="Expenses", amount=importo,
                category="Interessi", account_name="Fido",
                **({"debt_interest": importo} if dichiara else {})), self.session)
        voce = self._voce()
        # 40 dichiarato nell'anno; 25 non dichiarato; 99 di un altro anno.
        self.assertEqual(40.0, voce["interestThisYear"])
        self.assertEqual(1, voce["unclassified"]["count"])

    def test_senza_limite_non_si_mostra_un_utilizzo(self) -> None:
        self._preleva("2026-02-01", 1000)
        voce = self._voce()
        self.assertIsNone(voce["creditLimit"])
        self.assertIsNone(voce["utilisation"], "senza limite la percentuale non esiste")

    def test_con_un_limite_l_utilizzo_si_calcola(self) -> None:
        profilo = self.session.scalars(
            select(LiabilityProfile).where(LiabilityProfile.account_id == self.fido)).one()
        profilo.credit_limit = Decimal("4000")
        self.session.commit()
        self._preleva("2026-02-01", 1000)
        voce = self._voce()
        self.assertEqual(4000.0, voce["creditLimit"])
        self.assertEqual(25.0, voce["utilisation"])

    def test_si_salva_senza_i_dati_del_piano(self) -> None:
        """Una linea si configura senza tranche, frequenza ne' inizio rimborso.

        Nel modulo quei campi non compaiono, quindi il form non li invia.
        L'endpoint li validava prima di guardare il tipo e rifiutava il
        salvataggio con "liabilityInvalidFields": si configurava una linea solo
        riempiendo campi che per una linea non vogliono dire niente.
        """
        esito = save_liability(self.fido, LiabilityPayload(
            kind="credit_line", debt_type="revolving", original_principal=1, annual_rate=2.5,
            start_date="2026-01-01", end_date="2030-12-31",
            repayment_start_date=None, planned_drawdowns=[]), self.session)
        self.assertEqual("credit_line", esito["kind"])
        self.assertIsNone(esito["creditLimit"])
        self.assertEqual([], esito["plannedDrawdowns"], "una linea non ha tranche")

    def test_tornando_prestito_il_tipo_si_riallinea(self) -> None:
        # Cambiare idea deve funzionare in entrambi i versi: un profilo salvato
        # come prestito non deve restare marcato linea di credito.
        save_liability(self.fido, LiabilityPayload(
            kind="credit_line", debt_type="revolving", original_principal=1, annual_rate=2.5,
            start_date="2026-01-01", end_date="2030-12-31", planned_drawdowns=[]), self.session)
        esito = save_liability(self.fido, LiabilityPayload(
            kind="term_loan", debt_type="personal", original_principal=1000, annual_rate=3,
            start_date="2026-01-01", repayment_start_date="2026-02-01", end_date="2027-01-01",
            planned_drawdowns=[]), self.session)
        self.assertEqual("term_loan", esito["kind"])
        self.assertIsNone(esito["creditLimit"], "il limite non deve sopravvivere al cambio di tipo")

class PrestitoARateNonCambiaTests(unittest.TestCase):
    """Il rischio di questo lotto non sono le linee: sono i prestiti di prima.

    Un profilo senza `kind` dichiarato resta `term_loan`, e deve continuare a
    produrre esattamente quello che produceva: piano, scadenze, residuo teorico
    e confronto. Se questa prova cade, il ramo nuovo ha invaso il vecchio.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            Account(source_group="bank", name="Banca", starting_balance=Decimal("5000"),
                    current_balance=Decimal("5000"), is_active=True),
            Account(source_group="liability", name="Prestito", starting_balance=Decimal("0"),
                    current_balance=Decimal("0"), is_active=True),
        ])
        self.session.commit()
        conto = self.session.scalars(select(Account).where(Account.name == "Prestito")).one().id
        self.session.add(LiabilityProfile(
            account_id=conto, original_principal=Decimal("12000"), annual_rate=Decimal("5"),
            start_date=date(2026, 1, 1), repayment_start_date=date(2026, 2, 1),
            end_date=date(2027, 1, 1), payment_structure="amortizing"))
        self.session.commit()
        create_transaction(TransactionPayload(
            occurred_on="2026-01-01", transaction_type="Debt", amount=12000,
            account_name="Prestito", destination_name="Banca",
            debt_principal=12000, debt_interest=0), self.session)

    def tearDown(self) -> None:
        self.session.close()

    def test_il_tipo_predefinito_e_il_prestito_a_rate(self) -> None:
        voce = next(r for r in liabilities(self.session)["items"] if r["name"] == "Prestito")
        self.assertEqual("term_loan", voce["profile"]["kind"])

    def test_continua_a_produrre_piano_e_confronto(self) -> None:
        voce = next(r for r in liabilities(self.session)["items"] if r["name"] == "Prestito")
        self.assertTrue(voce["schedule"], "il piano deve esserci")
        self.assertIsNotNone(voce["theoreticalRemaining"])
        self.assertIsNotNone(voce["difference"])
        self.assertEqual(12000.0, voce["drawnPrincipal"])


class MotiviDelRifiutoTests(unittest.TestCase):
    """Ogni regola sulle date ha il suo codice, e il modulo lo traduce.

    Con un codice solo la pagina diceva "impossibile salvare le condizioni del
    debito": con due tranche e l'inizio dei rimborsi vuoto non si capiva cosa
    correggere.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(source_group="liability", name="Mutuo", starting_balance=Decimal("0"),
                                 current_balance=Decimal("0"), is_active=True))
        self.session.commit()
        self.conto = self.session.scalars(select(Account)).one().id

    def tearDown(self) -> None:
        self.session.close()

    def _rifiuto(self, **campi) -> str:
        from fastapi import HTTPException
        base = dict(kind="term_loan", debt_type="mortgage", original_principal=20000, annual_rate=2,
                    start_date="2024-01-15", end_date="2044-01-15",
                    planned_drawdowns=[{"occurred_on": "2024-01-15", "amount": 10000},
                                       {"occurred_on": "2024-06-15", "amount": 10000}])
        base.update(campi)
        with self.assertRaises(HTTPException) as errore:
            save_liability(self.conto, LiabilityPayload(**base), self.session)
        return errore.exception.detail

    def test_piu_tranche_senza_inizio_rimborsi_dice_quale_campo(self) -> None:
        self.assertEqual("liabilityDrawdownAfterRepaymentStart", self._rifiuto())

    def test_ogni_regola_ha_il_suo_codice(self) -> None:
        self.assertEqual("liabilityEndBeforeStart", self._rifiuto(end_date="2023-01-01"))
        self.assertEqual("liabilityRepaymentAfterEnd", self._rifiuto(repayment_start_date="2045-01-01"))
        self.assertEqual("liabilityDrawdownAfterEnd", self._rifiuto(
            repayment_start_date="2024-07-01", payment_structure="interest_only",
            planned_drawdowns=[{"occurred_on": "2045-01-01", "amount": 20000}]))
        self.assertEqual("liabilityDrawdownsNotPrincipal", self._rifiuto(
            repayment_start_date="2024-07-01", original_principal=25000))

    def test_con_l_inizio_rimborsi_dopo_l_ultima_tranche_si_salva(self) -> None:
        save_liability(self.conto, LiabilityPayload(
            kind="term_loan", debt_type="mortgage", original_principal=20000, annual_rate=2,
            start_date="2024-01-15", end_date="2044-01-15", repayment_start_date="2024-07-01",
            planned_drawdowns=[{"occurred_on": "2024-01-15", "amount": 10000},
                               {"occurred_on": "2024-06-15", "amount": 10000}]), self.session)
