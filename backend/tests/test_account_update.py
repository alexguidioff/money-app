"""Modificare un conto dall'app deve salvare davvero.

Prima la PATCH riceveva `starting_balance` e non lo assegnava mai, e nessuno
dal frontend la chiamava: l'unico modo di correggere il saldo iniziale di un
conto era una UPDATE a mano sul database.

L'altra meta' del test e' la riga che non deve muoversi: `current_balance` e' il
saldo dichiarato, e la riconciliazione confronta `starting + movimenti` contro
di lui. Riallinearlo in automatico farebbe sparire proprio la differenza che
l'utente sta cercando di capire.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import AccountPayload, update_account
from app.models import Account


class ModificaContoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine, tables=[Account.__table__])
        self.session = Session(self.engine)
        self.conto = Account(source_group="bank", name="Banca", starting_balance=Decimal("100.00"),
                             current_balance=Decimal("250.00"), counts_in_net_worth=True, is_active=True)
        self.session.add(self.conto)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def _salva(self, **campi) -> Account:
        base = {"name": "Banca", "source_group": "bank", "starting_balance": 100.0,
                "counts_in_net_worth": True, "is_active": True}
        update_account(self.conto.id, AccountPayload(**{**base, **campi}), self.session)
        self.session.refresh(self.conto)
        return self.conto

    def test_salva_il_saldo_iniziale(self) -> None:
        self.assertEqual(Decimal("-14234.50"), self._salva(starting_balance=-14234.5).starting_balance)

    def test_non_tocca_il_saldo_dichiarato(self) -> None:
        # Cambiare lo starting non deve riallineare current_balance: la
        # differenza fra i due e' l'informazione, non il difetto.
        self.assertEqual(Decimal("250.00"), self._salva(starting_balance=999.0).current_balance)

    def test_archivia_e_riattiva(self) -> None:
        self.assertFalse(self._salva(is_active=False).is_active)
        self.assertTrue(self._salva(is_active=True).is_active)

    def test_fuori_dal_patrimonio(self) -> None:
        self.assertFalse(self._salva(counts_in_net_worth=False).counts_in_net_worth)

    def test_valutazione_manuale_solo_per_le_attivita(self) -> None:
        # Un conto corrente e un debito hanno un saldo che i movimenti sanno
        # gia' calcolare: la domanda non ha senso, e la risposta non si salva.
        self.assertFalse(self._salva(source_group="bank", needs_manual_valuation=True).needs_manual_valuation)
        self.assertFalse(self._salva(source_group="liability", needs_manual_valuation=True).needs_manual_valuation)
        self.assertTrue(self._salva(source_group="asset", needs_manual_valuation=True).needs_manual_valuation)
        self.assertFalse(self._salva(source_group="liability", needs_manual_valuation=True).needs_manual_valuation)

    def test_cambiare_gruppo_spegne_la_valutazione_manuale(self) -> None:
        # Altrimenti resterebbe invisibile nel form e attivo nei calcoli.
        self._salva(source_group="asset", needs_manual_valuation=True)
        self.assertFalse(self._salva(source_group="bank", needs_manual_valuation=True).needs_manual_valuation)

    def test_la_risposta_dice_se_e_attivo(self) -> None:
        # Il frontend riceve questo oggetto dopo il salvataggio: senza isActive
        # la riga tornerebbe a mostrarsi come attiva fino al ricaricamento.
        risposta = update_account(self.conto.id, AccountPayload(
            name="Banca", source_group="bank", starting_balance=100.0, is_active=False), self.session)
        self.assertIn("isActive", risposta)
        self.assertFalse(risposta["isActive"])

    def test_la_nota_viene_salvata_e_restituita(self) -> None:
        risposta = update_account(self.conto.id, AccountPayload(
            name="Banca", source_group="bank", starting_balance=100.0,
            notes="Saldo storico da verificare"), self.session)
        self.assertEqual(risposta["notes"], "Saldo storico da verificare")


if __name__ == "__main__":
    unittest.main()
