"""Cache locale per le quotazioni di mercato.

Salva ogni osservazione su `market_prices` indicizzata per (symbol, observed_on,
provider). Se chiedo un prezzo per una data già presente in cache, lo leggo dal
DB e non chiamo Yahoo. Se invece la data non è ancora stata osservata, scarico
l'ultima chiusura disponibile, la scrivo in cache e la restituisco.

La cache è per *data di osservazione*, non per data di fetch: questo permette
di riutilizzare la quotazione di un giorno anche a distanza di settimane.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .market_data import MarketDataError, fetch_yahoo_quote
from .models import MarketPrice


DEFAULT_PROVIDER = "yahoo"


def get_cached_price(
    session: Session,
    symbol: str,
    observed_on: date,
    provider: str = DEFAULT_PROVIDER,
) -> Optional[MarketPrice]:
    return session.scalar(
        select(MarketPrice).where(
            MarketPrice.symbol == symbol,
            MarketPrice.observed_on == observed_on,
            MarketPrice.provider == provider,
        )
    )


def get_or_fetch_price(
    session: Session,
    symbol: str,
    observed_on: date,
    provider: str = DEFAULT_PROVIDER,
    *,
    force_refresh: bool = False,
) -> MarketPrice:
    """Restituisce un MarketPrice per (symbol, observed_on).

    Se la cache contiene già quell'osservazione, la restituisce. Altrimenti
    chiama Yahoo, scrive la riga in `market_prices` e la restituisce. Con
    ``force_refresh=True`` la cache esistente viene sovrascritta.
    """
    if not symbol or not symbol.strip():
        raise MarketDataError("symbol required", code="invalid_symbol")
    clean = symbol.strip().upper()

    if not force_refresh:
        cached = get_cached_price(session, clean, observed_on, provider)
        if cached is not None:
            return cached

    quote = fetch_yahoo_quote(clean)
    # L'observed_on del provider può differire da quello richiesto (es. chiedo
    # oggi ma Yahoo restituisce l'ultima chiusura disponibile): salviamo la
    # riga per la data restituita dal provider, e poi se è diversa da quella
    # richiesta salviamo anche un "mirror" per la data richiesta con lo
    # stesso prezzo, così la prossima richiesta trova subito la cache.
    rows = []
    for target_date in ({quote.observed_on, observed_on}):
        existing = get_cached_price(session, clean, target_date, provider)
        if existing is None:
            row = MarketPrice(
                symbol=clean,
                provider=provider,
                observed_on=target_date,
                price=quote.price,
                currency=quote.currency,
            )
            session.add(row)
            rows.append(row)
        elif force_refresh and target_date == quote.observed_on:
            existing.price = quote.price
            existing.currency = quote.currency
            rows.append(existing)
    session.flush()
    primary = get_cached_price(session, clean, quote.observed_on, provider)
    if primary is None:
        raise MarketDataError("cache unavailable after refresh", code="unexpected")
    return primary


def list_cached_symbols(session: Session) -> list[dict]:
    """Riepilogo simboli in cache con ultima data disponibile e conteggio."""
    rows = session.execute(
        select(
            MarketPrice.symbol,
            MarketPrice.provider,
            MarketPrice.currency,
        ).order_by(MarketPrice.symbol)
    ).all()
    summary: dict[tuple[str, str], dict] = {}
    for symbol, provider, currency in rows:
        key = (symbol, provider)
        entry = summary.setdefault(key, {
            "symbol": symbol,
            "provider": provider,
            "currency": currency,
            "observations": 0,
            "last_observed_on": None,
        })
        entry["observations"] += 1
    # Secondo passaggio: per ogni simbolo trova l'ultima data.
    for (symbol, provider), entry in summary.items():
        latest = session.scalar(
            select(MarketPrice.observed_on)
            .where(MarketPrice.symbol == symbol, MarketPrice.provider == provider)
            .order_by(MarketPrice.observed_on.desc())
            .limit(1)
        )
        entry["last_observed_on"] = latest.isoformat() if latest else None
    return sorted(summary.values(), key=lambda x: x["symbol"])
