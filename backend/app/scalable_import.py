"""Leggere l'elenco dei movimenti di Scalable, copiato dalla sua interfaccia.

Il testo non e' un file esportato: e' quello che si ottiene selezionando la
lista dei movimenti e incollandola. Questo modulo lo legge in sola lettura e
restituisce i movimenti di *una* giornata di borsa alla volta, con dentro tutto
quello che serve a importarli: data, tipo, strumento, quote, importo.

Il testo non e' una tabella: e' una sequenza di righe di data seguite dalle righe
dei movimenti di quel giorno, e le colonne sono separate da spazi. Il nome dello
strumento e' l'unica cosa a lunghezza variabile, quindi si legge per sottrazione:
si toglie l'importo dalla coda, si toglie il numero di quote, quello che resta e'
il nome.

Tre cose il testo non le dice, e vanno ricostruite:

- **il prezzo non c'e'**. Si ricava dall'importo diviso le quote. Le quote sono
  arrotondate a due decimali, quindi il prezzo e' approssimato: resta scritto
  nell'anteprima invece di essere spacciato per quello vero;
- **il verso del denaro** e' il **segno dell'importo**, non il tipo: la
  piattaforma scrive in negativo quello che esce, e il tipo non basta a dirlo -
  fra i versamenti ce n'e' uno negativo, "Deposito Return: ...", che e' la
  restituzione di un versamento ed e' denaro che esce. Ricavare il verso dal
  tipo lo conterebbe due volte col segno sbagliato: la somma degli importi
  così come sono scritti chiude a 25,90, che e' quello che resta sul conto;
- **le righe interne alla piattaforma**. Dentro la storia ci sono due giri di
  soldi che non sono ne' un versamento ne' un acquisto: il trasferimento dei
  titoli da un custode all'altro (le stesse quote escono e rientrano, con due
  valori diversi) e le due gambe in contanti che si annullano. Importarli
  scriverebbe nel ledger due operazioni mai avvenute, e i loro importi - 28,69
  euro in questo caso - diventerebbero contante che non esiste. Si riconoscono
  dalla forma, non dalla data: si scartano tutte e due le gambe.

Le grafie degli strumenti sono un capitolo a parte: lo stesso fondo cambia nome
nel tempo, e il nome cambia anche il giorno in cui il deposito si sposta da un
custode all'altro. Si raggruppano per somiglianza (vedi ``raggruppa``), ma e' una
proposta: l'anteprima la mostra e chi importa la conferma, perche' due fondi
diversi possono avere nomi che cominciano allo stesso modo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .statement_parsing import parse_amount, parse_date

# Le due gambe di uno spostamento interno della piattaforma distano al massimo
# un giorno, ma la contabilizzazione puo' slittare al giorno dopo: due giorni
# coprono il caso senza rischiare di accoppiare movimenti che non c'entrano.
GIORNI_RISCONTRO = 2

# I tipi come li scrive l'interfaccia, dal piu' lungo al piu' corto: si prende la
# prima riga che comincia con uno di questi, e "Trasferimento in entrata" non deve
# essere scambiato per un "Trasferisci".
TIPI = (
    "Piano di accumulo",
    "Trasferimento in entrata",
    "Trasferimento in uscita",
    "Deposito",
    "Prelievo",
    "Interesse",
    "Vendita",
    "Trasferisci",
    "Acquisto",
)

# Il verso delle **quote**, che non e' il segno dell'importo: le quote sono
# sempre scritte positive, e a dire da che parte vanno e' il tipo. Comprando
# escono soldi ed entrano titoli, vendendo il contrario; confonderli farebbe
# contare una vendita come un acquisto, e la posizione finale risulterebbe
# doppia. Il verso del **denaro** invece e' il segno dell'importo, e non si
# ricava da qui: vedi la nota in testa al modulo.
VERSO_QUOTE = {"Vendita": -1, "Trasferisci": -1, "Trasferimento in uscita": -1}
# Le righe che parlano di titoli invece che di contanti. Un tipo qui dentro senza
# quote e' una riga che il testo non ha finito di scrivere.
CON_QUOTE = frozenset({"Piano di accumulo", "Acquisto", "Vendita", "Trasferisci",
                       "Trasferimento in entrata", "Trasferimento in uscita"})
# Le due gambe dello spostamento interno dei titoli: stesse quote, versi opposti.
GAMBE_TITOLI = frozenset({"Trasferisci", "Trasferimento in entrata", "Trasferimento in uscita"})

# Un prefisso che sembra un giorno della settimana ("lunedì, "): sta solo davanti
# a una riga di data, e `parse_date` legge "14 settembre 2026" ma non "lunedì,".
GIORNO_SETTIMANA = re.compile(r"^[^\d,]{3,12},\s*")

# Le colonne di un movimento non sono separate da spazi: sono separate da un
# separatore di riga Unicode (U+2028), che `str.splitlines()` legge come un a
# capo. Leggendo cosi', un movimento si spezza in quattro righe - il tipo, il
# nome, le quote e l'importo - e nessuna delle quattro e' un movimento. Qui si
# legge per righe vere (quelle che finiscono con `\n`) e i separatori di colonna
# diventano spazi: e' l'unica differenza fra l'incollare e l'esportare.
COLONNE = re.compile("[  \v\f\x1c\x1d\x1e]+")
IMPORTO = re.compile(r"€\s*(-?[\d.,]+)\s*$")
QUOTE = re.compile(r"([\d.,]+)\s*az\.")
SCADUTO = re.compile(r"\bScaduto\s*$")

CENT = Decimal("0.01")
QUOTE_DECIMALI = Decimal("0.00000001")

# I pezzi di nome che dicono la forma del fondo, non quale fondo e': si tolgono
# per confrontare due grafie. Sono una proposta di lettura, non una verita': il
# confronto vero lo fa chi importa guardando l'anteprima.
SIGLE = (
    "ucits etf", "ucits", "etf", "etc", "etp", "acc", "inc", "dist", "disp",
    "capitalisation", "capitalization", "distribution", "a", "b", "c", "1c", "usd",
)
# Un nome piu' corto contenuto in uno piu' lungo vale come lo stesso strumento
# (il fondo ha aggiunto "Inc -B" o "Aggregate USD" dopo il cambio di custode).
# E' il solo confronto che unisce tutte le grafie di questo elenco senza unirne
# due diverse, ed e' anche il piu' rischioso: due fondi dello stesso emittente
# che condividono l'inizio del nome finirebbero in uno. Per questo il
# raggruppamento si conferma in anteprima.
MINIMO_PAROLE = 2


def _numero(testo: str | None) -> Decimal | None:
    """Un importo o una quantita' del testo, coi separatori che usa l'italiano.

    Il lavoro vero lo fa `parse_amount`, che l'app usa gia' per gli estratti
    conto: legge "1.210,64" e "-120,00" senza che qui si ripeta la regola. Il
    passaggio dal float e' per non avere Decimal(1210.6400000000001): la cifra
    scritta nel testo ha due decimali e torna esatta.
    """
    if testo is None:
        return None
    valore = Decimal(str(parse_amount(testo)))
    return valore.quantize(CENT)


def _data(riga: str) -> date | None:
    """La data di una riga, tolto il giorno della settimana che la precede."""
    # Prima senza il giorno della settimana, che qui c'e' sempre: chiedere a
    # `parse_date` di leggere "lunedì, 14 settembre 2026" e' chiedergli di
    # sbagliare per poi correggerlo.
    trovata = parse_date(GIORNO_SETTIMANA.sub("", riga, count=1)) or parse_date(riga)
    return date.fromisoformat(trovata) if trovata else None


def _righe_grezze(testo: str) -> tuple[list[dict], list[dict]]:
    """Le righe del testo, lette come stanno: senza scartarne nessuna.

    Stare larghi qui e' voluto: gli scarti si decidono dopo, dove si possono
    motivare. Una riga che non si capisce e' un rifiuto da mostrare, non una
    riga da far sparire.
    """
    movimenti: list[dict] = []
    rifiuti: list[dict] = []
    giorno: date | None = None
    for numero, grezza in enumerate(testo.split("\n"), 1):
        riga = " ".join(COLONNE.sub(" ", grezza).split())
        if not riga:
            continue
        # Una riga di data non porta importi ne' quote: senza questo controllo
        # le cifre di un movimento potrebbero leggersi come una data, e il
        # giorno di tutto quello che segue diventerebbe quello.
        if "€" not in riga and "az." not in riga and (quando := _data(riga)) is not None:
            giorno = quando
            continue
        tipo = next((t for t in TIPI if riga.startswith(t)), None)
        if tipo is None:
            rifiuti.append({"riga": numero, "testo": riga[:120], "motivo": "rigaSconosciuta"})
            continue
        resto = riga[len(tipo):].strip()
        importo = None
        if (trovato := IMPORTO.search(resto)) is not None:
            importo = _numero(trovato.group(1))
            resto = resto[:trovato.start()].strip()
        else:
            # "Scaduto" e' uno stato, non un tipo: dice che quell'ordine non e'
            # stato eseguito, e senza importo non c'e' niente da importare.
            resto = SCADUTO.sub("", resto).strip()
        quote = _numero(trovato.group(1)) if (trovato := QUOTE.search(resto)) else None
        nome = resto[:trovato.start()].strip() if trovato else resto
        movimenti.append({"riga": numero, "giorno": giorno, "tipo": tipo, "nome": nome,
                          "quote": quote, "importo": importo})
    return movimenti, rifiuti


def _accoppia(prime: list[dict], seconde: list[dict], chiave, giorni: int) -> list[tuple[dict, dict]]:
    """Accoppia due elenchi di righe, dalla piu' vecchia, una volta per riga.

    Una riga vale per un solo compagno: senza questo, tre righe dello stesso
    importo si prenderebbero a vicenda e l'ultima resterebbe sola per un motivo
    che non esiste. Si prende il compagno piu' vicino nel tempo, che e' quello
    con cui la riga ha piu' probabilita' di essere lo stesso spostamento.
    """
    libere = list(seconde)
    coppie: list[tuple[dict, dict]] = []
    for prima in sorted(prime, key=lambda r: (r["giorno"] or date.max, r["riga"])):
        candidati = [s for s in libere if s["giorno"] and prima["giorno"]
                     and chiave(prima, s) and abs((prima["giorno"] - s["giorno"]).days) <= giorni]
        if not candidati:
            continue
        scelto = min(candidati, key=lambda s: (abs((prima["giorno"] - s["giorno"]).days), s["riga"]))
        libere.remove(scelto)
        coppie.append((prima, scelto))
    return coppie


def scarta_interne(movimenti: list[dict]) -> list[dict]:
    """Le righe che non sono movimenti dell'utente, con il motivo dello scarto.

    Sono due giri di soldi che avvengono fra due conti della piattaforma, e per
    la storia dell'utente non sono ne' un'entrata ne' una spesa:

    - i **titoli** che escono e rientrano con le stesse quote: e' il deposito che
      cambia custode. Le quote tornano come erano, ma i due importi no - sono lo
      stesso portafoglio valutato in due giorni diversi - quindi importarli
      aggiungerebbe denaro che non e' mai arrivato;
    - i **contanti** che si annullano: un versamento e un prelievo uguali e
      opposti a distanza di un giorno.

    Si scartano tutte e due le gambe, sempre: una gamba sola muove il portafoglio
    o il contante di una cifra che non e' mai passata di li'.
    """
    scarti: dict[int, str] = {}
    titoli = [r for r in movimenti if r["tipo"] in GAMBE_TITOLI and r["quote"] is not None]
    uscite = [r for r in titoli if r["tipo"] != "Trasferimento in entrata"]
    entrate = [r for r in titoli if r["tipo"] == "Trasferimento in entrata"]
    stesso_strumento = lambda a, b: a["nome"].casefold() == b["nome"].casefold() and a["quote"] == b["quote"]
    for uscita, entrata in _accoppia(uscite, entrate, stesso_strumento, GIORNI_RISCONTRO):
        scarti[uscita["riga"]] = scarti[entrata["riga"]] = "coppiaInterna"

    versamenti = [r for r in movimenti if r["tipo"] == "Deposito" and r["importo"] is not None]
    prelievi = [r for r in movimenti if r["tipo"] == "Prelievo" and r["importo"] is not None]
    si_annullano = lambda a, b: a["importo"] == -b["importo"]
    for versamento, prelievo in _accoppia(versamenti, prelievi, si_annullano, GIORNI_RISCONTRO):
        scarti[versamento["riga"]] = scarti[prelievo["riga"]] = "versamentiAnnullati"

    return [{"riga": r["riga"], "testo": f'{r["tipo"]} {r["nome"]}'.strip(), "motivo": motivo}
            for r, motivo in ((r, scarti[r["riga"]]) for r in movimenti if r["riga"] in scarti)]


def _nucleo(nome: str) -> str:
    """Il nome di uno strumento senza le sigle che dicono la sua forma."""
    parole = [p for p in re.split(r"[^\w]+", nome.casefold()) if p]
    tenute = [p for p in parole if p not in SIGLE]
    # Un nome fatto solo di sigle non si azzera: meglio il nome intero che una
    # chiave vuota, che unirebbe fra loro tutti gli strumenti senza nome.
    return " ".join(tenute or parole)


def raggruppa(movimenti: list[dict]) -> list[dict]:
    """Le grafie degli strumenti, raggruppate in strumenti.

    Due grafie sono lo stesso strumento se il loro nucleo (il nome senza sigle)
    coincide, o se uno dei due e' l'inizio dell'altro: il fondo che ha aggiunto
    una parola al nome non e' un fondo nuovo.

    Il nome scelto e' quello dell'**ultima** riga del gruppo, cioe' come lo
    strumento si chiama adesso: e' il nome che si confronta con quello che
    l'interfaccia della piattaforma mostra oggi, ed e' quello che l'app scrive
    sia sullo strumento sia sulle righe. Il legame fra strumento e righe e' per
    nome (minuscolo), quindi due grafie non raggruppate diventano due posizioni.
    """
    per_nucleo: dict[str, list[dict]] = defaultdict(list)
    for movimento in movimenti:
        if movimento["quote"] is None:
            continue
        per_nucleo[_nucleo(movimento["nome"])].append(movimento)

    gruppi: list[dict] = []
    for nucleo, righe in per_nucleo.items():
        parole = set(nucleo.split())
        # Il gruppo che contiene il nucleo piu' corto che comincia allo stesso
        # modo: e' l'unico confronto che unisce tutte le grafie di uno stesso
        # fondo. Il minimo di parole serve a non unire "iShares" con "iShares
        # Global": un nome di una parola sola e' un emittente, non un fondo.
        gruppo = next((g for g in gruppi
                       if (g["parole"] <= parole or parole <= g["parole"])
                       and min(len(g["parole"]), len(parole)) >= MINIMO_PAROLE), None)
        if gruppo is None:
            gruppi.append({"parole": parole, "righe": list(righe)})
        else:
            gruppo["parole"] |= parole
            gruppo["righe"] += righe

    fuori: list[dict] = []
    for gruppo in gruppi:
        righe = sorted(gruppo["righe"], key=lambda r: (r["giorno"] or date.max, r["riga"]))
        ultima = righe[-1]
        # Le quote finali: quelle comprate meno quelle vendute. E' il numero che
        # si confronta con quello che la piattaforma mostra oggi, ed e' l'unica
        # prova che l'import non ha perso pezzi per strada.
        quote = sum((r["quote"] * VERSO_QUOTE.get(r["tipo"], 1) for r in righe),
                    Decimal(0)).quantize(QUOTE_DECIMALI)
        fuori.append({
            "nome": ultima["nome"],
            "grafie": sorted({r["nome"] for r in righe}),
            "quote": quote,
            "righe": len(righe),
            "prima": (righe[0]["giorno"].isoformat() if righe[0]["giorno"] else None),
            "ultima": (ultima["giorno"].isoformat() if ultima["giorno"] else None),
        })
    return sorted(fuori, key=lambda g: g["nome"].casefold())


def leggi_elenco(testo: str) -> dict:
    """L'elenco letto: i movimenti, le righe scartate con il motivo, gli strumenti.

    Ogni movimento porta il **nome di come e' scritto nel testo**: il nome dello
    strumento si decide dopo, insieme a chi importa (vedi ``raggruppa``). Le
    righe scartate hanno un motivo, cosi' l'anteprima puo' dire *perche'* - un
    elenco di rifiuti senza motivi e' un elenco di cose che non si possono
    sistemare.
    """
    movimenti, rifiuti = _righe_grezze(testo)
    scarti = scarta_interne(movimenti)
    per_riga = {rifiuto["riga"]: rifiuto["motivo"] for rifiuto in scarti}
    tenuti: list[dict] = []
    for movimento in movimenti:
        motivo = per_riga.get(movimento["riga"])
        if motivo is None and movimento["giorno"] is None:
            # Una riga di movimento senza la data sopra: succede se il testo
            # incollato comincia a meta' di un giorno.
            motivo = "rigaSenzaData"
        if motivo is None and movimento["importo"] is None:
            motivo = "rigaSenzaImporto"
        if motivo is None and movimento["quote"] is None and movimento["tipo"] in CON_QUOTE:
            # Un titolo senza quote non e' un titolo: il nome che si leggerebbe
            # sarebbe il resto di una riga, non lo strumento.
            motivo = "rigaSenzaQuote"
        if motivo is None and not movimento["nome"] and movimento["quote"] is not None:
            motivo = "strumentoSenzaNome"
        if motivo is not None:
            rifiuti.append({"riga": movimento["riga"],
                            "testo": f'{movimento["tipo"]} {movimento["nome"]}'.strip()[:120],
                            "motivo": motivo})
            continue
        tenuti.append(movimento)
    rifiuti.sort(key=lambda r: r["riga"])
    return {"movimenti": tenuti, "rifiuti": rifiuti, "strumenti": raggruppa(tenuti)}
