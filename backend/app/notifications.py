"""Gli avvisi: cose che l'app sa gia' e che finora non diceva.

Due scelte che tengono in piedi il resto:

**Non si salvano gli avvisi, si salva cosa hai chiuso.** Ogni avviso viene
ricalcolato dallo stato attuale, quindi non esistono avvisi che sopravvivono al
problema che li ha generati: risolvi la cosa e spariscono da soli. Del passato
resta solo l'elenco delle chiavi che hai messo via.

**La chiave descrive l'occorrenza, non il tipo.** ``budget:2026-09:Groceries``
non e' ``budget:2026-10:Groceries``: chiudere lo sforamento di settembre non ti
rende cieco a quello di ottobre. Viceversa lo stesso identico problema non
torna a bussare all'infinito, perche' la sua chiave e' gia' fra quelle chiuse.

Il testo non sta qui: si mandano un codice e i suoi valori, e lo compone
l'interfaccia nella lingua scelta.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core_routes import budget_actual, conti_con_valore_di_mercato, quoted_prices_by_instrument
from .database import get_session
from .models import (Account, AccountValuation, BudgetPlan, DismissedNotification,
                     InvestmentInstrument, InvestmentTransaction, Transaction)
from .transaction_rules import REAL_MOVEMENT, INCOMPLETE_MOVEMENT


def _incomplete(session):
    count, newest = session.execute(select(func.count(Transaction.id), func.max(Transaction.id))
                                   .where(REAL_MOVEMENT, INCOMPLETE_MOVEMENT)).one()
    return [{"key": f"incomplete:{count}:{newest}", "code": "incompleteMovements",
             "level": "warning", "params": {"count": count}}] if count else []

router = APIRouter()

# Oltre questa quota di budget consumata l'avviso scatta: non al primo euro
# sopra, perche' un budget sforato dell'1% non e' una notizia.
SOGLIA_SFORAMENTO = 1.2
# Un dump piu' vecchio di cosi' vuol dire che il backup automatico non gira.
GIORNI_BACKUP = 3


def _periodo(anno: int, mese: int) -> str:
    """Il periodo esce come data, non come frase.

    Il nome del mese lo scrive l'interfaccia nella lingua scelta: mandarlo
    gia' composto da qui significherebbe "Agosto 2026" dentro una pagina in
    inglese, ed e' un errore che abbiamo gia' fatto altrove.
    """
    return f"{anno}-{mese:02d}"


def _sforamenti_budget(session: Session, oggi: date) -> list[dict[str, Any]]:
    """Categorie che hanno superato il pianificato del mese in corso."""
    avvisi = []
    # Il confronto e' senza maiuscole, ma il nome mostrato resta quello scritto
    # nel budget: leggere "groceries" al posto di "Groceries" fa sembrare
    # l'avviso una cosa di un altro programma.
    pianificato = {
        piano.category.strip().lower(): (piano.category.strip(), float(piano.amount))
        for piano in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == date(oggi.year, oggi.month, 1),
            BudgetPlan.budget_type == "Expenses",
        )).all()
    }
    effettivo = budget_actual(session, oggi.year, oggi.month, "Expenses")
    for chiave, (nome, previsto) in pianificato.items():
        speso = effettivo.get(chiave, 0.0)
        if previsto > 0 and speso > previsto * SOGLIA_SFORAMENTO:
            avvisi.append({
                "key": f"budget:{oggi.year}-{oggi.month:02d}:{chiave}",
                "code": "budgetOverrun",
                "level": "warning",
                "params": {"category": nome, "spent": round(speso, 2),
                           "planned": round(previsto, 2),
                           "percent": round(speso / previsto * 100)},
            })
    return avvisi


def _quotazioni(session: Session) -> list[dict[str, Any]]:
    """Strumenti valorizzati al costo d'acquisto invece che a mercato."""
    avvisi = []
    _, problemi = quoted_prices_by_instrument(session)
    for problema in problemi:
        motivo = problema["reason"]
        if motivo == "stale":
            avvisi.append({
                "key": f"quote-stale:{problema['symbol']}:{problema['observedOn']}",
                "code": "staleQuote",
                "level": "warning",
                "params": {"instrument": problema["instrument"], "days": problema["ageDays"]},
            })
        elif motivo == "no_quote":
            avvisi.append({
                "key": f"quote-missing:{problema['symbol']}",
                "code": "missingQuote",
                "level": "warning",
                "params": {"instrument": problema["instrument"], "symbol": problema["symbol"]},
            })
        elif motivo == "no_fx":
            avvisi.append({
                "key": f"fx-missing:{problema['currency']}",
                "code": "missingFx",
                "level": "warning",
                "params": {"instrument": problema["instrument"], "currency": problema["currency"]},
            })
    return avvisi


def _strumenti_senza_ticker(session: Session) -> list[dict[str, Any]]:
    """Senza ticker non c'e' prezzo, e senza prezzo il valore e' fermo al costo."""
    senza = [row.name for row in session.scalars(select(InvestmentInstrument)).all()
             if not (row.provider_symbol or "").strip()]
    if not senza:
        return []
    return [{
        "key": "instruments-without-ticker:" + ",".join(sorted(senza)),
        "code": "instrumentsWithoutTicker",
        "level": "info",
        "params": {"names": ", ".join(sorted(senza)[:4])},
    }]


def _budget_in_scadenza(session: Session, oggi: date) -> list[dict[str, Any]]:
    """Il budget finisce e nessuno lo dice: la scheda resta vuota in silenzio."""
    avvisi = []
    for tipo in ("Expenses", "Income", "Savings"):
        ultimo = session.scalar(select(func.max(BudgetPlan.period)).where(BudgetPlan.budget_type == tipo))
        if ultimo is None:
            continue
        mesi_rimasti = (ultimo.year - oggi.year) * 12 + (ultimo.month - oggi.month)
        if mesi_rimasti <= 1:
            avvisi.append({
                "key": f"budget-ends:{tipo}:{ultimo.isoformat()}",
                "code": "budgetEnding",
                "level": "info",
                "params": {"type": tipo, "period": _periodo(ultimo.year, ultimo.month)},
            })
    return avvisi


def _mese_da_chiudere(session: Session, oggi: date) -> list[dict[str, Any]]:
    """Un mese finito senza nessun movimento registrato e' quasi sempre una
    dimenticanza, non un mese senza spese."""
    anno, mese = (oggi.year - 1, 12) if oggi.month == 1 else (oggi.year, oggi.month - 1)
    quanti = session.scalar(select(func.count(Transaction.id)).where(
        func.extract("year", Transaction.effective_on) == anno,
        func.extract("month", Transaction.effective_on) == mese,
        Transaction.is_recurring_template.is_(False),
    )) or 0
    if quanti:
        return []
    return [{
        "key": f"empty-month:{anno}-{mese:02d}",
        "code": "emptyMonth",
        "level": "info",
        "params": {"period": _periodo(anno, mese)},
    }]


def _backup(oggi: date) -> list[dict[str, Any]]:
    from .backup import last_backup

    ultimo = last_backup()
    if ultimo is None:
        return [{"key": "backup-missing", "code": "backupMissing", "level": "warning", "params": {}}]
    quando = datetime.fromisoformat(ultimo["created_at"].removesuffix("Z"))
    giorni = (datetime.utcnow() - quando).days
    if giorni <= GIORNI_BACKUP:
        return []
    return [{
        "key": f"backup-old:{quando.date().isoformat()}",
        "code": "backupOld",
        "level": "warning",
        "params": {"days": giorni},
    }]


# Sopra questa eta' una valutazione manuale non descrive piu' il presente: una
# casa o un'auto si rivedono una volta l'anno, non piu' spesso.
GIORNI_VALUTAZIONE = 365


def _valutazioni_scadute(session: Session, oggi: date) -> list[dict[str, Any]]:
    """Conti da valutare a mano la cui stima e' vecchia, o che non ne hanno.

    Non e' un promemoria generico su tutti i conti: per gli altri il saldo si
    calcola da solo e non c'e' niente da ricordare.
    """
    ultime = dict(session.execute(
        select(AccountValuation.account_id, func.max(AccountValuation.observed_on))
        .group_by(AccountValuation.account_id)).all())
    limite = oggi - timedelta(days=GIORNI_VALUTAZIONE)
    avvisi = []
    for conto in session.scalars(select(Account).where(
            Account.needs_manual_valuation.is_(True), Account.is_active.is_(True))).all():
        ultima = ultime.get(conto.id)
        if ultima is not None and ultima > limite:
            continue
        avvisi.append({
            # La chiave contiene la data dell'ultima stima: appena ne registri
            # una nuova la chiave cambia, e l'avviso chiuso non copre quello dopo.
            "key": f"valuation:{conto.id}:{ultima.isoformat() if ultima else 'mai'}",
            "code": "valuationStale",
            "level": "info",
            "params": {"account": conto.name, "lastSeen": ultima.isoformat() if ultima else None},
        })
    return avvisi


def _portafoglio_senza_conto(session: Session) -> list[dict[str, Any]]:
    """Il ledger ha operazioni ma nessun conto risulta collegato.

    Quale conto valga il prezzo di mercato si deduce dai collegamenti fra
    movimenti e operazioni del ledger. Se non ce n'e' nemmeno uno, il valore di
    mercato non ha un conto a cui attaccarsi: il patrimonio mostra il capitale
    versato e la rivalutazione sparisce, senza che niente lo dica.

    E' un calo silenzioso di decine di migliaia di euro, ed e' il motivo per cui
    questo avviso esiste: scollegare l'ultimo movimento non deve essere una cosa
    che si scopre guardando un grafico e non capendo perche' e' sceso.
    """
    if not session.scalar(select(func.count()).select_from(InvestmentTransaction)):
        return []
    if conti_con_valore_di_mercato(session):
        return []
    return [{
        "key": "portfolio:senza-conto",
        "code": "portfolioWithoutAccount",
        "level": "warning",
        "params": {},
    }]


def costruisci(session: Session) -> list[dict[str, Any]]:
    """Tutti gli avvisi che valgono adesso, ancora senza filtrare i chiusi."""
    oggi = date.today()
    avvisi: list[dict[str, Any]] = []
    for produttore in (
        lambda: _incomplete(session),
        lambda: _sforamenti_budget(session, oggi),
        lambda: _quotazioni(session),
        lambda: _strumenti_senza_ticker(session),
        lambda: _budget_in_scadenza(session, oggi),
        lambda: _mese_da_chiudere(session, oggi),
        lambda: _valutazioni_scadute(session, oggi),
        lambda: _portafoglio_senza_conto(session),
        lambda: _backup(oggi),
    ):
        try:
            avvisi.extend(produttore())
        except Exception:  # noqa: BLE001
            # Un avviso che non si riesce a calcolare non deve far sparire gli altri.
            continue
    ordine = {"warning": 0, "info": 1}
    avvisi.sort(key=lambda a: (ordine.get(a["level"], 2), a["key"]))
    return avvisi


@router.get("/api/notifications")
def elenco(session: Session = Depends(get_session)) -> dict[str, Any]:
    chiusi = {row.key for row in session.scalars(select(DismissedNotification)).all()}
    aperti = [avviso for avviso in costruisci(session) if avviso["key"] not in chiusi]
    return {
        "items": aperti,
        "unread": len(aperti),
        "dismissed": len(chiusi),
    }


@router.post("/api/notifications/dismiss")
def chiudi(payload: dict, session: Session = Depends(get_session)) -> dict[str, Any]:
    chiave = str(payload.get("key") or "").strip()
    if not chiave:
        raise HTTPException(status_code=422, detail="missing_key")
    gia = session.scalar(select(DismissedNotification).where(DismissedNotification.key == chiave))
    if gia is None:
        session.add(DismissedNotification(key=chiave))
        session.commit()
    return {"success": True, "key": chiave}


@router.post("/api/notifications/dismiss-all")
def chiudi_tutti(session: Session = Depends(get_session)) -> dict[str, Any]:
    chiusi = {row.key for row in session.scalars(select(DismissedNotification)).all()}
    nuovi = [avviso["key"] for avviso in costruisci(session) if avviso["key"] not in chiusi]
    for chiave in nuovi:
        session.add(DismissedNotification(key=chiave))
    session.commit()
    return {"success": True, "dismissed": len(nuovi)}
