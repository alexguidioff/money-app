"""Cache locale per le quotazioni di mercato.

Salva ogni osservazione su `market_prices` indicizzata per (symbol, observed_on,
provider). Se chiedo un prezzo per una data già presente in cache, lo leggo dal
DB e non chiamo Yahoo. Se invece la data non è ancora stata osservata, scarico
l'ultima chiusura disponibile, la scrivo in cache e la restituisco.

La cache è per *data di osservazione*, non per data di fetch: questo permette
di riutilizzare la quotazione di un giorno anche a distanza di settimane.

Se il fornitore primario non risponde si prova la riserva, quando è
configurata: un simbolo non deve restare senza prezzo perché una sola fonte
l'ha rifiutato.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .market_data import (
    MarketDataError,
    MarketQuote,
    fetch_reserve_quote,
    fetch_yahoo_quote,
    reserve_provider_configured,
)
from .models import MarketPrice


DEFAULT_PROVIDER = "yahoo"


def get_cached_price(
    session: Session,
    symbol: str,
    observed_on: date,
    provider: str | None = DEFAULT_PROVIDER,
) -> Optional[MarketPrice]:
    """La riga in cache per quel giorno, o ``None``.

    Con ``provider=None`` si accetta qualunque provenienza: serve dopo che la
    riserva ha risposto, altrimenti la riga salvata sotto il suo nome non
    verrebbe mai ritrovata e ogni richiesta tornerebbe in rete.
    """
    conditions = [MarketPrice.symbol == symbol, MarketPrice.observed_on == observed_on]
    if provider is not None:
        conditions.append(MarketPrice.provider == provider)
    return session.scalar(select(MarketPrice).where(*conditions))


def _fetch_quote(symbol: str) -> MarketQuote:
    """Il primario, e su suo errore la riserva se esiste.

    ponytail: due fornitori in sequenza, nessun voto di maggioranza e nessuna
    preferenza per mercato: vince il primo che risponde. Se un giorno un prezzo
    sbagliato costasse piu' di un prezzo mancante, servira' un terzo parere e
    una regola per scegliere fra i due — non prima di aver visto in cache
    quanto spesso i due disaccordano.

    Un fallimento di entrambi non e' zero: non si scrive niente e chi ha
    chiamato riceve il motivo di tutti e due, cosi' la pagina puo' dire cosa
    non ha funzionato invece di mostrare un portafoglio fermo al giorno prima.
    """
    try:
        return fetch_yahoo_quote(symbol)
    except MarketDataError as primary_error:
        # Senza chiave configurata la riserva non esiste: si solleva l'errore
        # del primario, identico a prima che questa catena esistesse, perche'
        # una riserva che non c'e' non ha niente da aggiungere al motivo.
        if not reserve_provider_configured():
            raise
        try:
            return fetch_reserve_quote(symbol)
        except MarketDataError as reserve_error:
            raise MarketDataError(f"{primary_error} / riserva: {reserve_error}",
                                  code=primary_error.code) from reserve_error


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
    chiama il fornitore — e, se quello non risponde, la riserva — scrive la
    riga in `market_prices` e la restituisce. Con ``force_refresh=True`` la
    cache esistente viene sovrascritta.
    """
    if not symbol or not symbol.strip():
        raise MarketDataError("symbol required", code="invalid_symbol")
    clean = symbol.strip().upper()

    if not force_refresh:
        cached = get_cached_price(session, clean, observed_on, provider)
        if cached is None:
            cached = get_cached_price(session, clean, observed_on, None)
        if cached is not None:
            return cached

    quote = _fetch_quote(clean)
    # L'observed_on del provider può differire da quello richiesto (es. chiedo
    # oggi ma Yahoo restituisce l'ultima chiusura disponibile): salviamo la
    # riga per la data restituita dal provider, e poi se è diversa da quella
    # richiesta salviamo anche un "mirror" per la data richiesta con lo
    # stesso prezzo, così la prossima richiesta trova subito la cache.
    # La riga porta il fornitore che ha risposto davvero, non quello chiesto:
    # è l'unico modo per sapere da dove viene un prezzo quando due fonti non
    # concordano.
    rows = []
    for target_date in ({quote.observed_on, observed_on}):
        existing = get_cached_price(session, clean, target_date, None)
        if existing is None:
            row = MarketPrice(
                symbol=clean,
                provider=quote.provider,
                observed_on=target_date,
                price=quote.price,
                currency=quote.currency,
            )
            session.add(row)
            rows.append(row)
        elif force_refresh and target_date == quote.observed_on:
            existing.price = quote.price
            existing.currency = quote.currency
            existing.provider = quote.provider
            rows.append(existing)
    session.flush()
    primary = get_cached_price(session, clean, quote.observed_on, None)
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
