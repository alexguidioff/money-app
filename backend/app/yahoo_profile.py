"""Anagrafica strumenti da Yahoo (settori, composizione, titoli sottostanti).

L'endpoint quoteSummary richiede una stretta di mano non documentata: un cookie
da fc.yahoo.com e un "crumb" da /v1/test/getcrumb. E' lo stesso meccanismo usato
da yfinance. Non essendo un'API pubblica con contratto, Yahoo puo' cambiarla
senza preavviso: ogni errore viene quindi riportato al chiamante, che deve
mostrare l'allocazione come non disponibile invece di inventare dati.
"""

from __future__ import annotations

import json
import http.cookiejar
import threading
import urllib.request as request_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from .market_data import SYMBOL_PATTERN

# Deliberatamente scarno. La stringa Chrome completa e' quella che usa yfinance,
# e Yahoo la limita cosi' duramente da rispondere 429 a ogni richiesta, per
# sempre: era la ragione per cui l'allocazione non ha mai funzionato una volta.
BROWSER_UA = "Mozilla/5.0"
CRUMB_TTL = timedelta(hours=6)
# Dopo un blocco (429) inutile insistere: si riprova passata la finestra.
HANDSHAKE_COOLDOWN = timedelta(minutes=20)

# Yahoo usa chiavi snake_case per i settori dei fondi. Si normalizzano in
# etichette inglesi, che sono il vocabolario finanziario standard: tradurle
# lato server le renderebbe incoerenti con la lingua scelta nell'interfaccia.
SECTOR_LABELS = {
    "realestate": "Real Estate",
    "consumer_cyclical": "Consumer Cyclical",
    "basic_materials": "Basic Materials",
    "consumer_defensive": "Consumer Defensive",
    "technology": "Technology",
    "communication_services": "Communication Services",
    "financial_services": "Financial Services",
    "utilities": "Utilities",
    "industrials": "Industrials",
    "energy": "Energy",
    "healthcare": "Healthcare",
}


class YahooProfileError(RuntimeError):
    """La fonte non e' raggiungibile o ha cambiato formato.

    Porta un codice invece di una frase: i messaggi vanno tradotti
    nell'interfaccia, che conosce la lingua scelta dall'utente.
    """

    code = "unexpected"

    def __init__(self, message: str, *, code: str | None = None, retry_minutes: int | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.retry_minutes = retry_minutes


@dataclass
class _Session:
    opener: Any = None
    crumb: str = ""
    obtained_at: datetime | None = None
    blocked_until: datetime | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def expired(self) -> bool:
        return not self.crumb or self.obtained_at is None or datetime.now(timezone.utc) - self.obtained_at > CRUMB_TTL


_session = _Session()


class YahooRateLimited(YahooProfileError):
    """La fonte sta rifiutando le richieste: riprovare piu' tardi."""

    code = "rate_limited"


def _renew_session() -> None:
    if _session.blocked_until and datetime.now(timezone.utc) < _session.blocked_until:
        remaining = int((_session.blocked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
        raise YahooRateLimited("source temporarily unavailable", retry_minutes=remaining)
    jar = http.cookiejar.CookieJar()
    opener = request_module.build_opener(request_module.HTTPCookieProcessor(jar))
    headers = {"User-Agent": BROWSER_UA}
    # I cookie di sessione arrivano dalla home di Finance; fc.yahoo.com risponde
    # 404 e a volte non ne assegna nessuno, quindi si prova con entrambi.
    for seed in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
        try:
            opener.open(request_module.Request(seed, headers=headers), timeout=10)
        except (HTTPError, URLError, TimeoutError):
            continue
    try:
        response = opener.open(
            request_module.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=headers),
            timeout=10,
        )
        crumb = response.read().decode().strip()
    except HTTPError as error:
        if error.code == 429:
            _session.blocked_until = datetime.now(timezone.utc) + HANDSHAKE_COOLDOWN
            raise YahooRateLimited("rate limited by source", retry_minutes=int(HANDSHAKE_COOLDOWN.total_seconds() // 60)) from error
        raise YahooProfileError(f"handshake failed: HTTP {error.code}", code="handshake_failed") from error
    except (URLError, TimeoutError) as error:
        raise YahooProfileError(f"handshake failed: {error}", code="handshake_failed") from error
    if not crumb or "<" in crumb:
        raise YahooProfileError("invalid crumb", code="handshake_failed")
    _session.opener = opener
    _session.crumb = crumb
    _session.obtained_at = datetime.now(timezone.utc)
    _session.blocked_until = None


def _fetch_summary(symbol: str, modules: str) -> dict[str, Any]:
    with _session.lock:
        if _session.expired():
            _renew_session()
        opener, crumb = _session.opener, _session.crumb
    url = (
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}"
        f"?modules={modules}&crumb={quote(crumb)}"
    )
    headers = {"User-Agent": BROWSER_UA}
    try:
        with opener.open(request_module.Request(url, headers=headers), timeout=15) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            # Crumb scaduto o meccanismo cambiato: un solo nuovo tentativo.
            with _session.lock:
                _renew_session()
                opener, crumb = _session.opener, _session.crumb
            retry_url = (
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}"
                f"?modules={modules}&crumb={quote(crumb)}"
            )
            try:
                with opener.open(request_module.Request(retry_url, headers=headers), timeout=15) as response:
                    payload = json.load(response)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as retry_error:
                raise YahooProfileError(f"access denied: {retry_error}", code="handshake_failed") from retry_error
        else:
            raise YahooProfileError(f"unreachable: HTTP {error.code}", code="unreachable") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise YahooProfileError(f"unreachable: {error}", code="unreachable") from error

    summary = payload.get("quoteSummary") or {}
    if summary.get("error"):
        raise YahooProfileError(str(summary["error"]))
    results = summary.get("result") or []
    if not results:
        raise YahooProfileError("no data for symbol", code="no_data")
    return results[0]


def _raw(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_profile(symbol: str) -> dict[str, Any]:
    """Composizione dello strumento, normalizzata.

    Ritorna settori (peso 0-1), ripartizione azioni/obbligazioni/liquidita',
    paese per i singoli titoli e principali posizioni per i fondi.
    """
    clean = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(clean):
        raise YahooProfileError("invalid symbol", code="invalid_symbol")
    result = _fetch_summary(clean, "assetProfile,topHoldings,fundProfile")

    asset_profile = result.get("assetProfile") or {}
    top_holdings = result.get("topHoldings") or {}
    fund_profile = result.get("fundProfile") or {}

    sectors: dict[str, float] = {}
    for entry in top_holdings.get("sectorWeightings") or []:
        for key, value in entry.items():
            weight = _raw(value)
            if weight:
                sectors[SECTOR_LABELS.get(key, key.replace("_", " ").title())] = weight
    if not sectors and asset_profile.get("sector"):
        sectors[str(asset_profile["sector"])] = 1.0

    asset_mix = {
        "stock": _raw(top_holdings.get("stockPosition")) or 0.0,
        "bond": _raw(top_holdings.get("bondPosition")) or 0.0,
        "cash": _raw(top_holdings.get("cashPosition")) or 0.0,
        "other": _raw(top_holdings.get("otherPosition")) or 0.0,
    }

    holdings = []
    for item in (top_holdings.get("holdings") or [])[:12]:
        weight = _raw(item.get("holdingPercent"))
        if weight:
            holdings.append({
                "symbol": item.get("symbol"),
                "name": item.get("holdingName"),
                "weight": weight,
            })

    return {
        "symbol": clean,
        "sectors": sectors,
        "assetMix": asset_mix,
        "holdings": holdings,
        "country": asset_profile.get("country"),
        "category": fund_profile.get("categoryName") or fund_profile.get("family"),
        "isFund": bool(top_holdings or fund_profile),
    }
