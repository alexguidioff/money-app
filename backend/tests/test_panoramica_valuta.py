"""La Panoramica in un'altra valuta: gli stessi numeri, in un'altra moneta.

La regola e' una: il cambio serve **solo** ai movimenti che non sono gia' nella
valuta in cui si sta leggendo la pagina. Un'entrata in franchi letta in franchi
vale quello che dice l'estratto conto - non passa per l'euro, perche' dividere e
rimoltiplicare per il cambio non torna al numero di partenza. Un movimento in
euro letto in franchi si converte al cambio del *suo* giorno; un piano di budget
e un obiettivo, che un giorno non ce l'hanno, alla fine del periodo; il
patrimonio, che e' un saldo a una data, a quella data.

Il primo test fissa la promessa su cui sta il resto: chi non chiede niente, o
chiede l'euro, riceve il dizionario di prima, chiave per chiave.

Il test della classificazione e' quello che conta davvero: cammina ogni foglia
delle due letture e pretende che ogni numero o sia denaro convertito, o sia
dichiarato qui sotto con il motivo. E' l'unico modo di accorgersi se un campo
monetario nuovo entra in questa risposta e nessuno lo converte - un numero in
euro in mezzo a numeri in franchi, che si legge come se fosse giusto.

I cambi e i numeri sono inventati e tondi: questo repository e' pubblico.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal
from typing import Any, Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import (BASE_CURRENCY, budget_actual, budget_currencies, display_currencies,
                             fine_periodo, summary, summary_breakdown, valuta_di_lettura)
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
        # percorso in euro non legge nemmeno il listino. Il confronto e' sulla
        # risposta *scritta*, non sui due dizionari: in Python `0 == 0.0`, e
        # un campo che in euro passasse per la conversione uscirebbe `0.0`
        # invece di `0` - stesso numero per il test, testo diverso per il
        # client.
        self.assertEqual(self._scritto(self._euro()), self._scritto(self._in("EUR")))
        self._cambio("CHF")
        self.assertEqual(self._scritto(self._euro()), self._scritto(self._in("EUR")))

    def _scritto(self, payload: dict[str, Any]) -> str:
        """La risposta come la legge il client: cosi' anche i tipi contano."""
        return json.dumps(payload, sort_keys=True, default=str)

    def test_il_piano_a_fine_periodo_il_movimento_al_suo_giorno(self) -> None:
        # Due date, due cambi. Il piano di settembre non ha un giorno: si legge
        # al cambio di fine settembre (1,20), non a quello del giorno in cui
        # l'hai scritto ne' a quello di oggi. Il movimento ce l'ha: il 10
        # settembre vale 1,00, e la pagina di settembre si legge a 1,00.
        self._cambio("CHF", giorno=date(2026, 9, 1), valore="1.00")
        self._cambio("CHF", giorno=date(2026, 9, 20), valore="1.20")
        self._valute_patrimonio("CHF")
        franchi = self._in("CHF")
        self.assertEqual(round(100.00 * 1.00, 2), franchi["expenses"])
        self.assertEqual(round(150.00 * 1.20, 2), franchi["plannedExpenses"])
        # Il cambio di oggi (o di ottobre) non entra da nessuna parte.
        self._cambio("CHF", giorno=date(2026, 10, 1), valore="1.30")
        self.assertEqual(franchi, self._in("CHF"))

    def test_un_movimento_nella_valuta_letta_non_si_riconverte(self) -> None:
        """La regola: il cambio serve solo a chi non e' in quella valuta.

        Un'entrata in franchi letta in franchi vale quello che dice l'estratto
        conto - 3.409,70, non 3.415,01 - perche' dividere e rimoltiplicare per
        il cambio non torna al numero di partenza. Letta in euro si converte al
        cambio del giorno in cui e' successa.
        """
        self._cambio("CHF", giorno=date(2026, 9, 25), valore="0.94")
        self._cambio("CHF", giorno=date(2026, 10, 1), valore="0.94527")
        self._valute_patrimonio("CHF")
        self.session.add(Account(name="Conto CHF", source_group="bank", currency="CHF",
                                 starting_balance=Decimal("0"), current_balance=Decimal("0"),
                                 counts_in_net_worth=True, is_active=True))
        self.session.add(Transaction(occurred_on=date(2026, 9, 25), effective_on=date(2026, 10, 1),
                                     transaction_type="Income", amount=Decimal("3409.70"),
                                     category_id=categoria(self.session, "Stipendio"),
                                     account_name="Conto CHF", currency="CHF",
                                     is_recurring_template=False))
        self.session.commit()
        # Il valore di faccia, non il giro andata e ritorno: 3409.70 / 0.94 * 0.94527
        # farebbe 3415,01, che e' il numero sbagliato che si vedeva in Panoramica.
        self.assertEqual(3409.70, summary(2026, 10, None, self.session, "CHF")["income"])
        self.assertEqual(round(3409.70 / 0.94, 2), summary(2026, 10, None, self.session)["income"])

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
        # Anno intero e mese sono due periodi, e i *piani* prendono due cambi:
        # quello di dicembre (1,30) e quello di settembre (1,10). I movimenti
        # no: quello del 10 settembre si converte al cambio del 10 settembre in
        # tutte e due le letture, ed e' il punto della regola.
        self._cambio("CHF", giorno=date(2026, 9, 1), valore="1.10")
        self.session.add(MarketPrice(symbol="EURCHF=X", observed_on=date(2026, 12, 20),
                                     price=Decimal("1.30")))
        self._valute_patrimonio("CHF")
        self.session.commit()
        self.assertEqual(date(2026, 9, 30), fine_periodo(2026, 9))
        self.assertEqual(date(2026, 12, 31), fine_periodo(2026, None))
        self.assertEqual(round(100.00 * 1.10, 2), self._in("CHF")["expenses"])
        self.assertEqual(round(150.00 * 1.10, 2), self._in("CHF")["plannedExpenses"])
        anno = summary(2026, None, None, self.session, "CHF")
        self.assertEqual(round(100.00 * 1.10, 2), anno["expenses"])
        self.assertEqual(round(150.00 * 1.30, 2), anno["plannedExpenses"])

    def test_la_valuta_non_resta_accesa_dopo_la_pagina(self) -> None:
        """La valuta letta non sopravvive alla richiesta che l'ha chiesta.

        E' l'unico modo in cui questa conversione puo' sbagliare *in silenzio*:
        la variabile di contesto non da' errore se resta accesa, e su un thread
        del pool la richiesta dopo - il budget, il report, un altro utente -
        leggerebbe in franchi senza averlo chiesto. Vale anche quando la pagina
        finisce con un 409 a meta' strada.
        """
        self._cambio("CHF")
        self._valute_patrimonio("CHF")
        self._in("CHF")
        self.assertEqual(BASE_CURRENCY, valuta_di_lettura())
        # E il percorso che solleva: `month=1` con lo storico che comincia a
        # settembre non ha un cambio per il periodo che chiede.
        with self.assertRaises(HTTPException):
            summary(2026, 1, None, self.session, "CHF")
        self.assertEqual(BASE_CURRENCY, valuta_di_lettura())
        # Il totale in memoria, che il gemello Python converte: in euro resta
        # quello di prima, non un numero in franchi.
        self.assertEqual({categoria(self.session, "Casa"): 100.0},
                         budget_actual(self.session, 2026, 9, "Expenses"))

    # -- la guardia sui campi ----------------------------------------------

    def test_ogni_numero_e_classificato(self) -> None:
        """Ogni numero in fondo alla risposta e' denaro, o e' dichiarato qui.

        E' la guardia vera, e va letta al contrario di come viene da scrivere:
        non chiede che i campi noti siano convertiti, chiede che **ogni numero
        presente** o sia denaro - e allora il fattore lo moltiplica, con un
        cambio solo in tutto il periodo - o stia in uno dei due elenchi passati
        qui sotto, con il motivo. Un campo monetario nuovo che entra nella
        risposta senza essere convertito resta in euro in mezzo a numeri in
        franchi, e si legge come se fosse giusto: e' il solo modo in cui questa
        conversione puo' sbagliare in silenzio.

        Se questo test diventa rosso, la domanda e' una sola: quel numero e'
        denaro?
        """
        self._cambio("CHF")
        self._valute_patrimonio("CHF")
        # Conteggi, giorni e riferimenti al periodo: non sono importi e il
        # cambio non li tocca.
        self._classificazione(self._euro(), self._in("CHF"), {
            "daysInPeriod", "daysPassed", "periodCompletion",
            "spentPercent", "timePercent", "overBudgetCategories",
            "periodYear", "periodMonth",
        }, rapporti={
            # Importi divisi per importi: non sono denaro, e quanto valgono
            # dipende dai due - che possono essere convertiti a due date diverse
            # (lo speso al giorno del movimento, il pianificato a fine periodo).
            "budgetUsed", "savingsRate", "coverage",
        })

    def test_anche_la_ripartizione_si_legge_in_valuta(self) -> None:
        self._cambio("CHF")
        self._valute_patrimonio("CHF")
        euro = summary_breakdown(2026, 9, self.session)
        franchi = summary_breakdown(2026, 9, self.session, "CHF")
        # L'identificativo della categoria e' una chiave; il rapporto
        # speso/pianificato e' un rapporto fra due importi, e Remaining/Excess
        # sono differenze fra due importi convertiti: si leggono giusti senza
        # toccarli, e il test lo verifica uno per uno.
        self._classificazione(euro, franchi, {"categoryId", "parentId"},
                              rapporti={"completion"})

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
                         non_denaro: set[str], rapporti: set[str] = frozenset(),
                         fattore: str = "1.10") -> None:
        """Ogni numero delle due letture e' denaro convertito, o dichiarato.

        Il nome del campo e non il percorso: `completion` vuol dire la stessa
        cosa in tutte le sezioni, e un elenco per percorso andrebbe riscritto
        ogni volta che una riga cambia forma.
        """
        foglie_euro = dict(_foglie(euro))
        foglie_franchi = dict(_foglie(franchi))
        self.assertEqual(set(foglie_euro), set(foglie_franchi),
                         "la valuta cambia la forma della risposta")
        numeri = {percorso: valore for percorso, valore in foglie_euro.items()
                  if isinstance(valore, (int, float)) and not isinstance(valore, bool)}
        # Un numero solo non basta a dire che la risposta e' quella vera.
        self.assertGreater(len(numeri), 10)
        for percorso, valore in numeri.items():
            nome = percorso.rsplit(".", 1)[-1]
            if nome in non_denaro:
                self.assertEqual(valore, foglie_franchi[percorso], percorso)
            elif nome in rapporti:
                # Un rapporto fra due importi: con un cambio solo in tutto il
                # periodo resterebbe uguale, ma non e' una promessa - dipende
                # dalle due date dei due importi. Qui si dichiara e basta.
                continue
            else:
                self.assertEqual(round(valore * float(fattore), 2),
                                 foglie_franchi[percorso],
                                 f"{percorso} non e' classificato: e' denaro?")
