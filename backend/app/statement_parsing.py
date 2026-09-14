"""Lettura di importi e date dagli estratti conto: una sola implementazione,
usata sia dal parser PDF sia da quello CSV."""

from __future__ import annotations

import re
from datetime import datetime

DATE_FORMATS = [
    '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
    '%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d',
    '%d/%m/%y', '%d-%m-%y', '%d.%m.%y',
    '%Y%m%d',
]


def parse_amount(amount_str: str) -> float:
    """Converte un importo in float, qualunque sia la convenzione dei separatori.

    L'ultimo separatore vale come decimale solo se lo seguono una o due cifre:
    cosi' `1.500` resta millecinquecento e `1.500,00` non finisce a zero.
    """
    if not amount_str:
        return 0.0
    testo = str(amount_str).strip()
    if not testo:
        return 0.0
    # Le banche scrivono il negativo in tre modi: -100, 100-, (100).
    negativo = '-' in testo or ('(' in testo and ')' in testo)
    pulito = re.sub(r'[^\d,.]', '', testo)
    if not pulito:
        return 0.0
    separatore = max(pulito.rfind(','), pulito.rfind('.'))
    if separatore == -1:
        numero = pulito
    elif len(pulito) - separatore - 1 in (1, 2):
        numero = re.sub(r'[,.]', '', pulito[:separatore]) + '.' + pulito[separatore + 1:]
    else:
        numero = re.sub(r'[,.]', '', pulito)
    try:
        valore = float(numero)
    except ValueError:
        return 0.0
    return -valore if negativo else valore


def parse_date(date_str: str) -> str | None:
    """Data in ISO, o None se non e' riconoscibile. Non inventa la data di oggi."""
    if not date_str:
        return None
    pulito = re.sub(r'[^\d\-/.]', '', str(date_str).strip())
    for formato in DATE_FORMATS:
        try:
            return datetime.strptime(pulito, formato).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None
