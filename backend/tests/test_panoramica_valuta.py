"""La Panoramica in un'altra valuta: gli stessi numeri, in un'altra moneta.

I numeri della Panoramica si contano in euro - ogni movimento al cambio del suo
giorno, che e' come l'app legge le pagine che sommano conti diversi - e si
portano nella valuta chiesta moltiplicando i totali per il cambio di fine
periodo. Il primo test fissa la promessa su cui sta il resto: chi non chiede
niente, o chiede l'euro, riceve il dizionario di prima, chiave per chiave.

Il quarto test e' quello che conta davvero: confronta il percorso di ogni foglia
che cambia fra la lettura in euro e quella in franchi con un elenco scritto a
mano. E' l'unico modo di accorgersi se un campo monetario nuovo entra in questa
risposta e nessuno lo mette in `CAMPI_MONETARI` - un numero in euro in mezzo a
numeri in franchi, che si legge come se fosse giusto.

I cambi e i numeri sono inventati e tondi: questo repository e' pubblico.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any, Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import (BASE_CURRENCY, CAMPI_MONETARI, budget_currencies, display_currencies,
                             fine_periodo, summary, summary_breakdown)
from app.database import Base
from app.models import Account, AppSetting, BudgetPlan, MarketPrice, Transaction
from tests.categorie_fixture import categoria


def _cambio(session: Session, codice: str, giorno: date, valore: str) -> None:
    """Un cambio nel listino, come lo scriverebbe lo scarico delle quotazioni."""
    session.add(MarketPrice(symbol=f"EUR{codice}=X", observed_on=giorno, price=Decimal(valore)))


def _foglie(payload: Any, percorso: str = "") -> Iterator[tuple[str, Any]]:
    """Ogni valore in fondo all'albero, con il percorso che ci porta.

    Serve a dire *quale* campo e' cambiato: due dizionari con le stesse chiavi
    si confrontano, ma il nome del campo che differisce e' quello che si vuole
    leggere quando il test diventa rosso.
    """
    if isinstance(payload, dict):
        for chiave, valore in payload.items():
            yield from _foglie(valore, f"{percorso}.{chiave}" if percorso else str(chiave))
    elif isinstance(payload, list):
        for indice, valore in enumerate(payload):
            yield from _foglie(valore, f"{percorso}[{indice}]")
    else:
        yield percorso, payload


class ValutaDellaPanoramicaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(Account(name="Conto", source_group="bank", currency="EUR",
                                 starting_balance=Decimal("0"), current_balance=Decimal("0"),
                                 counts_in_net_worth=True, is_active=True))
        self.session.add(Transaction(occurred_on=date(2026, 9, 10), effective_on=date(2026, 9, 10),
                                     transaction_type="Expenses", amount=Decimal("100.00"),
                                     category_id=categoria(self.session, "Casa"),
                                     account_name="Conto", currency="EUR",
                                     is_recurring_template=False))
        self.session.add(BudgetPlan(period=date(2026, 9, 1), budget_type="Expenses", currency="EUR",
                                    category_id=categoria(self.session, "Casa"), amount=Decimal("150.00")))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    # -- attrezzi ----------------------------------------------------------

    def _valute_patrimonio(self, valore: str) -> None:
        """Le valute scelte in Patrimonio > Valute, che comandano anche qui."""
        self.session.query(AppSetting).filter(AppSetting.key == "net_worth_currencies").delete()
        self.session.add(AppSetting(key="net_worth_currencies", label="Valute nel patrimonio", value=valore))
        self.session.commit()

    def _euro(self) -> dict[str, Any]:
        return summary(2026, 9, None, self.session)

    def _in(self, valuta: str) -> dict[str, Any]:
        return summary(2026, 9, None, self.session, valuta)

    # -- la promessa -------------------------------------------------------

    def test_senza_valuta_la_pagina_e_quella_di_sempre(self) -> None:
        # Il parametro assente e l'euro esplicito sono la stessa pagina, e il
        # percorso in euro non legge nemmeno il listino.
        self.assertEqual(self._euro(), self._in("EUR"))
        self._cambio("CHF")
        self.assertEqual(self._euro(), self._in("EUR"))

    def test_il_cambio_e_quello_di_fine_periodo(self) -> None:
        # Settembre vale 1,10 e ottobre 1,20: la pagina di settembre si legge a
        # 1,10, non al cambio di oggi. E' la regola del patrimonio - "ognuna al
        # cambio del mese a cui si riferisce, non a quello di oggi" - e senza di
        # lei il passato si riscriverebbe a ogni movimento del cambio.
        self._cambio("CHF", giorno=date(2026, 9, 1), valore="1.10")
        self._cambio("CHF", giorno=date(2026, 10, 1), valore="1.20")
        self._valute_patrimonio("CHF")
        euro, franchi = self._euro(), self._in("CHF")
        self.assertEqual(round(euro["expenses"] * 1.10, 2), franchi["expenses"])
        self.assertEqual(round(euro["plannedExpenses"] * 1.10, 2), franchi["plannedExpenses"])
        # Un rapporto fra due importi scalati dello stesso fattore non cambia:
        # e' il motivo per cui la conversione si fa sui totali e non riga per
        # riga, e per cui `budgetUsed` non va ricalcolato.
        self.assertEqual(euro["budgetUsed"], franchi["budgetUsed"])

    def test_il_cambio_che_manca_non_si_inventa(self) -> None:
        # Una valuta che non e' una scelta dell'utente: non e' una pagina che si
        # puo' chiedere.
        with self.assertRaises(HTTPException) as errore:
            self._in("XYZ")
        self.assertEqual(422, errore.exception.status_code)
        # Una valuta in elenco di cui non si ha il cambio: 409, non un numero
        # in euro spacciato per franchi.
        self._valute_patrimonio("GBP")
        with self.assertRaises(HTTPException) as errore:
            self._in("GBP")
        self.assertEqual(409, errore.exception.status_code)
        self.assertEqual("currencyUnavailable", errore.exception.detail)
        # Il periodo prima dello storico e' lo stesso caso: si chiede un mese
        # che i cambi non coprono.
        self._cambio("CHF", giorno=date(2027, 1, 5), valore="1.10")
        self._valute_patrimonio("CHF")
        with self.assertRaises(HTTPException) as errore:
            self._in("CHF")
        self.assertEqual(409, errore.exception.status_code)

    def test_i_cambi_di_due_periodi_diversi(self) -> None:
        # Anno intero e mese sono due periodi, e prendono due cambi: quello di
        # dicembre e quello di settembre.
        self._cambio("CHF", giorno=date(2026, 9, 1), valore="1.10")
        self.session.add(MarketPrice(symbol="EURCHF=X", observed_on=date(2026, 12, 20),
                                     price=Decimal("1.30")))
        self._valute_patrimonio("CHF")
        self.session.commit()
        self.assertEqual(date(2026, 9, 30), fine_periodo(2026, 9))
        self.assertEqual(date(2026, 12, 31), fine_periodo(2026, None))
        self.assertEqual(round(self._euro()["expenses"] * 1.10, 2), self._in("CHF")["expenses"])
        anno_euro = summary(2026, None, None, self.session)
        anno_franchi = summary(2026, None, None, self.session, "CHF")
        self.assertEqual(round(anno_euro["expenses"] * 1.30, 2), anno_franchi["expenses"])

    # -- la guardia sui campi ----------------------------------------------

    def test_ogni_numero_e_classificato(self) -> None:
        """Ogni numero in fondo alla risposta e' denaro, o e' dichiarato qui.

        E' la guardia vera, e va letta al contrario di come viene da scrivere:
        non chiede che i campi noti siano convertiti, chiede che **ogni numero
        presente** o sia denaro - e allora vale il cambio di fine periodo - o
        stia nell'elenco passato qui sotto con il motivo. Un campo monetario
        nuovo che entra nella risposta senza finire in `CAMPI_MONETARI` resta in
        euro in mezzo a numeri in franchi, e si legge come se fosse giusto: e'
        il solo modo in cui questa conversione puo' sbagliare in silenzio.

        Se questo test diventa rosso, la domanda e' una sola: quel numero e'
        denaro?
        """
        self._cambio("CHF")
        self._valute_patrimonio("CHF")
        # Percentuali, conteggi, giorni e riferimenti al periodo: non si
        # convertono, e il test lo verifica uno per uno.
        self._classificazione(self._euro(), self._in("CHF"), {
            "budgetUsed", "daysInPeriod", "daysPassed", "periodCompletion",
            "savingsRate", "spentPercent", "timePercent", "overBudgetCategories",
            "periodYear", "periodMonth",
        })

    def test_anche_la_ripartizione_si_legge_in_valuta(self) -> None:
        self._cambio("CHF")
        self._valute_patrimonio("CHF")
        euro = summary_breakdown(2026, 9, self.session)
        franchi = summary_breakdown(2026, 9, self.session, "CHF")
        # L'identificativo della categoria e il rapporto speso/pianificato non
        # sono denaro: uno e' una chiave, l'altro e' un rapporto fra due importi
        # scalati dello stesso fattore, e si legge gia' giusto senza toccarlo.
        self._classificazione(euro, franchi, {"categoryId", "completion"})

    # -- l'elenco maestro --------------------------------------------------

    def test_le_valute_sono_l_elenco_maestro(self) -> None:
        # Una riga sola, e l'euro sempre primo: il budget e la Panoramica
        # offrono esattamente quello che si e' scelto in Patrimonio > Valute.
        self.assertEqual([BASE_CURRENCY] + display_currencies(self.session),
                         budget_currencies(self.session))
        self._valute_patrimonio("CHF")
        self.assertEqual(["EUR", "CHF"], budget_currencies(self.session))
        self.assertEqual(["EUR", "CHF"], [BASE_CURRENCY] + display_currencies(self.session))

    def _cambio(self, codice: str, giorno: date = date(2026, 9, 1), valore: str = "1.10") -> None:
        _cambio(self.session, codice, giorno, valore)
        self.session.commit()

    def _classificazione(self, euro: dict[str, Any], franchi: dict[str, Any],
                         non_denaro: set[str], fattore: str = "1.10") -> None:
        """Ogni numero delle due letture e' denaro convertito, o dichiarato."""
        foglie_euro = dict(_foglie(euro))
        foglie_franchi = dict(_foglie(franchi))
        self.assertEqual(set(foglie_euro), set(foglie_franchi),
                         "la valuta cambia la forma della risposta")
        numeri = {percorso: valore for percorso, valore in foglie_euro.items()
                  if isinstance(valore, (int, float)) and not isinstance(valore, bool)}
        # Un numero solo non basta a dire che la risposta e' quella vera.
        self.assertGreater(len(numeri), 10)
        for percorso, valore in numeri.items():
            if percorso.rsplit(".", 1)[-1] in CAMPI_MONETARI:
                self.assertEqual(round(valore * float(fattore), 2),
                                 foglie_franchi[percorso], percorso)
            else:
                # Come per `CAMPI_MONETARI` si confronta il nome del campo e non
                # il percorso: `completion` vuol dire la stessa cosa in tutte le
                # sezioni, e un elenco per percorso andrebbe riscritto ogni volta
                # che una riga cambia forma.
                self.assertIn(percorso.rsplit(".", 1)[-1], non_denaro,
                              f"{percorso} non e' classificato: e' denaro?")
                self.assertEqual(valore, foglie_franchi[percorso], percorso)
