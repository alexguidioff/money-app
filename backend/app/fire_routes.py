"""L'API della pagina FIRE: collega il motore ai dati veri.

Qui non si calcola niente di finanziario - quello sta in `fire_engine`, in
funzioni pure - e non si ricostruisce nessun aggregato: spese, patrimonio e
tasso di risparmio li sanno gia' `_summary_core`, `_net_worth_breakdown` e
`derived_savings`, e riscriverli vorrebbe dire farli divergere.

Questo modulo fa tre cose, e sono tutte e tre giunti fra due mondi:

1. traduce le righe del database nei tipi del motore - vocabolari diversi da
   una parte e dall'altra, e un valore non riconosciuto non darebbe errore ma
   un numero plausibile e sbagliato;
2. converte le percentuali salvate (4,00) nelle frazioni che il motore vuole
   (0,04): un fattore cento su un rendimento atteso e' invisibile a occhio e
   devastante su trent'anni;
3. sceglie **quali** spese e **quale** patrimonio entrano nel conto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core_routes import _net_worth_breakdown, _summary_core, budget_actual_year, net_worth_series, nomi_categorie
from .database import get_session
from .fire_engine import ETA_FINE_PROIEZIONE, Flusso, PianoFire, piano_fire
from .fire_montecarlo import EsitoMonteCarlo, simula
from .models import IncomeStream, RetirementProfile

router = APIRouter()

CENTO = Decimal("100")

# I due lati parlano lo stesso vocabolario - allineato di proposito - ma il
# confine va comunque presidiato: senza questo controllo un valore finito nel
# database per altra strada arriverebbe al motore, che lo tratterebbe come uno
# dei due e produrrebbe un piano plausibile e sbagliato. Non e' una traduzione,
# e' una lista di cio' che si accetta.
TIPI_AMMESSI = frozenset({"annuity", "capital"})

# Il motore e la pagina chiamano lo stesso traguardo con due nomi. Tradurre qui
# e' meglio che rinominare da una delle due parti: il motore parla la lingua
# del dominio finanziario, la pagina quella dell'interfaccia.
TRAGUARDI = {"solo_ponte": "bridge"}

# Uno scenario solo sarebbe una bugia di precisione: il rischio vero non e' la
# media dei rendimenti ma la loro *sequenza*. Un rendimento costante pero' non
# produce mai una sequenza sfortunata, quindi uno scarto fisso sulla media non
# poteva dirlo: diceva cosa succede se la media e' diversa, non se i primi anni
# di prelievo vanno male. La larghezza ora la misurano i percorsi di
# `fire_montecarlo`, e le due curve laterali sono i loro percentili.

# Fin dove si disegna. Il motore calcola fino a 100 anni - l'esaurimento del
# capitale va visto anche se arriva tardi - ma oltre i 90 la curva serve solo
# a schiacciare tutto il resto del grafico.
ETA_FINE_GRAFICO = 90

# I tassi di risparmio della tabella della leva, attorno a quello reale.
TASSI_LEVA = tuple(range(5, 65, 5))

# Il rendimento reale, un punto per riga: sotto l'1% il capitale non cresce
# abbastanza perche' la domanda abbia un senso, sopra il 12% non e' piu' un
# piano ma una scommessa.
RENDIMENTI_LEVA = tuple(range(1, 13))

# Le spese in pensione, dal -20% al +20% in passi del 10%: sono la variabile
# che si controlla meno, e il passo dice la differenza fra vivere come oggi e
# vivere un po' diversamente.
PASSI_SPESE_LEVA = (Decimal("-0.2"), Decimal("-0.1"), Decimal("0"), Decimal("0.1"), Decimal("0.2"))

RIGHE_LEVA = 5


def _frazione(percentuale: Decimal | None, predefinita: str = "0") -> Decimal:
    """Da percentuale salvata a frazione attesa dal motore."""
    return (Decimal(percentuale) if percentuale is not None else Decimal(predefinita)) / CENTO


def flusso_da_riga(riga: IncomeStream) -> Flusso:
    """Una riga del database come la vuole il motore.

    Il tipo non si passa cosi' com'e': un valore sconosciuto verrebbe trattato
    come uno dei due e il piano cambierebbe senza che nulla lo segnali.
    """
    if riga.kind not in TIPI_AMMESSI:
        raise HTTPException(422, detail="fireUnknownStreamKind")
    return Flusso(nome=riga.name, tipo=riga.kind, importo=Decimal(riga.amount),
                  eta_inizio=riga.start_age, indicizzato=bool(riga.indexed),
                  paese=riga.country or "",
                  importo_se_smetti_oggi=(Decimal(riga.amount_if_stopping_now)
                                          if riga.amount_if_stopping_now is not None else None))


def _riepiloghi(session: Session, oggi: date) -> dict[int, dict[str, Any]]:
    """Gli aggregati degli anni **conclusi**: quello in corso e' parziale.

    Mettere nel calcolo un anno a meta' farebbe sembrare l'obiettivo piu' basso
    di quello che e', ed e' l'errore che rende inutile un piano. Si calcolano
    una volta: spese e tasso di risparmio li chiedevano ciascuno per conto
    proprio, e ogni anno costava due passaggi sulle transazioni.
    """
    return {anno: _summary_core(session, anno, None) for anno in range(oggi.year - 3, oggi.year)}


def _tassi_storici(riepiloghi: dict[int, dict[str, Any]]) -> dict[int, float]:
    """Il tasso di risparmio degli anni conclusi, in percentuale.

    Serve alla tabella della leva e agli scenari: meglio il ritmo che l'utente
    ha tenuto davvero che un cursore con un valore plausibile e inventato.
    """
    return {anno: round(float(r["savingsRate"]) * 100, 1)
            for anno, r in riepiloghi.items() if r["savingsRate"] is not None}


def _spese_storiche(riepiloghi: dict[int, dict[str, Any]]) -> dict[int, float]:
    return {anno: r["expenses"] for anno, r in riepiloghi.items()}


def _versamenti(spese: Decimal, tasso_percentuale: float) -> Decimal:
    """Il risparmio annuo che, a queste spese, corrisponde a questo tasso.

    Tasso = risparmio / reddito e reddito = spese + risparmio, quindi
    risparmio = spese * t / (1 - t). Si parte dalle spese e non dal reddito
    perche' sono le stesse su cui e' costruito l'obiettivo: tabella della leva
    e piano principale devono dire lo stesso numero di anni.
    """
    tasso = min(max(Decimal(str(tasso_percentuale)) / CENTO, Decimal("0")), Decimal("0.95"))
    return (spese * tasso / (1 - tasso)).quantize(Decimal("0.01"))


def _anni_di_riferimento(storiche: dict[int, float], base: str) -> list[int]:
    """Gli anni la cui media e' la spesa di riferimento.

    Detto cosi' - anni, non un numero - la stessa scelta vale per il totale e
    per ogni categoria: la mediana di tre anni e' un anno preciso, e le sue
    categorie sommano esattamente al totale. Le mediane per categoria no.
    """
    if base == "last_year":
        return [max(storiche)] if storiche else []
    positivi = sorted((anno for anno, v in storiche.items() if v > 0), key=lambda anno: storiche[anno])
    if base == "median" and positivi:
        meta = len(positivi) // 2
        return positivi[meta:meta + 1] if len(positivi) % 2 else positivi[meta - 1:meta + 1]
    return positivi


def _spese_di_riferimento(storiche: dict[int, float], base: str,
                          personalizzate: Decimal | None) -> Decimal:
    if base == "custom" and personalizzate:
        return Decimal(personalizzate)
    anni = _anni_di_riferimento(storiche, "average" if base == "custom" else base)
    return Decimal(str(sum(storiche[anno] for anno in anni) / len(anni))) if anni else Decimal("0")


REGOLE_AMMESSE = frozenset({"drop", "change"})


def _spese_per_categoria(session: Session, anni: list[int]) -> dict[str, float]:
    """La spesa media per categoria negli anni di riferimento, con i nomi veri.

    Stessa fonte del totale (`budget_actual_year`, rimborsi gia' netti): se le
    categorie venissero da un'altra somma non tornerebbero al centesimo.
    """
    somme: dict[str, float] = {}
    for anno in anni:
        for mese in budget_actual_year(session, anno, "Expenses").values():
            for categoria, importo in mese.items():
                somme[categoria] = somme.get(categoria, 0.0) + importo
    if not somme:
        return {}
    nomi = nomi_categorie(session)
    return {nomi.get(chiave, chiave): round(somma / len(anni), 2)
            for chiave, somma in sorted(somme.items(), key=lambda voce: -voce[1]) if round(somma, 2) > 0}


def _regole(profilo: RetirementProfile) -> list[dict[str, Any]]:
    try:
        return json.loads(profilo.expense_rules) if profilo.expense_rules else []
    except ValueError:
        return []


def _spese_in_pensione(spese: Decimal, per_categoria: dict[str, float],
                       regole: list[dict[str, Any]]) -> Decimal:
    """Le spese di oggi, corrette categoria per categoria.

    Si applica solo alle categorie che esistono ancora nella storia: una
    regola rimasta su una categoria sparita cambierebbe il totale senza che la
    pagina possa mostrarla.
    """
    delta = Decimal("0")
    for regola in regole:
        oggi = per_categoria.get(regola["category"])
        if oggi is None:
            continue
        nuovo = Decimal("0") if regola["mode"] == "drop" else Decimal(str(regola["amount"]))
        delta += nuovo - Decimal(str(oggi))
    return max(Decimal("0"), spese + delta)


@dataclass
class _Contesto:
    """Tutto quello che il piano legge dal database, letto una volta."""

    profilo: RetirementProfile
    oggi: date
    eta: int
    patrimonio: dict[str, Any]
    storiche: dict[int, float]
    tassi: dict[int, float]
    tasso: float
    spese: Decimal              # oggi: su queste si misura il risparmio
    spese_pensione: Decimal     # in pensione: su queste si misura l'obiettivo
    versamenti: Decimal
    flussi_righe: list[IncomeStream] = field(default_factory=list)


def _contesto(session: Session, profilo: RetirementProfile) -> _Contesto:
    oggi = date.today()
    riepiloghi = _riepiloghi(session, oggi)
    storiche = _spese_storiche(riepiloghi)
    spese = _spese_di_riferimento(storiche, profilo.expense_basis, profilo.custom_annual_expenses)
    regole = _regole(profilo)
    # Con spese personalizzate l'utente dichiara il totale: correggerlo con le
    # categorie di una vita diversa (l'Italia di oggi per la Svizzera di
    # domani) darebbe un numero senza significato.
    spese_pensione = spese
    if regole and profilo.expense_basis != "custom":
        anni = _anni_di_riferimento(storiche, profilo.expense_basis)
        spese_pensione = _spese_in_pensione(spese, _spese_per_categoria(session, anni), regole)
    tassi = _tassi_storici(riepiloghi)
    # Il ritmo reale: la media degli anni conclusi.
    tasso = round(sum(tassi.values()) / len(tassi), 1) if tassi else 0.0
    return _Contesto(
        profilo=profilo, oggi=oggi, eta=oggi.year - profilo.birth_year,
        patrimonio=_net_worth_breakdown(session, oggi.year, oggi.month),
        storiche=storiche, tassi=tassi, tasso=tasso, spese=spese,
        spese_pensione=spese_pensione, versamenti=_versamenti(spese, tasso),
        flussi_righe=list(session.scalars(select(IncomeStream).order_by(IncomeStream.start_age)).all()),
    )


def _errore_motore(errore: ValueError) -> HTTPException:
    # Il motore rifiuta ipotesi incoerenti - tassi fuori intervallo, flussi
    # duplicati, un ritiro dopo la fine della proiezione - e ha ragione.
    # Ma il suo messaggio e' un codice, non un 500 con un traceback: la
    # pagina deve poter dire cosa non torna.
    return HTTPException(422, detail=f"fireEngine_{errore}")


@router.get("/api/fire")
def fire(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Il piano, piu' i dati su cui e' costruito.

    Restituisce anche gli ingressi - spese per anno, composizione del
    patrimonio - perche' la pagina deve poter mostrare *perche'* il numero e'
    quello, non solo qual e'.
    """
    profilo = session.scalar(select(RetirementProfile))
    if profilo is None:
        # Senza profilo non c'e' piano: la pagina chiede di configurarlo invece
        # di mostrare numeri costruiti su valori inventati.
        oggi = date.today()
        return {"configured": False, "expensesByYear": _spese_storiche(_riepiloghi(session, oggi)),
                "netWorth": _net_worth_breakdown(session, oggi.year, oggi.month)["total"],
                "plan": None, "streams": []}
    c = _contesto(session, profilo)
    try:
        piano = _piano(c)
        esito = _montecarlo(c, piano)
        scenari = {"p10": esito.percentili[10], "p90": esito.percentili[90]}
        leve = _leve(c, piano)
    except ValueError as errore:
        raise _errore_motore(errore) from errore
    return _risposta(c, piano, esito, scenari, _storico_patrimonio(session, c.oggi), leve)


def _piano(c: _Contesto, versamenti: Decimal | None = None, rendimento: Decimal | None = None,
           flussi: list[Flusso] | None = None, eta_ritiro: int | None = None,
           spese_annue_ritiro: Decimal | None = None) -> PianoFire:
    """Il motore chiamato con il profilo dell'utente, o con un'ipotesi diversa.

    Ogni parametro facoltativo e' un'ipotesi: quello che non si passa viene dal
    profilo. Il piano e le sue leve passano di qui, ed e' l'unico posto in cui
    il piano si calcola: e' quello che impedisce a una leva di diventare un
    secondo calcolo che col tempo diverge dal primo.
    """
    lean = c.profilo.lean_annual_expenses
    # Il ritiro non puo' precedere oggi: chi ha gia' passato l'eta obiettivo e'
    # in ritiro adesso, e il motore rifiuterebbe un orizzonte al contrario.
    ritiro = max(c.profilo.target_retirement_age, c.eta) if eta_ritiro is None else eta_ritiro
    spese_ritiro = c.spese_pensione if spese_annue_ritiro is None else spese_annue_ritiro
    return piano_fire(
        capitale=Decimal(str(c.patrimonio["total"])),
        spese_annue=c.spese,
        spese_annue_ritiro=spese_ritiro,
        flussi=flussi if flussi is not None else [flusso_da_riga(riga) for riga in c.flussi_righe],
        eta_oggi=c.eta,
        eta_ritiro=ritiro,
        rendimento_reale=rendimento if rendimento is not None else _frazione(c.profilo.real_return, "4"),
        swr=_frazione(c.profilo.withdrawal_rate, "4"),
        aliquota_prelievo=_frazione(c.profilo.withdrawal_tax_rate),
        # Senza, il motore rifiuta ogni rendita non indicizzata e la pagina
        # intera andava in errore alla prima LPP inserita.
        inflazione=_frazione(c.profilo.inflation, "2"),
        anno_oggi=c.oggi.year,
        versamenti_annui=c.versamenti if versamenti is None else versamenti,
        # Il motore rifiuta spese lean piu' alte di quelle in pensione, ma
        # queste cambiano con la storia: un profilo salvato ieri non deve
        # rompere la pagina oggi. Lean pari alle spese equivale a FI. Il tetto
        # segue le spese della riga: la leva che le abbassa le abbassa anche a
        # lui, o il motore rifiuterebbe l'ipotesi che l'utente sta guardando.
        spese_lean_annue=None if lean is None else min(Decimal(lean), spese_ritiro),
    )



def _montecarlo(c: _Contesto, piano) -> EsitoMonteCarlo:
    """La distribuzione del piano, sulle rendite che il motore ha gia' calcolato.

    Le entrate non si ricalcolano qui: il rischio di sequenza riguarda i
    rendimenti del capitale, non le pensioni, e una seconda implementazione dei
    flussi prima o poi divergerebbe dalla prima.

    L'orizzonte e' quello del motore, non quello del grafico. Fermarsi a 90
    direbbe "regge sempre" a chi preleva piu' a lungo, e a volatilita' zero
    questa simulazione deve dare la stessa serie di `piano_fire`: due orizzonti
    diversi la farebbero divergere sulla coda, che e' dove si decide se il
    capitale basta.
    """
    ritiro = max(c.profilo.target_retirement_age, c.eta)
    return simula(
        capitale=float(c.patrimonio["total"]),
        # In pensione si spendono queste, non quelle di oggi.
        spese_annue=float(c.spese_pensione),
        versamenti_annui=float(c.versamenti),
        entrate_per_eta={p.eta: float(p.rendite) for p in piano.serie},
        eta_oggi=c.eta, eta_ritiro=ritiro,
        # Un ritiro a 100 anni lascerebbe zero anni di prelievo: almeno uno.
        eta_fine=max(ETA_FINE_PROIEZIONE, ritiro + 1),
        rendimento_medio=float(_frazione(c.profilo.real_return, "4")),
        volatilita=float(c.profilo.return_volatility) / 100,
        aliquota_prelievo=float(_frazione(c.profilo.withdrawal_tax_rate)),
    )


def _al_centinaio(valore: Decimal) -> Decimal:
    """Arrotondato al centinaio: 14.732 non e' una cifra che si sceglie."""
    return (valore / CENTO).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * CENTO


@dataclass(frozen=True)
class _Leva:
    """Una leva: cosa si muove, e come si chiede al motore di muoverlo.

    `griglia` sono i valori che si provano, `corrente` quello dell'utente, e
    `prova` chiama il motore con quel valore. La riga dell'utente non passa da
    `prova`: e' il piano che la pagina ha gia' calcolato.
    """

    chiave: str
    griglia: Callable[[_Contesto], Sequence[Decimal]]
    corrente: Callable[[_Contesto], Decimal]
    prova: Callable[[_Contesto, Decimal], PianoFire]
    # Il risparmio annuo lo mostra solo la leva che lo muove: sulle altre
    # sarebbe lo stesso numero cinque volte.
    risparmio: bool = False


def _prova_risparmio(c: _Contesto, tasso: Decimal) -> PianoFire:
    return _piano(c, versamenti=_versamenti(c.spese, float(tasso)))


def _prova_rendimento(c: _Contesto, rendimento: Decimal) -> PianoFire:
    return _piano(c, rendimento=_frazione(rendimento))


def _prova_eta(c: _Contesto, eta: Decimal) -> PianoFire:
    return _piano(c, eta_ritiro=int(eta))


def _prova_spese(c: _Contesto, spese: Decimal) -> PianoFire:
    return _piano(c, spese_annue_ritiro=spese)


# Le quattro leve, nell'ordine in cui il selettore le mostra: si parte da
# quella che si controlla di piu' - quanto si mette da parte - e si finisce con
# quella che si controlla di meno.
LEVE = (
    _Leva("savingsRate", lambda c: tuple(Decimal(tasso) for tasso in TASSI_LEVA),
          lambda c: Decimal(str(c.tasso)), _prova_risparmio, risparmio=True),
    _Leva("return", lambda c: tuple(Decimal(tasso) for tasso in RENDIMENTI_LEVA),
          lambda c: Decimal(c.profilo.real_return), _prova_rendimento),
    _Leva("retirementAge", lambda c: tuple(Decimal(eta) for eta in range(c.eta, ETA_FINE_PROIEZIONE)),
          lambda c: Decimal(max(c.profilo.target_retirement_age, c.eta)), _prova_eta),
    _Leva("retirementExpenses",
          lambda c: tuple(_al_centinaio(c.spese_pensione * (1 + passo)) for passo in PASSI_SPESE_LEVA),
          lambda c: c.spese_pensione, _prova_spese),
)


def _leve(c: _Contesto, piano: PianoFire) -> list[dict[str, Any]]:
    """Le quattro leve del piano, calcolate dal motore.

    Una formula a parte nel browser dava 24 anni dove il piano ne diceva 29:
    due calcoli dello stesso numero finiscono sempre per divergere. La riga
    dell'utente e' il piano stesso, non una sua approssimazione al 5%. Vale per
    tutte e quattro: ogni riga e' `_piano(c, ...)` con un parametro cambiato, e
    chi prova a scrivere una formula approssimata se ne accorge dal primo test.

    Cinque righe centrate sul valore dell'utente, che resta quella evidenziata
    anche quando e' fuori griglia o al bordo: agli estremi si mostra la griglia
    che c'e', non si inventano valori per farlo stare in mezzo.
    """
    leve = []
    for leva in LEVE:
        griglia = leva.griglia(c)
        corrente = leva.corrente(c)
        vicino = min(griglia, key=lambda valore: abs(valore - corrente))
        inizio = max(0, min(griglia.index(vicino) - RIGHE_LEVA // 2, len(griglia) - RIGHE_LEVA))
        righe = []
        for valore in griglia[inizio:inizio + RIGHE_LEVA]:
            if valore == vicino:
                righe.append({"value": float(corrente), "current": True,
                              "yearsLeft": piano.anni_mancanti,
                              "capitalNeeded": float(piano.capitale_necessario),
                              "annualSavings": float(c.versamenti) if leva.risparmio else None})
                continue
            calcolato = leva.prova(c, valore)
            righe.append({"value": float(valore), "current": False,
                          "yearsLeft": calcolato.anni_mancanti,
                          "capitalNeeded": float(calcolato.capitale_necessario),
                          "annualSavings": float(_versamenti(c.spese, float(valore))) if leva.risparmio else None})
        leve.append({"key": leva.chiave, "rows": righe})
    return leve


def _storico_patrimonio(session: Session, oggi: date) -> list[dict[str, Any]]:
    """Il patrimonio come e' andato davvero, da disegnare dietro la proiezione.

    Una proiezione che parte da oggi non e' mai stata verificata da niente;
    con la storia dietro si vede se si sta sopra o sotto il proprio stesso piano.
    """
    serie = net_worth_series(session, oggi.year, oggi.month, 120)
    # Prima dei primi movimenti la serie ripete il saldo iniziale all'indietro
    # per tutti i dieci anni: sarebbe storia inventata. Si parte dall'ultimo
    # mese piatto, che fa da punto d'appoggio.
    primo_cambio = next((i for i, riga in enumerate(serie)
                         if riga["netWorth"] != serie[0]["netWorth"]), len(serie))
    serie = serie[max(primo_cambio - 1, 0):]
    per_anno: dict[int, float] = {}
    for riga in serie:
        anno = int(str(riga["period"])[:4])
        per_anno[anno] = riga["netWorth"]
    return [{"year": anno, "capital": valore} for anno, valore in sorted(per_anno.items())]


def _risposta(c: _Contesto, piano, esito: EsitoMonteCarlo, scenari, storico, leve) -> dict[str, Any]:
    # I percentili non portano l'anno: le eta' sono le stesse della serie
    # centrale, quindi l'anno e' quello che il motore ha gia' scritto per
    # quell'eta', non un secondo calendario calcolato qui.
    anni = {p.eta: p.anno for p in piano.serie}
    return {
        "configured": True,
        "age": c.eta,
        "expensesByYear": c.storiche,
        "expensesUsed": float(c.spese),
        # Diverse da quelle di oggi solo se ci sono regole per categoria: e'
        # su queste che si misura l'obiettivo.
        "retirementExpenses": float(c.spese_pensione),
        "savingsRateByYear": c.tassi,
        "savingsRate": c.tasso,
        # Il risparmio che la proiezione conta davvero: senza vederlo scritto
        # non si capisce da dove venga l'anno di indipendenza.
        "annualSavings": float(c.versamenti),
        "netWorth": c.patrimonio["total"],
        # Quanto del patrimonio e' a valore di mercato: serve alla pagina per
        # dire cosa sta contando.
        "marketValue": c.patrimonio["marketValue"],
        "profile": _profilo_dict(c.profilo),
        "streams": [_flusso_dict(riga) for riga in c.flussi_righe],
        "plan": {
            "phases": [{"kind": f.tipo, "fromAge": f.eta_inizio, "toAge": f.eta_fine,
                        "fromYear": f.anno_inizio, "toYear": f.anno_fine}
                       for f in piano.fasi],
            "capitalNeeded": float(piano.capitale_necessario),
            "bridgeCapital": float(piano.capitale_ponte),
            "topUpCapital": float(piano.capitale_rabbocco),
            "regimeAge": piano.eta_regime,
            "reachedAtAge": piano.eta_raggiungimento,
            "reachedInYear": piano.anno_raggiungimento,
            "yearsLeft": piano.anni_mancanti,
            "depletedAtAge": piano.eta_esaurimento,
            # Il capitale a inizio anno: quello di fine anno etichettato con
            # l'anno in corso staccava la curva dalla storia di un anno di
            # rendimento, e spostava l'incrocio prima dell'anno dichiarato.
            "series": [{"age": p.eta, "year": p.anno,
                        "capital": float(p.capitale),
                        "capitalNeeded": float(p.capitale_necessario),
                        "annuities": float(p.rendite), "lumpSums": float(p.capitali),
                        "expenses": float(p.spese), "shortfall": float(p.deficit)}
                       for p in piano.serie if p.eta <= ETA_FINE_GRAFICO],
            "milestones": {TRAGUARDI.get(nome, nome): {"capitalNeeded": float(t.capitale_necessario) if t.capitale_necessario is not None else None,
                                  "reached": t.raggiunto}
                           for nome, t in piano.traguardi.items()},
            "warnings": [*piano.avvertenze, "montecarlo_rendimenti_indipendenti"],
            # La simulazione non e' una previsione piu' di quanto lo sia il
            # piano: e' una distribuzione di ipotesi, e va detto che i
            # rendimenti estratti sono indipendenti fra loro.
            "monteCarlo": {
                "successRate": float(esito.successo),
                "paths": esito.percorsi,
                "volatility": float(c.profilo.return_volatility) / 100,
                "medianDepletionAge": esito.eta_esaurimento_mediana,
            },
            # Le spese lean oltre quelle di riferimento vengono portate al loro
            # livello (vedi `_piano`): la pagina deve dirlo, altrimenti Lean e
            # FI mostrano lo stesso numero senza una ragione visibile.
            "leanCapped": c.profilo.lean_annual_expenses is not None
                          and Decimal(c.profilo.lean_annual_expenses) > c.spese_pensione,
            "history": storico,
            # Tutte e quattro le leve, gia' calcolate: il selettore vive nel
            # browser e cambiare leva non costa una seconda chiamata, che su un
            # motore gia' chiamato una dozzina di volte per gli scenari sarebbe
            # l'unica parte visibile della sua lentezza.
            "leverage": leve,
            "scenarios": {nome: [{"age": p.eta, "year": anni.get(p.eta), "capital": float(p.capitale)}
                                 for p in serie if p.eta <= ETA_FINE_GRAFICO]
                          for nome, serie in scenari.items()},
        },
    }


def _profilo_dict(riga: RetirementProfile) -> dict[str, Any]:
    return {"birthYear": riga.birth_year, "country": riga.country,
            "targetRetirementAge": riga.target_retirement_age,
            "realReturn": float(riga.real_return),
            "returnVolatility": float(riga.return_volatility),
            "withdrawalRate": float(riga.withdrawal_rate),
            "withdrawalTaxRate": float(riga.withdrawal_tax_rate),
            "expenseBasis": riga.expense_basis,
            "customAnnualExpenses": float(riga.custom_annual_expenses) if riga.custom_annual_expenses is not None else None,
            "leanAnnualExpenses": float(riga.lean_annual_expenses) if riga.lean_annual_expenses is not None else None,
            "inflation": float(riga.inflation) if riga.inflation is not None else 2.0,
            # Testo, mai null: il modulo lo mette in un campo di testo, che con
            # null smette di essere controllato da React.
            "notes": riga.notes or ""}


def _flusso_dict(riga: IncomeStream) -> dict[str, Any]:
    return {"id": riga.id, "name": riga.name, "kind": riga.kind, "amount": float(riga.amount),
            "startAge": riga.start_age, "indexed": bool(riga.indexed), "country": riga.country,
            "amountIfStoppingNow": float(riga.amount_if_stopping_now) if riga.amount_if_stopping_now is not None else None,
            "notes": riga.notes or ""}


class ProfiloPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    birth_year: int = Field(ge=1900, le=2100, alias="birthYear")
    country: str = Field(min_length=2, max_length=2)
    target_retirement_age: int = Field(ge=18, le=100, alias="targetRetirementAge")
    real_return: float = Field(ge=-50, le=50, alias="realReturn")
    return_volatility: float = Field(default=15, ge=0, le=100, alias="returnVolatility")
    withdrawal_rate: float = Field(gt=0, le=100, alias="withdrawalRate")
    withdrawal_tax_rate: float = Field(ge=0, lt=100, alias="withdrawalTaxRate")
    expense_basis: str = Field(default="average", alias="expenseBasis")
    custom_annual_expenses: float | None = Field(default=None, ge=0, alias="customAnnualExpenses")
    lean_annual_expenses: float | None = Field(default=None, ge=0, alias="leanAnnualExpenses")
    inflation: float = Field(default=2, ge=0, le=50)
    notes: str | None = None


@router.put("/api/fire/profile")
def salva_profilo(payload: ProfiloPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    if payload.expense_basis not in {"last_year", "average", "median", "custom"}:
        raise HTTPException(422, detail="fireInvalidExpenseBasis")
    riga = session.scalar(select(RetirementProfile))
    if riga is None:
        riga = RetirementProfile(birth_year=payload.birth_year)
        session.add(riga)
    for campo in ("birth_year", "country", "target_retirement_age", "expense_basis", "notes"):
        setattr(riga, campo, getattr(payload, campo))
    riga.real_return = Decimal(str(payload.real_return))
    riga.return_volatility = Decimal(str(payload.return_volatility))
    riga.withdrawal_rate = Decimal(str(payload.withdrawal_rate))
    riga.withdrawal_tax_rate = Decimal(str(payload.withdrawal_tax_rate))
    riga.custom_annual_expenses = (Decimal(str(payload.custom_annual_expenses))
                                   if payload.custom_annual_expenses is not None else None)
    riga.lean_annual_expenses = (Decimal(str(payload.lean_annual_expenses))
                                 if payload.lean_annual_expenses is not None else None)
    riga.inflation = Decimal(str(payload.inflation))
    _valida_o_annulla(session, riga)
    session.commit(); session.refresh(riga)
    return _profilo_dict(riga)


class FlussoPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=120)
    kind: str = "annuity"
    amount: float = Field(ge=0)
    start_age: int = Field(ge=18, le=100, alias="startAge")
    indexed: bool = True
    country: str | None = None
    amount_if_stopping_now: float | None = Field(default=None, ge=0, alias="amountIfStoppingNow")
    notes: str | None = None


def _valida_o_annulla(session: Session, profilo: RetirementProfile | None = None) -> None:
    """Il piano che la pagina calcolera' deve reggere, prima di salvare.

    Il motore rifiuta ipotesi incoerenti - due flussi con lo stesso nome, una
    stima di pensione anticipata su un capitale, stime invertite - e ha
    ragione. Ma accorgersene all'apertura della pagina FIRE voleva dire un
    errore la', lontano dal modulo che l'aveva causato. Si prova il piano con i
    dati appena scritti e, se non regge, non si salva e il modulo dice perche'.
    Patrimonio e spese non contano per queste regole: bastano zeri.
    """
    session.flush()
    righe = list(session.scalars(select(IncomeStream)).all())
    try:
        nomi = [riga.name for riga in righe]
        if len(nomi) != len(set(nomi)):
            raise HTTPException(409, detail="fireStreamDuplicateName")
        profilo = profilo or session.scalar(select(RetirementProfile))
        if profilo is None:
            return
        oggi = date.today()
        _piano(_Contesto(profilo=profilo, oggi=oggi, eta=oggi.year - profilo.birth_year,
                         patrimonio={"total": 0}, storiche={}, tassi={}, tasso=0.0,
                         spese=Decimal("0"), spese_pensione=Decimal("0"), versamenti=Decimal("0"),
                         flussi_righe=righe))
    except ValueError as errore:
        session.rollback()
        raise _errore_motore(errore) from errore
    except HTTPException:
        session.rollback()
        raise


def _applica_flusso(riga: IncomeStream, payload: FlussoPayload) -> None:
    if payload.kind not in TIPI_AMMESSI:
        raise HTTPException(422, detail="fireUnknownStreamKind")
    riga.name, riga.kind, riga.start_age = payload.name.strip(), payload.kind, payload.start_age
    riga.amount = Decimal(str(payload.amount))
    riga.indexed, riga.country, riga.notes = payload.indexed, payload.country, payload.notes
    riga.amount_if_stopping_now = (Decimal(str(payload.amount_if_stopping_now))
                                   if payload.amount_if_stopping_now is not None else None)


@router.get("/api/fire/profile")
def leggi_profilo(session: Session = Depends(get_session)) -> dict[str, Any] | None:
    riga = session.scalar(select(RetirementProfile))
    return _profilo_dict(riga) if riga is not None else None


@router.get("/api/fire/streams")
def elenco_flussi(session: Session = Depends(get_session)) -> dict[str, Any]:
    righe = session.scalars(select(IncomeStream).order_by(IncomeStream.start_age)).all()
    return {"streams": [_flusso_dict(riga) for riga in righe]}


@router.post("/api/fire/streams", status_code=201)
def crea_flusso(payload: FlussoPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = IncomeStream(name=payload.name, amount=Decimal("0"), start_age=payload.start_age)
    _applica_flusso(riga, payload)
    session.add(riga)
    _valida_o_annulla(session)
    session.commit(); session.refresh(riga)
    return _flusso_dict(riga)


@router.patch("/api/fire/streams/{stream_id}")
def modifica_flusso(stream_id: int, payload: FlussoPayload,
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = session.get(IncomeStream, stream_id)
    if riga is None:
        raise HTTPException(404, detail="fireStreamNotFound")
    _applica_flusso(riga, payload)
    _valida_o_annulla(session)
    session.commit(); session.refresh(riga)
    return _flusso_dict(riga)


@router.delete("/api/fire/streams/{stream_id}")
def cancella_flusso(stream_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = session.get(IncomeStream, stream_id)
    if riga is None:
        raise HTTPException(404, detail="fireStreamNotFound")
    session.delete(riga); session.commit()
    return {"deleted": stream_id}


class RegolaPayload(BaseModel):
    category: str = Field(min_length=1, max_length=255)
    mode: str
    amount: float | None = Field(default=None, ge=0)


class RegolePayload(BaseModel):
    rules: list[RegolaPayload]


def _regole_dict(session: Session, profilo: RetirementProfile) -> dict[str, Any]:
    oggi = date.today()
    storiche = _spese_storiche(_riepiloghi(session, oggi))
    spese = _spese_di_riferimento(storiche, profilo.expense_basis, profilo.custom_annual_expenses)
    personalizzate = profilo.expense_basis == "custom"
    per_categoria = {} if personalizzate else _spese_per_categoria(
        session, _anni_di_riferimento(storiche, profilo.expense_basis))
    regole = {r["category"]: r for r in _regole(profilo)}
    return {
        "configured": True,
        # Con spese personalizzate le regole non si applicano: la pagina lo
        # dice invece di mostrare un modulo che non cambia niente.
        "applies": not personalizzate,
        "referenceExpenses": float(spese),
        "retirementExpenses": float(_spese_in_pensione(spese, per_categoria, list(regole.values()))),
        "categories": [{"category": nome, "amount": importo,
                        "mode": regole.get(nome, {}).get("mode", "stay"),
                        "newAmount": regole.get(nome, {}).get("amount")}
                       for nome, importo in per_categoria.items()],
    }


def _profilo_obbligatorio(session: Session) -> RetirementProfile:
    profilo = session.scalar(select(RetirementProfile))
    if profilo is None:
        raise HTTPException(409, detail="fireProfileRequired")
    return profilo


@router.get("/api/fire/expense-rules")
def leggi_regole(session: Session = Depends(get_session)) -> dict[str, Any]:
    profilo = session.scalar(select(RetirementProfile))
    if profilo is None:
        # Nessun profilo e' lo stato di chi apre le impostazioni la prima volta,
        # non un conflitto: con un 409 il browser registrava un errore a ogni
        # visita. Il 409 resta per chi prova a *salvare* regole senza profilo.
        return {"configured": False, "applies": False, "referenceExpenses": 0.0,
                "retirementExpenses": 0.0, "categories": []}
    return _regole_dict(session, profilo)


@router.put("/api/fire/expense-rules")
def salva_regole(payload: RegolePayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    profilo = _profilo_obbligatorio(session)
    regole = []
    for regola in payload.rules:
        if regola.mode == "stay":
            continue
        if regola.mode not in REGOLE_AMMESSE or (regola.mode == "change" and regola.amount is None):
            raise HTTPException(422, detail="fireInvalidExpenseRule")
        regole.append({"category": regola.category, "mode": regola.mode,
                       "amount": regola.amount if regola.mode == "change" else None})
    profilo.expense_rules = json.dumps(regole) if regole else None
    session.commit()
    return _regole_dict(session, profilo)


SPOSTAMENTI_PENSIONE = range(-5, 6)


@router.get("/api/fire/pension-shift")
def spostamento_pensioni(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Il piano con le pensioni spostate da -5 a +5 anni, tutto in una volta.

    Le eta' pensionabili europee si muovono con l'aspettativa di vita, e a
    trent'anni dalla pensione quella scritta oggi e' un'ipotesi. Undici piani
    in una risposta: il cursore sulla pagina e' istantaneo invece di chiedere
    al server a ogni scatto. Si spostano solo i flussi futuri, e mai prima di
    oggi: un capitale gia' ricevuto non torna indietro.
    """
    profilo = _profilo_obbligatorio(session)
    c = _contesto(session, profilo)
    risultati = []
    for anni in SPOSTAMENTI_PENSIONE:
        flussi = [replace(f, eta_inizio=max(c.eta + 1, f.eta_inizio + anni)) if f.eta_inizio > c.eta else f
                  for f in (flusso_da_riga(riga) for riga in c.flussi_righe)]
        try:
            piano = _piano(c, flussi=flussi)
        except ValueError:
            # Uno spostamento che rende il piano incoerente (una pensione
            # anticipata oltre il limite della stima) non cancella gli altri.
            risultati.append({"shift": anni, "capitalNeeded": None, "reachedAtAge": None, "reachedInYear": None})
            continue
        risultati.append({"shift": anni, "capitalNeeded": float(piano.capitale_necessario),
                          "reachedAtAge": piano.eta_raggiungimento, "reachedInYear": piano.anno_raggiungimento})
    return {"hasStreams": bool(c.flussi_righe), "results": risultati}


def register_fire_routes(app) -> None:
    app.include_router(router)
