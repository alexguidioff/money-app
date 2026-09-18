"""Le regole che propongono una categoria durante l'import, e imparano dallo storico.

Due meta': il motore, che decide la categoria di una riga di estratto conto; e
l'apprendimento, che legge i movimenti gia' registrati e propone le regole che
li descrivono. Stanno insieme perche' condividono la normalizzazione della
descrizione - se il raggruppamento usasse una regola e il confronto un'altra,
le proposte non combacerebbero mai con le regole create da quelle proposte.

Le regole si applicano **solo** nell'anteprima dell'import: riempiono una
casella che l'utente vede e puo' cambiare riga per riga. Nessuna regola scrive
in database senza essere passata sotto gli occhi di chi importa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BudgetPlan, CategorizationRule, LookupOption, Transaction

# Sotto questa soglia una descrizione ripetuta non e' un'abitudine: sono due
# righe capitate per caso, e una regola costruita su due righe sbaglia il
# doppio delle volte che indovina.
MIN_OCCORRENZE = 3
# Sopra questa quota la categoria e' quasi sempre la stessa: la regola si
# propone gia' spuntata. Sotto, resta da controllare a mano.
QUOTA_SICURA = 0.90
QUOTA_INCERTA = 0.60
# Non e' una funzione, e' una cintura: una regex patologica moltiplicata per
# righe illimitate e' l'unico modo in cui questa funzione puo' far male.
MAX_REGOLE = 200
# I tipi che una regola puo' scegliere. Gli spostamenti non hanno categoria.
TIPI_CON_CATEGORIA = ("Expenses", "Income")

_SPAZI = re.compile(r"\s+")
# I gruppi di voci fra cui l'app pesca le categorie da scegliere.
GRUPPI_CATEGORIE = ("categories_expenses", "categories_income", "categories_savings")


def categorie_ammesse(session: Session) -> set[str]:
    """Le categorie che l'interfaccia offre da scegliere, tutte in un insieme.

    Il vocabolario di partenza, quelle gia' usate nei movimenti e quelle
    pianificate a budget: sono esattamente le voci che finiscono nell'elenco a
    tendina del modulo movimento. Rifiutare una categoria che l'elenco propone
    sarebbe un errore che chi usa l'app non puo' capire ne' correggere.
    """
    valori = set(session.scalars(select(LookupOption.value)
                                 .where(LookupOption.option_group.in_(GRUPPI_CATEGORIE))).all())
    valori.update(session.scalars(select(Transaction.category).distinct()).all())
    valori.update(session.scalars(select(BudgetPlan.category).distinct()).all())
    return {valore.strip() for valore in valori if valore and valore.strip()}


def normalizza(descrizione: str | None) -> str:
    """La descrizione ridotta alla forma con cui si confronta e si raggruppa.

    Minuscole e spazi multipli a uno solo, niente di piu': nel database di
    riferimento la lunghezza media di una descrizione e' sedici caratteri e le
    ripetizioni sono letterali. Togliere altro (numeri, date) unirebbe
    descrizioni che sono movimenti diversi.
    """
    return _SPAZI.sub(" ", (descrizione or "").strip()).casefold()


@dataclass(frozen=True)
class RegolaCompilata:
    """Una regola pronta all'uso: la regex e' gia' compilata.

    ``pattern`` resta com'e' stato scritto, perche' e' quello che si mostra
    all'utente; ``chiave`` e' la versione normalizzata con cui si confronta.
    Una regola con ``is_regex`` acceso e ``regex`` a None non si e' potuta
    compilare: non si applica e finisce fra quelle scartate.
    """

    id: int
    pattern: str
    categoria: str
    transaction_type: str | None
    min_amount: Decimal | None
    max_amount: Decimal | None
    is_regex: bool = False
    chiave: str = ""
    regex: re.Pattern[str] | None = None
    # Serve a tenere l'ordine con cui si mostrano le regole scartate.
    scartata: bool = False


def _compila(riga: CategorizationRule) -> RegolaCompilata:
    """Una regola del database pronta all'uso, senza poter far fallire l'import.

    Una regex malformata - salvata forzando il database, perche' la scrittura
    la rifiuta - non deve fermare l'import di un estratto conto: si compila
    qui una volta sola, e se non riesce la regola si salta.
    """
    if riga.is_regex:
        try:
            return RegolaCompilata(id=riga.id, pattern=riga.pattern, categoria=riga.category,
                                   transaction_type=riga.transaction_type, min_amount=riga.min_amount,
                                   max_amount=riga.max_amount, is_regex=True,
                                   regex=re.compile(riga.pattern, re.IGNORECASE))
        except re.error:
            return RegolaCompilata(id=riga.id, pattern=riga.pattern, categoria=riga.category,
                                   transaction_type=riga.transaction_type, min_amount=riga.min_amount,
                                   max_amount=riga.max_amount, is_regex=True, scartata=True)
    return RegolaCompilata(id=riga.id, pattern=riga.pattern, categoria=riga.category,
                           transaction_type=riga.transaction_type, min_amount=riga.min_amount,
                           max_amount=riga.max_amount, chiave=normalizza(riga.pattern))


def carica_regole(session: Session) -> list[RegolaCompilata]:
    """Tutte le regole attive, in ordine di priorita', compilate una volta per import.

    Si chiama una volta prima del ciclo sulle righe: compilare la stessa regex
    per ognuna delle trecento righe di un estratto conto con ottanta regole e'
    la differenza fra un'anteprima immediata e una che si fa aspettare.
    """
    return [_compila(riga) for riga in session.scalars(
        select(CategorizationRule)
        .where(CategorizationRule.active.is_(True))
        .order_by(CategorizationRule.position, CategorizationRule.id)).all()]


def scartate(regole: list[RegolaCompilata]) -> list[str]:
    """I pattern delle regole che non si sono potute compilare, da segnalare."""
    return [regola.pattern for regola in regole if regola.scartata]


def applica(regole: list[RegolaCompilata], descrizione: str | None, tipo: str,
            importo: Decimal) -> tuple[str, str] | None:
    """La categoria della prima regola che combacia, col pattern che l'ha decisa.

    Vince la prima, non la piu' specifica: l'ordine e' una scelta dell'utente e
    deve restare leggibile guardando l'elenco. ``None`` vuol dire che nessuna
    regola si e' pronunciata e la categoria resta quella di prima.
    """
    testo = normalizza(descrizione)
    importo = abs(importo)
    for regola in regole:
        if regola.scartata:
            continue
        if regola.transaction_type is not None and regola.transaction_type != tipo:
            continue
        if regola.min_amount is not None and importo < regola.min_amount:
            continue
        if regola.max_amount is not None and importo > regola.max_amount:
            continue
        if regola.is_regex:
            # Il pattern vuoto non e' una regex valida per l'utente ma lo e' per
            # re: combacerebbe con ogni riga.
            if not regola.pattern or regola.regex.search(descrizione or "") is None:
                continue
        elif not regola.chiave or regola.chiave not in testo:
            continue
        return regola.categoria, regola.pattern
    return None
