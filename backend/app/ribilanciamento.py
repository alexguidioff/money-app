"""Deriva dai pesi obiettivo, e cosa comprare o vendere per tornarci.

Solo aritmetica su posizioni gia' calcolate: nessuna colonna nuova, nessuna
scrittura, nessun accesso al database. La deriva si ricalcola a ogni richiesta,
come gia' fanno le posizioni da cui nasce.

Il segno dell'importo segue la formula ``deriva * totale``: positivo vuol dire
sopra il peso obiettivo, cioe' da vendere; negativo vuol dire sotto, da
comprare. Chi disegna la pagina decide il colore leggendo il segno, non
ricevendo una decisione gia' presa.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

# Sotto due punti di deriva non si propone niente. Ribilanciare per mezzo punto
# costa in commissioni piu' di quanto corregga, e un elenco di micro-ordini
# nasconde le due righe che contano davvero. E' una costante e non un campo da
# configurare: non e' una preferenza di chi usa l'app, e' il punto sotto il
# quale l'operazione non conviene a nessuno.
SOGLIA_DERIVA = Decimal("0.02")

# Quanto i pesi obiettivo possono discostarsi da 1 prima che sia un errore di
# configurazione e non un arrotondamento di chi li ha scritti.
TOLLERANZA_PESI = Decimal("0.005")

# I pesi si arrotondano a sei decimali, come la colonna che li contiene.
SEI_DECIMALI = Decimal("0.000001")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class PosizionePeso:
    """Una posizione come la vede il ribilanciamento.

    ``obiettivo`` a ``None`` vuol dire fuori dal piano: non e' zero, ed e' la
    differenza che evita di dire "vendi tutto cio' che non hai classificato".
    """

    nome: str
    valore: Decimal
    obiettivo: Decimal | None
    aperta: bool = True


@dataclass(frozen=True)
class RigaRiequilibrio:
    nome: str
    peso_attuale: Decimal
    obiettivo: Decimal
    deriva: Decimal
    importo: Decimal


@dataclass(frozen=True)
class Riequilibrio:
    totale: Decimal
    # La somma dei pesi obiettivo dichiarati: 1 e' il caso sano, e un valore
    # diverso e' l'unica cosa che rende sbagliati tutti i numeri sotto.
    pesi_dichiarati: Decimal
    righe: tuple[RigaRiequilibrio, ...]
    avvisi: tuple[str, ...]


def riequilibrio(posizioni: Iterable[PosizionePeso]) -> Riequilibrio:
    """Le sole righe fuori soglia, con quanto comprare o vendere.

    Il denominatore e' la somma dei soli strumenti **con** un peso obiettivo:
    metterci anche gli altri farebbe sembrare sotto peso tutto il resto per il
    solo fatto che esiste un titolo non ancora classificato.
    """
    dentro = [(p, p.obiettivo) for p in posizioni if p.aperta and p.obiettivo is not None]
    totale = sum((p.valore for p, _ in dentro), Decimal("0"))
    pesi = sum((obiettivo for _, obiettivo in dentro), Decimal("0"))
    # Non si normalizza in silenzio: se i pesi non tornano, chi ha sbagliato a
    # scriverli deve poterlo vedere, altrimenti l'errore resta nel profilo e
    # tutti i numeri calcolati sopra restano falsi.
    avvisi = () if abs(pesi - 1) <= TOLLERANZA_PESI else ("pesi_non_sommano_a_cento",)
    if totale <= 0:
        return Riequilibrio(totale=Decimal("0"), pesi_dichiarati=pesi, righe=(), avvisi=avvisi)
    righe = []
    for posizione, obiettivo in dentro:
        # Il conto viaggia a precisione piena e si arrotonda solo quello che si
        # mostra: arrotondare la deriva prima di moltiplicarla sposta gli
        # importi di qualche centesimo, e la somma di vendite e acquisti smette
        # di tornare.
        attuale = posizione.valore / totale
        deriva = attuale - obiettivo
        if abs(deriva) <= SOGLIA_DERIVA:
            continue
        righe.append(RigaRiequilibrio(
            nome=posizione.nome,
            peso_attuale=attuale.quantize(SEI_DECIMALI),
            obiettivo=obiettivo,
            deriva=deriva.quantize(SEI_DECIMALI),
            importo=(deriva * totale).quantize(CENT),
        ))
    return Riequilibrio(totale=totale, pesi_dichiarati=pesi, righe=tuple(righe), avvisi=avvisi)
