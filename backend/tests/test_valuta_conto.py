"""La valuta di un conto: chi la sceglie, chi la rifiuta, chi la conta.

Il saldo di un conto in franchi e' in franchi, e sommarlo a uno in euro senza
convertirlo da' un numero che non vuol dire niente: la colonna esiste per
questo. Il valore di partenza e' l'euro perche' e' quello che erano i conti
prima che la colonna ci fosse, e un campo nuovo non deve cambiare il
significato di un saldo che nessuno ha toccato.

Il codice si controlla all'ingresso: tre lettere o niente. Una sigla che nessuno
sa leggere non da' un errore, da' un saldo convertito a un cambio che non
esiste, ed e' il modo peggiore di sbagliare: nessuno se ne accorge.
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core_routes import account_currencies
from app.database import Base
from app.main import AccountPayload, create_account, update_account
from app.models import Account


class ValutaDelContoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[Account.__table__])
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def _crea(self, nome: str = "Banca", **campi) -> dict:
        base = {"source_group": "bank", "starting_balance": 0}
        return create_account(AccountPayload(name=nome, **{**base, **campi}), self.session)

    def _conto(self, nome: str) -> Account:
        return self.session.scalars(select(Account).where(Account.name == nome)).one()

    def test_senza_valuta_resta_euro(self) -> None:
        risposta = self._crea()
        self.assertEqual("EUR", risposta["currency"])
        self.assertEqual("EUR", self._conto("Banca").currency)

    def test_la_valuta_si_salva_in_maiuscolo(self) -> None:
        risposta = self._crea("Franchi", currency=" chf ")
        self.assertEqual("CHF", risposta["currency"])
        self.assertEqual("CHF", self._conto("Franchi").currency)

    def test_una_sigla_che_non_e_una_valuta_si_rifiuta(self) -> None:
        for codice in ("Franchi", "EURO", "EU", "12"):
            with self.subTest(codice=codice), self.assertRaises(HTTPException) as errore:
                self._crea(currency=codice)
            self.assertEqual(422, errore.exception.status_code)
            self.assertEqual("accountCurrencyInvalid", errore.exception.detail)
        # Il rifiuto arriva prima della scrittura: un conto a meta' non resta.
        self.assertIsNone(self.session.scalar(select(Account).where(Account.name == "Banca")))

    def test_cambiare_valuta_si_salva_e_la_risposta_lo_dice(self) -> None:
        conto = self._crea()
        risposta = update_account(conto["id"], AccountPayload(
            name="Banca", source_group="bank", starting_balance=0, currency="chf"), self.session)
        self.assertEqual("CHF", risposta["currency"])
        self.assertEqual("CHF", self._conto("Banca").currency)

    def test_le_valute_dei_conti_lasciano_fuori_l_euro(self) -> None:
        # L'euro e' la valuta di partenza: chiedere il suo storico di cambi
        # sarebbe una richiesta di rete per un cambio che vale 1 per definizione.
        self._crea("Euro")
        self._crea("Franchi", currency="chf")
        self._crea("Dollari", currency="USD")
        self.assertEqual(["CHF", "USD"], account_currencies(self.session))


if __name__ == "__main__":
    unittest.main()
