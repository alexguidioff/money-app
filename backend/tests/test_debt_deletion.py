from datetime import date
from unittest import TestCase, mock

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import (LiabilityDeletePayload, TransactionPayload, create_transaction,
                      delete_account, delete_liability, liabilities)
from app.models import Account, LiabilityProfile, LiabilityTransactionDetail, Transaction
from tests.categorie_fixture import categoria


class DebtDeletionTests(TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.account = Account(name="Debito prova", source_group="liability",
                               starting_balance=-1000, current_balance=-1000)
        self.session.add(self.account)
        self.session.flush()
        self.account_id = self.account.id
        self.profile = LiabilityProfile(account_id=self.account_id, original_principal=1000,
                                        annual_rate=5, start_date=date(2026, 1, 1),
                                        end_date=date(2030, 1, 1))
        self.session.add(self.profile)
        self.session.commit()
        # La categoria si nomina per id: `categoria` la crea e torna l'id.
        create_transaction(TransactionPayload(occurred_on=date.today().isoformat(),
                           transaction_type="Expenses", amount=10,
                           categoryId=categoria(self.session, "Interessi"),
                           account_name=self.account.name, debt_interest=10), self.session)
        self.backup = mock.patch("app.main.create_backup",
                                 return_value={"success": True, "filename": "test.dump"})
        self.dump = self.backup.start()

    def tearDown(self):
        self.backup.stop()
        self.session.close()
        self.engine.dispose()

    def test_conto_richiede_conferma_esplicita(self):
        for name in (None, "Nome diverso"):
            with self.assertRaises(HTTPException) as error:
                delete_liability(self.account_id, LiabilityDeletePayload(account_name=name), self.session)
            self.assertEqual(409, error.exception.status_code)
        self.dump.assert_not_called()
        self.assertIsNotNone(self.session.get(Account, self.account_id))

    def test_backup_prima_di_eliminare_e_movimenti_conservati(self):
        def backup_prima(label):
            self.assertIsNotNone(self.session.get(Account, self.account_id))
            self.assertIsNotNone(self.session.scalar(select(LiabilityProfile)))
            return {"success": True, "filename": "test.dump"}
        self.dump.side_effect = backup_prima
        result = delete_liability(self.account_id,
                                  LiabilityDeletePayload(account_name=self.account.name), self.session)
        self.assertEqual("test.dump", result["backup"])
        self.assertIsNone(self.session.get(Account, self.account_id))
        self.assertIsNone(self.session.scalar(select(LiabilityProfile)))
        self.assertIsNone(self.session.scalar(select(LiabilityTransactionDetail)))
        self.assertIsNotNone(self.session.scalar(select(Transaction)))
        self.dump.assert_called_once()

    def test_backup_fallito_non_elimina_nulla(self):
        self.dump.side_effect = RuntimeError("disco pieno")
        with self.assertRaises(HTTPException) as error:
            delete_liability(self.account_id,
                             LiabilityDeletePayload(account_name=self.account.name), self.session)
        self.assertEqual("debtDeleteBackupFailed", error.exception.detail)
        self.assertIsNotNone(self.session.get(Account, self.account_id))
        self.assertIsNotNone(self.session.scalar(select(LiabilityProfile)))
        self.assertIsNotNone(self.session.scalar(select(LiabilityTransactionDetail)))

    def test_configurazione_orfana_visibile_ed_eliminabile(self):
        self.session.delete(self.account)
        self.session.commit()
        self.assertEqual(self.account_id, liabilities(self.session)["orphaned"][0]["accountId"])
        delete_liability(self.account_id, LiabilityDeletePayload(), self.session)
        self.assertIsNone(self.session.scalar(select(LiabilityProfile)))
        self.assertIsNone(self.session.scalar(select(LiabilityTransactionDetail)))
        self.dump.assert_called_once()

    def test_conto_senza_profilo_eliminabile(self):
        self.session.delete(self.profile)
        self.session.commit()
        delete_liability(self.account_id,
                         LiabilityDeletePayload(account_name=self.account.name), self.session)
        self.assertIsNone(self.session.get(Account, self.account_id))
        self.dump.assert_called_once()

    def test_patrimonio_non_scavalca_conferma_o_backup(self):
        with self.assertRaises(HTTPException) as error:
            delete_account(self.account_id, self.session)
        self.assertEqual(409, error.exception.status_code)
        self.dump.assert_not_called()
        delete_account(self.account_id, self.session, confirm_liability_account=self.account.name)
        self.dump.assert_called_once()
