"""Small, dependency-free market data adapter with a database-backed cache.

The provider is intentionally isolated here: changing source later won't touch
the ledger nor portfolio calculations.  Yahoo's chart endpoint is used only
when the user has configured a provider symbol for an instrument.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.^=\-]{1,80}$")


class MarketDataError(RuntimeError):
    """Errore della fonte quotazioni.

    Porta un codice: la frase da mostrare la costruisce l'interfaccia nella
    lingua scelta dall'utente.
    """

    def __init__(self, message: str, *, code: str = "unexpected") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    price: float
    currency: str | None
    observed_on: date
    provider: str = "yahoo"


@dataclass(frozen=True)
class SymbolMatch:
    symbol: str
    name: str
    exchange: str
    quote_type: str


def fetch_price_history(symbol: str, *, years: int = 10) -> list[tuple[date, float, str | None]]:
    """Chiusure di fine mese, per ricostruire il valore storico.

    Si scaricano le barre GIORNALIERE e si tiene l'ultima chiusura di ogni
    mese: le barre mensili di Yahoo sono datate con l'inizio del periodo e in
    fuso locale finiscono sull'ultimo giorno del mese precedente, cosi' una
    lettura ingenua valorizza ogni mese col prezzo del mese dopo.
    """
    clean_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(clean_symbol):
        raise MarketDataError("invalid symbol", code="invalid_symbol")
    request = Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(clean_symbol)}?range={int(years)}y&interval=1d",
        headers={"User-Agent": "Money-local/1.0"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MarketDataError("quote source unreachable", code="unreachable") from error
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise MarketDataError("no history available", code="no_data")
    currency = result.get("meta", {}).get("currency")
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    timestamps = result.get("timestamp") or []
    by_month: dict[tuple[int, int], tuple[date, float]] = {}
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        observed_on = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        key = (observed_on.year, observed_on.month)
        previous = by_month.get(key)
        if previous is None or observed_on > previous[0]:
            by_month[key] = (observed_on, float(close))
    points = [(observed_on, close, currency) for observed_on, close in sorted(by_month.values())]
    if not points:
        raise MarketDataError("no valid closes in history", code="no_data")
    return points


def search_yahoo_symbols(query: str, limit: int = 8) -> list[SymbolMatch]:
    """Cerca un titolo per nome comune e restituisce i simboli candidati.

    Serve a non dover conoscere a memoria il ticker: si scrive "Berkshire
    Hathaway" e si sceglie fra i risultati, evitando di salvare un simbolo
    inesistente o quotato su una borsa diversa da quella che si possiede.
    """
    clean = (query or "").strip()
    if len(clean) < 2:
        raise MarketDataError("query too short", code="query_too_short")
    request = Request(
        f"https://query1.finance.yahoo.com/v1/finance/search?q={quote(clean)}&quotesCount={max(1, min(limit, 20))}&newsCount=0",
        headers={"User-Agent": "Money-local/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MarketDataError(f"search failed: {error}", code="unreachable") from error
    matches = []
    for item in payload.get("quotes", []):
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or not SYMBOL_PATTERN.fullmatch(symbol.upper()):
            continue
        matches.append(SymbolMatch(
            symbol=symbol.upper(),
            name=str(item.get("shortname") or item.get("longname") or "").strip(),
            exchange=str(item.get("exchDisp") or item.get("exchange") or "").strip(),
            quote_type=str(item.get("quoteType") or "").strip(),
        ))
    return matches


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    currency: str | None
    instrument_type: str | None   # ETF, EQUITY, CRYPTOCURRENCY...
    exchange: str | None          # borsa di quotazione (Milan, NYSE...)
    long_name: str | None


def fetch_instrument_profile(symbol: str) -> InstrumentProfile:
    """Anagrafica minima ricavabile senza autenticazione.

    L'endpoint chart espone valuta, tipo di strumento, borsa e nome esteso.
    Settore e area geografica del sottostante starebbero in quoteSummary, che
    pero' risponde 401 senza cookie+crumb: restano quindi manuali.
    """
    clean_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(clean_symbol):
        raise MarketDataError("invalid symbol", code="invalid_symbol")
    request = Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(clean_symbol)}?range=1d&interval=1d",
        headers={"User-Agent": "Money-local/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MarketDataError("quote source unreachable", code="unreachable") from error
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise MarketDataError("instrument unavailable", code="no_data")
    meta = result.get("meta", {})
    return InstrumentProfile(
        symbol=clean_symbol,
        currency=meta.get("currency"),
        instrument_type=meta.get("instrumentType"),
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
        long_name=meta.get("longName") or meta.get("shortName"),
    )


def fetch_yahoo_quote(symbol: str) -> MarketQuote:
    clean_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(clean_symbol):
        raise MarketDataError("invalid symbol", code="invalid_symbol")
    request = Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(clean_symbol)}?range=5d&interval=1d",
        headers={"User-Agent": "Money-local/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MarketDataError("quote source unreachable", code="unreachable") from error
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise MarketDataError("no quote available", code="no_data")
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    timestamps = result.get("timestamp") or []
    for timestamp, close in reversed(list(zip(timestamps, closes))):
        if close is not None:
            return MarketQuote(
                symbol=clean_symbol,
                price=float(close),
                currency=result.get("meta", {}).get("currency"),
                observed_on=datetime.fromtimestamp(timestamp, tz=timezone.utc).date(),
            )
    raise MarketDataError("no valid close returned", code="no_data")
