"""I giunti fra database e motore FIRE.

Il motore e' provato a parte e i dati sono provati altrove: quello che qui puo'
rompersi sono i **bordi**, ed e' il punto dove un errore non da' un'eccezione
ma un numero plausibile e falso.

Tre bordi, tre rischi:

- **le unita'**: il database salva percentuali (4,00), il motore vuole frazioni
  (0,04). Un fattore cento su un rendimento atteso non si vede a occhio e su
  trent'anni cambia tutto;
- **il vocabolario**: un tipo di flusso non riconosciuto verrebbe trattato come
  uno dei due, e un capitale scambiato per una rendita sposta il piano di anni;
- **quali spese**: l'anno in corso e' parziale, e includerlo farebbe sembrare
  l'obiettivo piu' basso di quello che e'.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from unittest.mock import patch

from app.fire_routes import (_anni_di_riferimento, _frazione, _spese_di_riferimento, _spese_in_pensione,
                             _storico_patrimonio, _versamenti, elenco_flussi, leggi_regole, salva_regole,
                             spostamento_pensioni, crea_flusso, modifica_flusso, RegolaPayload, RegolePayload,
                             flusso_da_riga, fire, leggi_profilo, salva_profilo,
                             FlussoPayload, ProfiloPayload)
from app.models import IncomeStream, RetirementProfile


class UnitaTests(unittest.TestCase):
    def test_le_percentuali_diventano_frazioni(self) -> None:
        # 4% salvato deve arrivare al motore come 0,04. Cento volte tanto
        # sarebbe un rendimento del 400% l'anno, e il piano direbbe che sei
        # gia' arrivato.
        self.assertEqual(Decimal("0.04"), _frazione(Decimal("4")))
        self.assertEqual(Decimal("0.035"), _frazione(Decimal("3.5")))
        self.assertEqual(Decimal("0"), _frazione(None))
        self.assertEqual(Decimal("0.04"), _frazione(None, "4"))


class VocabolarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_i_due_tipi_attraversano_il_confine(self) -> None:
        for kind in ("annuity", "capital"):
            riga = IncomeStream(name="x", kind=kind, amount=Decimal("100"), start_age=65)
            self.assertEqual(kind, flusso_da_riga(riga).tipo)

    def test_un_tipo_sconosciuto_viene_rifiutato(self) -> None:
        # Non deve "cadere" su uno dei due: un capitale scambiato per rendita
        # sposta la data di arrivo di anni senza che nulla lo segnali.
        riga = IncomeStream(name="x", kind="pensione", amount=Decimal("100"), start_age=65)
        with self.assertRaises(HTTPException) as errore:
            flusso_da_riga(riga)
        self.assertEqual("fireUnknownStreamKind", errore.exception.detail)

    def test_la_stima_a_contribuzione_ferma_attraversa(self) -> None:
        riga = IncomeStream(name="AVS", kind="annuity", amount=Decimal("12000"), start_age=65,
                            amount_if_stopping_now=Decimal("4000"))
        self.assertEqual(Decimal("4000"), flusso_da_riga(riga).importo_se_smetti_oggi)


class SpeseDiRiferimentoTests(unittest.TestCase):
    STORICHE = {2023: 12000.0, 2024: 13654.0, 2025: 19932.0}

    def test_ultimo_anno(self) -> None:
        self.assertEqual(Decimal("19932"), _spese_di_riferimento(self.STORICHE, "last_year", None))

    def test_media(self) -> None:
        atteso = Decimal(str((12000 + 13654 + 19932) / 3))
        self.assertEqual(atteso, _spese_di_riferimento(self.STORICHE, "average", None))

    def test_mediana(self) -> None:
        self.assertEqual(Decimal("13654"), _spese_di_riferimento(self.STORICHE, "median", None))

    def test_personalizzate(self) -> None:
        self.assertEqual(Decimal("16000"),
                         _spese_di_riferimento(self.STORICHE, "custom", Decimal("16000")))

    def test_senza_storia_non_inventa_un_numero(self) -> None:
        self.assertEqual(Decimal("0"), _spese_di_riferimento({}, "average", None))


class SenzaProfiloTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()

    def test_senza_profilo_non_si_inventa_un_piano(self) -> None:
        # Meglio chiedere di configurarlo che mostrare numeri costruiti su
        # ipotesi che nessuno ha dichiarato.
        esito = fire(self.session)
        self.assertFalse(esito["configured"])
        self.assertIsNone(esito["plan"])

    def test_il_profilo_si_salva_e_si_rilegge(self) -> None:
        salva_profilo(ProfiloPayload(
            birth_year=1998, country="CH", target_retirement_age=50,
            real_return=4.5, withdrawal_rate=3.5, withdrawal_tax_rate=26,
            expense_basis="median"), self.session)
        riga = self.session.scalars(select(RetirementProfile)).one()
        self.assertEqual((1998, "CH", 50, Decimal("4.50"), Decimal("3.50"), Decimal("26.00"), "median"),
                         (riga.birth_year, riga.country, riga.target_retirement_age,
                          riga.real_return, riga.withdrawal_rate, riga.withdrawal_tax_rate,
                          riga.expense_basis))

    def test_quello_che_la_pagina_legge_lo_puo_rimandare_indietro(self) -> None:
        # Le risposte sono in camelCase, le richieste in snake_case: chi
        # rilegge il profilo e lo rispedisce tale e quale - la cosa piu'
        # naturale da fare - riceveva un 422 senza capire perche'.
        salva_profilo(ProfiloPayload(
            birth_year=1998, country="CH", target_retirement_age=50,
            real_return=4.5, withdrawal_rate=3.5, withdrawal_tax_rate=26,
            expense_basis="median"), self.session)
        letto = leggi_profilo(self.session)
        assert letto is not None
        salva_profilo(ProfiloPayload(**letto), self.session)
        self.assertEqual(letto, leggi_profilo(self.session))

    def test_i_flussi_si_rileggono_con_la_chiave_che_la_pagina_cerca(self) -> None:
        # Senza questa lettura la pagina non aveva niente da mostrare, e la
        # chiave sbagliata equivale a non averla.
        self.session.add(IncomeStream(name="AVS", kind="annuity", amount=Decimal("12000"),
                                      start_age=65, country="CH"))
        self.session.commit()
        elenco = elenco_flussi(self.session)
        self.assertEqual(["AVS"], [f["name"] for f in elenco["streams"]])
        FlussoPayload(**{k: v for k, v in elenco["streams"][0].items() if k != "id"})

    def test_una_base_di_spesa_inventata_viene_rifiutata(self) -> None:
        with self.assertRaises(HTTPException):
            salva_profilo(ProfiloPayload(
                birth_year=1998, country="IT", target_retirement_age=60,
                real_return=4, withdrawal_rate=4, withdrawal_tax_rate=0,
                expense_basis="a_occhio"), self.session)


class PianoCompletoTests(unittest.TestCase):
    """Il percorso con profilo configurato, che nessun altro test toccava.

    E' il buco che ha lasciato passare un errore banale: la risposta leggeva
    `Fase.nome`, un campo che non esiste - i nomi li avevo indovinati invece di
    leggerli. Non e' un caso limite: e' *il* percorso della pagina.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        salva_profilo(ProfiloPayload(
            birth_year=date.today().year - 28, country="CH", target_retirement_age=50,
            real_return=4, withdrawal_rate=4, withdrawal_tax_rate=26,
            expense_basis="custom", custom_annual_expenses=15000), self.session)
        self.session.add_all([
            IncomeStream(name="AVS", kind="annuity", amount=Decimal("12000"), start_age=65, country="CH"),
            IncomeStream(name="INPS", kind="annuity", amount=Decimal("5000"), start_age=67, country="IT"),
            IncomeStream(name="LPP", kind="capital", amount=Decimal("150000"), start_age=65, country="CH"),
        ])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_la_risposta_ha_la_forma_che_la_pagina_si_aspetta(self) -> None:
        piano = fire(self.session)["plan"]
        for campo in ("capitalNeeded", "bridgeCapital", "topUpCapital", "phases",
                      "series", "milestones", "warnings", "regimeAge"):
            self.assertIn(campo, piano, f"manca {campo}")
        self.assertEqual(["accumulo", "ponte", "pensione"], [f["kind"] for f in piano["phases"]])
        self.assertTrue(piano["series"], "la serie non puo' essere vuota")
        for punto in piano["series"][:1]:
            for campo in ("age", "capital", "capitalNeeded", "shortfall"):
                self.assertIn(campo, punto, f"manca {campo} nella serie")

    def test_le_pensioni_che_coprono_le_spese_azzerano_il_rabbocco(self) -> None:
        # AVS 12.000 + INPS 5.000 = 17.000 contro 15.000 di spese: dopo l'eta'
        # di regime il capitale non deve piu' finanziare niente. E' il motivo
        # per cui in Europa il numero e' molto piu' basso della regola del 4%.
        piano = fire(self.session)["plan"]
        self.assertEqual(0, piano["topUpCapital"])
        self.assertEqual(piano["capitalNeeded"], piano["bridgeCapital"])

    def test_le_avvertenze_arrivano_alla_pagina(self) -> None:
        # Il motore dichiara che e' uno scenario e non una previsione: quel
        # messaggio deve poter arrivare a chi guarda, non restare nel backend.
        self.assertIn("scenario_non_previsione", fire(self.session)["plan"]["warnings"])

    def test_un_flusso_duplicato_non_diventa_un_errore_del_server(self) -> None:
        # Il motore li rifiuta, giustamente. Ma deve uscirne un 422 con un
        # codice leggibile, non un 500 con un traceback.
        self.session.add(IncomeStream(name="AVS", kind="annuity", amount=Decimal("12000"),
                                      start_age=65, country="CH"))
        self.session.commit()
        with self.assertRaises(HTTPException) as errore:
            fire(self.session)
        self.assertEqual(422, errore.exception.status_code)
        self.assertTrue(str(errore.exception.detail).startswith("fireEngine_"))


class RisparmiEProiezioneTests(unittest.TestCase):
    """Quello che la pagina mostrava in contraddizione con se stessa.

    Il piano ignorava i risparmi futuri e diceva 29 anni; la tabella della leva,
    calcolata a parte nel browser, per lo stesso tasso ne diceva 24.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        salva_profilo(ProfiloPayload(
            birth_year=date.today().year - 28, country="CH", target_retirement_age=65,
            real_return=4, withdrawal_rate=4, withdrawal_tax_rate=0,
            expense_basis="custom", custom_annual_expenses=15000), self.session)

    def tearDown(self) -> None:
        self.session.close()

    def _fire(self, tassi):
        with patch("app.fire_routes._tassi_storici", return_value=tassi):
            return fire(self.session)

    def test_il_risparmio_si_ricava_da_spese_e_tasso(self) -> None:
        # 40% di risparmio con 15.000 di spese: reddito 25.000, risparmio 10.000.
        self.assertEqual(Decimal("10000.00"), _versamenti(Decimal("15000"), 40))
        self.assertEqual(Decimal("0.00"), _versamenti(Decimal("15000"), -12))

    def test_chi_risparmia_arriva_prima_di_chi_non_risparmia(self) -> None:
        # Patrimonio zero: senza versare non ci si arriva mai.
        self.assertIsNone(self._fire({})["plan"]["yearsLeft"])
        self.assertIsNotNone(self._fire({2025: 40.0})["plan"]["yearsLeft"])

    def test_la_riga_della_leva_dice_gli_stessi_anni_del_piano(self) -> None:
        esito = self._fire({2024: 30.0, 2025: 40.0})
        tua = [r for r in esito["plan"]["leverage"] if r["current"]]
        self.assertEqual(1, len(tua))
        self.assertEqual(35.0, tua[0]["savingsRate"])
        self.assertEqual(esito["plan"]["yearsLeft"], tua[0]["yearsLeft"])
        self.assertEqual(esito["annualSavings"], tua[0]["annualSavings"])

    def test_la_proiezione_parte_dal_patrimonio_di_oggi(self) -> None:
        # Il capitale di fine anno etichettato con l'anno in corso staccava la
        # curva dalla storia.
        esito = self._fire({2025: 40.0})
        self.assertEqual(esito["netWorth"], esito["plan"]["series"][0]["capital"])

    def test_la_proiezione_disegnata_si_ferma_a_90_anni(self) -> None:
        self.assertEqual(90, self._fire({})["plan"]["series"][-1]["age"])

    def test_le_spese_lean_arrivano_al_traguardo(self) -> None:
        riga = self.session.scalars(select(RetirementProfile)).one()
        riga.lean_annual_expenses = Decimal("10000")
        self.session.commit()
        traguardi = self._fire({})["plan"]["milestones"]
        self.assertLess(traguardi["lean"]["capitalNeeded"], traguardi["fi"]["capitalNeeded"])

    def test_spese_lean_sopra_quelle_di_riferimento_non_rompono_la_pagina(self) -> None:
        # Le spese di riferimento cambiano con la storia: un profilo salvato
        # ieri non deve diventare un 422 oggi.
        riga = self.session.scalars(select(RetirementProfile)).one()
        riga.lean_annual_expenses = Decimal("20000")
        self.session.commit()
        piano = self._fire({})["plan"]
        traguardi = piano["milestones"]
        self.assertEqual(traguardi["fi"]["capitalNeeded"], traguardi["lean"]["capitalNeeded"])
        # ...ma lo dice: due numeri uguali senza spiegazione sembrano un errore.
        self.assertTrue(piano["leanCapped"])


class StoriaTests(unittest.TestCase):
    def test_il_saldo_iniziale_non_diventa_dieci_anni_di_storia(self) -> None:
        serie = ([{"period": f"{anno}-12", "netWorth": 50000.0} for anno in range(2016, 2022)]
                 + [{"period": "2022-05", "netWorth": 50000.0},
                    {"period": "2022-12", "netWorth": 60000.0},
                    {"period": "2023-12", "netWorth": 90000.0}])
        with patch("app.fire_routes.net_worth_series", return_value=serie):
            storia = _storico_patrimonio(None, date(2024, 1, 1))
        self.assertEqual([2022, 2023], [p["year"] for p in storia])


class SpeseInPensioneTests(unittest.TestCase):
    """Le regole per categoria: quello che cambia quando si smette di lavorare."""

    STORICHE = {2023: 9799.0, 2024: 13654.0, 2025: 19932.0}

    def test_la_mediana_e_un_anno_preciso(self) -> None:
        # E' questo che fa sommare le categorie esattamente al totale.
        self.assertEqual([2024], _anni_di_riferimento(self.STORICHE, "median"))
        self.assertEqual([2025], _anni_di_riferimento(self.STORICHE, "last_year"))
        self.assertEqual(3, len(_anni_di_riferimento(self.STORICHE, "average")))

    def test_sparisce_toglie_e_cambia_sostituisce(self) -> None:
        categorie = {"Affitto": 9000.0, "Palestra": 600.0}
        regole = [{"category": "Affitto", "mode": "change", "amount": 3000},
                  {"category": "Palestra", "mode": "drop", "amount": None}]
        self.assertEqual(Decimal("5400"), _spese_in_pensione(Decimal("12000"), categorie, regole))

    def test_una_regola_su_una_categoria_sparita_non_conta(self) -> None:
        regole = [{"category": "Mutuo", "mode": "drop", "amount": None}]
        self.assertEqual(Decimal("12000"), _spese_in_pensione(Decimal("12000"), {"Affitto": 9000.0}, regole))

    def test_le_spese_non_scendono_sotto_zero(self) -> None:
        regole = [{"category": "Affitto", "mode": "change", "amount": 0}]
        self.assertEqual(Decimal("0"), _spese_in_pensione(Decimal("1000"), {"Affitto": 9000.0}, regole))


class RegoleEPensioniSpostateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        salva_profilo(ProfiloPayload(
            birth_year=date.today().year - 28, country="CH", target_retirement_age=55,
            real_return=4, withdrawal_rate=4, withdrawal_tax_rate=0,
            expense_basis="average"), self.session)
        self.categorie = {"Affitto": 9000.0, "Palestra": 1000.0}
        self.storiche = {2025: 10000.0}

    def tearDown(self) -> None:
        self.session.close()

    def _con_storia(self):
        return (patch("app.fire_routes._spese_storiche", return_value=self.storiche),
                patch("app.fire_routes._spese_per_categoria", return_value=self.categorie))

    def test_le_regole_si_salvano_e_abbassano_l_obiettivo(self) -> None:
        a, b = self._con_storia()
        with a, b:
            prima = fire(self.session)["plan"]["capitalNeeded"]
            esito = salva_regole(RegolePayload(rules=[
                RegolaPayload(category="Palestra", mode="drop"),
                RegolaPayload(category="Affitto", mode="stay")]), self.session)
            dopo = fire(self.session)
        self.assertEqual(9000.0, esito["retirementExpenses"])
        self.assertEqual({"Palestra": "drop", "Affitto": "stay"},
                         {c["category"]: c["mode"] for c in esito["categories"]})
        self.assertEqual(9000.0, dopo["retirementExpenses"])
        # Il risparmio si misura sulle spese di oggi, l'obiettivo su quelle future.
        self.assertEqual(10000.0, dopo["expensesUsed"])
        self.assertLess(dopo["plan"]["capitalNeeded"], prima)

    def test_con_spese_personalizzate_le_regole_non_si_applicano(self) -> None:
        riga = self.session.scalars(select(RetirementProfile)).one()
        riga.expense_basis, riga.custom_annual_expenses = "custom", Decimal("30000")
        riga.expense_rules = '[{"category": "Affitto", "mode": "drop", "amount": null}]'
        self.session.commit()
        a, b = self._con_storia()
        with a, b:
            esito = fire(self.session)
            regole = leggi_regole(self.session)
        self.assertEqual(30000.0, esito["retirementExpenses"])
        self.assertFalse(regole["applies"])

    def test_una_regola_cambia_senza_importo_viene_rifiutata(self) -> None:
        with self.assertRaises(HTTPException):
            salva_regole(RegolePayload(rules=[RegolaPayload(category="Affitto", mode="change")]), self.session)

    def test_le_pensioni_piu_tardi_chiedono_piu_capitale(self) -> None:
        self.session.add(IncomeStream(name="AVS", kind="annuity", amount=Decimal("8000"),
                                      start_age=65, country="CH"))
        self.session.commit()
        a, b = self._con_storia()
        with a, b:
            esito = spostamento_pensioni(self.session)
        per_anni = {r["shift"]: r["capitalNeeded"] for r in esito["results"]}
        self.assertTrue(esito["hasStreams"])
        self.assertEqual(list(range(-5, 6)), sorted(per_anni))
        self.assertLess(per_anni[-5], per_anni[0])
        self.assertLess(per_anni[0], per_anni[5])

    def test_senza_profilo_le_regole_si_leggono_vuote_e_non_si_salvano(self) -> None:
        # Leggere senza profilo e' uno stato normale (prima visita), salvare no.
        self.session.delete(self.session.scalars(select(RetirementProfile)).one())
        self.session.commit()
        self.assertEqual({"configured": False, "categories": []},
                         {k: v for k, v in leggi_regole(self.session).items() if k in ("configured", "categories")})
        with self.assertRaises(HTTPException) as errore:
            salva_regole(RegolePayload(rules=[]), self.session)
        self.assertEqual(409, errore.exception.status_code)


class FlussiCheRompevanoLaPaginaTests(unittest.TestCase):
    """I4: flussi che il salvataggio accettava e la pagina FIRE poi rifiutava.

    L'errore compariva all'apertura della pagina, lontano dal modulo che
    l'aveva causato. Ora o il piano regge, o non si salva.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        salva_profilo(ProfiloPayload(
            birth_year=date.today().year - 28, country="CH", target_retirement_age=60,
            real_return=4, withdrawal_rate=4, withdrawal_tax_rate=0,
            expense_basis="custom", custom_annual_expenses=30000, inflation=1.5), self.session)

    def tearDown(self) -> None:
        self.session.close()

    def _flussi(self):
        return [r.name for r in self.session.scalars(select(IncomeStream)).all()]

    def test_una_lpp_non_indicizzata_non_rompe_la_pagina(self) -> None:
        crea_flusso(FlussoPayload(name="LPP", kind="annuity", amount=20000, start_age=65,
                                  indexed=False), self.session)
        piano = fire(self.session)["plan"]
        self.assertIn("rendite_non_indicizzate_escluse_dopo_regime", piano["warnings"])

    def test_l_inflazione_si_salva_e_si_rilegge(self) -> None:
        self.assertEqual(1.5, leggi_profilo(self.session)["inflation"])

    def test_un_nome_doppio_non_si_salva(self) -> None:
        crea_flusso(FlussoPayload(name="AVS", amount=12000, start_age=65), self.session)
        with self.assertRaises(HTTPException) as errore:
            crea_flusso(FlussoPayload(name="AVS", amount=5000, start_age=67), self.session)
        self.assertEqual(409, errore.exception.status_code)
        self.assertEqual(["AVS"], self._flussi())

    def test_una_stima_anticipata_su_un_capitale_non_si_salva(self) -> None:
        with self.assertRaises(HTTPException) as errore:
            crea_flusso(FlussoPayload(name="LPP", kind="capital", amount=150000, start_age=65,
                                      amount_if_stopping_now=90000), self.session)
        self.assertEqual("fireEngine_stima_solo_per_rendita", errore.exception.detail)
        self.assertEqual([], self._flussi())

    def test_una_modifica_che_rompe_il_piano_non_tocca_la_riga(self) -> None:
        riga = crea_flusso(FlussoPayload(name="INPS", amount=6000, start_age=67,
                                         amount_if_stopping_now=3000), self.session)
        with self.assertRaises(HTTPException):
            # Stima "se smetti oggi" piu' alta di quella "se continui": invertite.
            modifica_flusso(riga["id"], FlussoPayload(name="INPS", amount=6000, start_age=67,
                                                      amount_if_stopping_now=9000), self.session)
        self.assertEqual(Decimal("3000"), self.session.get(IncomeStream, riga["id"]).amount_if_stopping_now)
