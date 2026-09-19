"""Le quattro leve del piano: una sola strada per calcolarle.

La regola e' quella che la tabella del risparmio ha gia' pagata una volta - una
formula scritta a parte nel browser diceva 24 anni dove il piano ne diceva 29 -
estesa alle altre tre leve: ogni riga si ottiene richiamando il motore vero con
un parametro cambiato. Il primo test e' quello che la difende: la riga
dell'utente deve *essere* il piano, non un calcolo parallelo che gli somiglia.

I numeri sono tondi e inventati: questo repository e' pubblico e i valori veri
di chi usa l'app non ci entrano.
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.fire_routes import LEVE, ProfiloPayload, _contesto, _leve, _piano, salva_profilo
from app.models import IncomeStream, RetirementProfile

# Il tasso di risparmio non si sceglie con un cursore: lo dicono gli anni
# conclusi. Qui sono inventati, ed e' l'unico ingresso che il test finge.
TASSI = {2025: 40.0}


class LeveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        salva_profilo(ProfiloPayload(
            birth_year=date.today().year - 40, country="CH", target_retirement_age=65,
            real_return=5, withdrawal_rate=4, withdrawal_tax_rate=0,
            expense_basis="custom", custom_annual_expenses=20000), self.session)
        self.session.add(IncomeStream(name="Pensione", kind="annuity", amount=Decimal("12000"),
                                      start_age=67, country="CH"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def contesto(self, tassi=None):
        with patch("app.fire_routes._tassi_storici", return_value=TASSI if tassi is None else tassi):
            return _contesto(self.session, self.session.scalar(select(RetirementProfile)))

    def leve(self, tassi=None):
        c = self.contesto(tassi)
        piano = _piano(c)
        return c, piano, {leva["key"]: leva["rows"] for leva in _leve(c, piano)}

    def corrente(self, righe):
        return [riga for riga in righe if riga["current"]]

    def test_1_la_riga_dell_utente_e_il_piano(self) -> None:
        """Per tutte e quattro le leve, non solo per il risparmio.

        E' il test che impedisce alle leve di diventare un secondo calcolo: se
        una riga dell'utente venisse da una formula approssimata, questo numero
        non tornerebbe, e non tornerebbe *subito*, non fra un anno.
        """
        c, piano, leve = self.leve()
        atteso = {
            "savingsRate": Decimal(str(c.tasso)),
            "return": Decimal(c.profilo.real_return),
            "retirementAge": Decimal(max(c.profilo.target_retirement_age, c.eta)),
            "retirementExpenses": c.spese_pensione,
        }
        self.assertEqual(set(atteso), set(leve), "una leva e' sparita dalla risposta")
        for chiave, righe in leve.items():
            tua = self.corrente(righe)
            self.assertEqual(1, len(tua), chiave)
            self.assertEqual(float(atteso[chiave]), tua[0]["value"], chiave)
            self.assertEqual(piano.anni_mancanti, tua[0]["yearsLeft"], chiave)
            self.assertEqual(float(piano.capitale_necessario), tua[0]["capitalNeeded"], chiave)

    def test_2_piu_rendimento_meno_anni(self) -> None:
        """Il rendimento accorcia la strada, e non la allunga mai."""
        _, _, leve = self.leve()
        righe = leve["return"]
        anni = [riga["yearsLeft"] for riga in righe]
        self.assertLess(righe[0]["value"], righe[-1]["value"])
        self.assertTrue(all(anno is not None for anno in anni), righe)
        self.assertEqual(sorted(anni, reverse=True), anni, righe)

    def test_3_ritirarsi_piu_tardi_costa_meno(self) -> None:
        """Gli anni da coprire sono di meno: il capitale necessario scende."""
        _, _, leve = self.leve()
        righe = leve["retirementAge"]
        self.assertLess(righe[0]["value"], righe[-1]["value"])
        self.assertEqual(sorted((riga["capitalNeeded"] for riga in righe), reverse=True),
                         [riga["capitalNeeded"] for riga in righe], righe)

    def test_4_spendere_meno_costa_meno_e_arriva_prima(self) -> None:
        """Due cose insieme: cambia il traguardo, non solo la strada.

        E' la ragione per cui ogni riga dice anche il capitale necessario:
        solo gli anni nasconderebbero che il traguardo si e' spostato.
        """
        _, _, leve = self.leve()
        righe = leve["retirementExpenses"]
        capitali = [riga["capitalNeeded"] for riga in righe]
        anni = [riga["yearsLeft"] for riga in righe]
        self.assertLess(righe[0]["value"], righe[-1]["value"])
        self.assertEqual(sorted(capitali), capitali, righe)
        self.assertTrue(all(anno is not None for anno in anni), righe)
        self.assertEqual(sorted(anni), anni, righe)
        self.assertLess(capitali[0], capitali[-1])

    def test_5_chi_non_arriva_non_ha_un_numero(self) -> None:
        """Chi non arriva nell'orizzonte non ha un anno: `None`, non un numero grande.

        Un numero grande sarebbe la solita stima inventata: la pagina scrive
        "mai, in questa proiezione", che e' quello che il motore ha detto. Il
        capitale necessario invece c'e' lo stesso: dice quanto mancherebbe, ed
        e' l'altra cosa che una riga deve dire.
        """
        # Niente rendite: con una pensione in arrivo il piano si chiude
        # comunque, e il caso da provare e' proprio quello che non si chiude.
        self.session.query(IncomeStream).delete()
        self.session.commit()
        c, piano, leve = self.leve(tassi={})
        self.assertEqual(0.0, c.tasso)
        self.assertIsNone(piano.anni_mancanti)
        tua = self.corrente(leve["savingsRate"])[0]
        self.assertIsNone(tua["yearsLeft"], tua)
        self.assertGreater(tua["capitalNeeded"], 0.0)

    def test_6_cinque_righe_con_la_tua_in_mezzo(self) -> None:
        """Cinque righe per leva, e quella dell'utente dove sta lui.

        In mezzo quando il suo valore e' dentro la griglia, al bordo quando e'
        fuori: si mostra la griglia che c'e', non si inventano valori per farlo
        stare al centro.
        """
        _, _, leve = self.leve()
        for chiave, righe in leve.items():
            self.assertEqual(5, len(righe), chiave)
            self.assertEqual(2, [riga["current"] for riga in righe].index(True), chiave)
        # Un tasso al bordo della griglia: la riga dell'utente e' la prima.
        _, _, basso = self.leve(tassi={2025: 5.0})
        righe = basso["savingsRate"]
        self.assertEqual(5, len(righe))
        self.assertEqual(0, [riga["current"] for riga in righe].index(True))
        self.assertEqual(5.0, righe[0]["value"])

    def test_7_il_risparmio_lo_mostra_solo_la_leva_che_lo_muove(self) -> None:
        """Sulle altre tre sarebbe lo stesso numero cinque volte."""
        _, _, leve = self.leve()
        self.assertTrue(all(riga["annualSavings"] is not None for riga in leve["savingsRate"]))
        for chiave in ("return", "retirementAge", "retirementExpenses"):
            self.assertTrue(all(riga["annualSavings"] is None for riga in leve[chiave]), chiave)

    def test_8_le_chiavi_sono_quelle_della_risposta(self) -> None:
        """Le chiavi le legge il browser: rinominarle e' cambiare il contratto."""
        self.assertEqual(["savingsRate", "return", "retirementAge", "retirementExpenses"],
                         [leva.chiave for leva in LEVE])

    def test_9_senza_flussi_l_eta_di_ritiro_non_muove_niente(self) -> None:
        """Il caso che la pagina deve saper dire invece di mostrare.

        Senza un flusso che parte dopo il ritiro il capitale necessario e' la
        riserva perpetua - spese diviso prelievo - e gli anni da coprire non
        entrano nel conto; gli anni al piano, dal canto loro, l'eta' di ritiro
        non la leggono affatto. Le cinque righe escono uguali in entrambe le
        colonne, ed e' la condizione su cui il browser smette di disegnare la
        tabella e scrive cosa la farebbe muovere.

        E' un test che fissa un fatto, non un desiderio: se un giorno il motore
        cambia, questo cade e quella riga a video va riletta.
        """
        # Niente rendite: e' il caso di chi il profilo non l'ha ancora
        # completato, che e' anche quello da cui arriva la segnalazione.
        self.session.query(IncomeStream).delete()
        self.session.commit()
        _, _, leve = self.leve()
        righe = leve["retirementAge"]
        self.assertEqual(5, len(righe))
        self.assertEqual(sorted(riga["value"] for riga in righe), [riga["value"] for riga in righe])
        self.assertEqual(1, len({riga["capitalNeeded"] for riga in righe}), righe)
        self.assertEqual(1, len({riga["yearsLeft"] for riga in righe}), righe)
        # E non e' un caso di "non arriva nessuno": gli anni ci sono, sono
        # soltanto gli stessi.
        self.assertIsNotNone(righe[0]["yearsLeft"])
