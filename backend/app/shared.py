"""I totali di chi ha scelto di condividerli, affiancati.

Si vedono solo i numeri riassuntivi: quanto e' entrato, quanto e' uscito,
quanto e' rimasto, il patrimonio e il portafoglio. Mai un movimento, mai una
categoria, mai un conto. La riservatezza non e' affidata al fatto che la
pagina non li mostri: i dettagli non escono proprio da qui.

Il modo in cui i totali vengono calcolati merita una nota. Le politiche di riga
impediscono di leggere i dati altrui, ed e' giusto cosi'. Quindi per ogni
persona che condivide ci si mette nei suoi panni - si dichiara al database che
la richiesta e' sua - e si riusano gli stessi calcoli della Panoramica. Nessuna
scorciatoia che scavalchi l'isolamento, nessuna formula duplicata che un domani
possa divergere da quella vera.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core_routes import _net_worth_breakdown, _summary_core, net_worth_series
from .database import SessionLocal, get_session, reset_current_user, set_current_user
from .models import User

router = APIRouter()


def _totali_di(user_id: int, year: int, month: int | None) -> dict[str, Any]:
    gettone = set_current_user(user_id)
    try:
        # La sessione va aperta dopo: e' all'inizio della transazione che si
        # dichiara al database di chi sono i dati da leggere.
        with SessionLocal() as sessione:
            base = _summary_core(sessione, year, month)
            patrimonio = _net_worth_breakdown(sessione, year, month)
        return {
            "income": base["income"],
            "expenses": base["expenses"],
            "savings": base["savings"],
            "savingsRate": base["savingsRate"],
            "daysInPeriod": base["daysInPeriod"],
            "daysPassed": base["daysPassed"],
            "periodCompletion": base["periodCompletion"],
            "netWorth": patrimonio["total"],
            "marketValue": patrimonio["marketValue"],
            "investedCapital": patrimonio["investedCapital"],
            "gain": patrimonio["gain"],
            "gainPercent": patrimonio["gainPercent"],
        }
    finally:
        reset_current_user(gettone)


def _serie_di(user_id: int, end_year: int, end_month: int, months: int) -> list[dict[str, Any]]:
    gettone = set_current_user(user_id)
    try:
        with SessionLocal() as sessione:
            return net_worth_series(sessione, end_year, end_month, months)
    finally:
        reset_current_user(gettone)


@router.get("/api/shared/trend")
def andamento_condiviso(
    year: int = Query(ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12, description="0 = fine anno"),
    months: int = Query(12, ge=1, le=60),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """L'andamento di ciascuno e quello di tutti messi insieme.

    La riga "famiglia" e' la somma dei patrimoni e dei flussi di chi condivide:
    per i patrimoni ha senso perche' sono grandezze allo stesso istante, per
    entrate e uscite perche' sono flussi dello stesso mese.
    """
    oggi = date.today()
    # Un anno in corso finisce oggi: i mesi futuri non sono zeri e non sono
    # nemmeno una previsione, quindi non devono diventare punti del grafico.
    fine_mese = oggi.month if month == 0 and year == oggi.year else month or 12
    condividono = session.scalars(
        select(User).where(User.shares_totals.is_(True)).order_by(User.id)
    ).all()
    persone = [{
        "id": persona.id,
        "displayName": persona.display_name,
        "series": _serie_di(persona.id, year, fine_mese, months),
    } for persona in condividono]

    insieme: list[dict[str, Any]] = []
    if persone:
        for indice, punto in enumerate(persone[0]["series"]):
            insieme.append({
                "period": punto["period"],
                "label": punto["label"],
                **{campo: round(sum(p["series"][indice][campo] for p in persone), 2)
                   for campo in ("netWorth", "income", "expenses", "savings")},
            })
    return {"people": persone, "family": insieme}


@router.get("/api/shared/totals")
def totali_condivisi(
    year: int = Query(ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12, description="Omesso = anno intero"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """I totali di chi ha acceso la condivisione, uno accanto all'altro."""
    condividono = session.scalars(
        select(User).where(User.shares_totals.is_(True)).order_by(User.id)
    ).all()
    return {
        "period": f"{year}-{month:02d}" if month else str(year),
        "people": [{
            "id": persona.id,
            "displayName": persona.display_name,
            "totals": _totali_di(persona.id, year, month),
        } for persona in condividono],
    }
