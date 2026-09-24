"""Leggere un backup di FastBudget: le stesse righe grezze di un estratto conto.

Il file che FastBudget esporta e' un database SQLite con dentro tutta la storia
di chi lo usa: conti, categorie, movimenti. Qui si legge in sola lettura e si
traduce in righe della stessa forma che produce il parser CSV, cosi' l'anteprima
(`statement_preview`), il controllo dei doppioni (`_Doppioni`) e il salvataggio
restano quelli che l'app usa gia' per gli estratti conto: nessun secondo
vocabolario da tenere allineato.

Il file non si scrive mai. Non e' una formalita': quel backup e' l'unica copia
che chi lo carica ha della sua storia, e un lettore che scrive e' un lettore che
puo' rovinarla.

Tre cose il file non le dice, e vanno dedotte:

- **le due gambe di un trasferimento sono due righe separate**, senza niente che
  le leghi (`onlineId` e `bank_sync_id` sono vuoti). Si ricompongono per conti,
  verso opposto e vicinanza di data: importarle tutte e due come trasferimenti a
  se' stanti conterebbe due volte lo stesso giro di soldi, perche' il movimento
  dell'app muove il denaro da tutte e due le parti;
- **la controparte puo' non esistere piu' nel file**: le righe che nominano un
  conto non piu' presente restano trasferimenti a una gamba sola. Toglierle
  farebbe mentire il saldo del conto da cui i soldi sono usciti;
- **le categorie sono un albero italiano**, e l'app ne ha uno suo in inglese. La
  corrispondenza sta qui sotto, in due tabelle; quello che non corrisponde tiene
  il nome che ha, sotto la radice mappata.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .migrations import ALBERO_ENTRATE, ALBERO_SPESE

# Quanti giorni possono separare le due gambe dello stesso trasferimento. E' la
# soglia con cui l'app riconosce gia' i doppioni: una banca registra alla
# contabilizzazione e l'altra il giorno dopo, oltre quella distanza non sono
# piu' lo stesso giro di soldi.
GIORNI_ACCOPPIAMENTO = 5

# Il nome con cui FastBudget segna un trasferimento fra conti: e' una categoria
# come le altre, ed e' l'unico modo che il file ha di dire "questi soldi si sono
# spostati" invece di "questi soldi sono usciti".
CATEGORIA_TRASFERIMENTO = "Transfer between accounts"

# Dove finisce un movimento quando la sua categoria non e' quella del suo verso:
# un "Pranzi/Cene" che nel file sta fra le entrate non puo' atterrare
# nell'albero delle spese, o sparirebbe da tutte le schermate che guardano le
# entrate. E' il segnaposto dell'albero giusto, dove l'utente lo sistema.
SEGNALIBRI = {False: "Other Expenses", True: "Other Income"}

# Le radici dei due alberi dell'app, per riconoscere a quale dei due appartiene
# una categoria mappata. L'elenco non si scrive qui: sono gli alberi veri, e una
# seconda copia sarebbe quella che un giorno resta indietro.
RADICI_ENTRATE = frozenset(ALBERO_ENTRATE)
RADICI_SPESE = frozenset(ALBERO_SPESE)

# Dove va una voce del file, quando nell'albero dell'app ne esiste una che dice
# la stessa cosa: (radice dell'app, sottocategoria) - `None` vuol dire "la radice
# stessa", perche' una voce generica del file non ha una sottocategoria piu'
# precisa dove andare. Un nome assente da questa tabella tiene il suo nome
# italiano e nasce sotto la radice mappata della sua voce (vedi ``_categoria``).
VOCI: dict[str, tuple[str, str | None]] = {
    # Spese
    "Cibo/Bevande": ("Food & Dining", None),
    "Pranzi/Cene": ("Food & Dining", "Restaurants"),
    "Bar": ("Food & Dining", "Bars & Alcohol"),
    "Spesa": ("Shopping", None),
    "Abbigliamento": ("Shopping", "Clothes"),
    "Scarpe": ("Shopping", "Clothes"),
    "Tecnologia": ("Shopping", "Electronics"),
    "Regali": ("Gifts & Donations", "Gifts"),
    "Trasporti": ("Transportation", None),
    "Automobile": ("Transportation", "Car"),
    "Carburanti": ("Transportation", "Gas & Fuel"),
    "Assicurazioni": ("Transportation", "Auto Insurance"),
    "Casa": ("Housing", None),
    "Affitto": ("Housing", "Rent/Mortgage"),
    "Bolletta energetica": ("Housing", "Utilities"),
    "Bolletta dell'acqua": ("Housing", "Utilities"),
    "Bolletta rifiuti": ("Housing", "Utilities"),
    "Divertimento": ("Entertainment", None),
    "Libri/Giornali": ("Entertainment", "Leisure"),
    "Salute/Sport": ("Health & Wellness", None),
    "Salute": ("Health & Wellness", "Health"),
    "Sport": ("Health & Wellness", "Gym & Fitness"),
    "Viaggi": ("Travels", None),
    "Altro (Spese)": ("Other Expenses", None),
    "Istruzione": ("Study", None),
    # Entrate
    "Guadagni": ("Employment", None),
    "Salario": ("Employment", "Salary"),
    "Altri lavori": ("Self-Employment", "Freelance"),
    "Proventi finanziari": ("Investment Income", None),
    "Altro (Guadagni)": ("Other Income", None),
    "Risparmi personali": ("Other Income", None),
    "Pensione": ("Other Income", None),
}

# La radice dell'app sotto cui far nascere una voce che non corrisponde a
# niente: "Tasse" da sola non dice dove andare, "Tasse sotto Altro (Spese)" dice
# che e' una spesa generica. Un albero che il file non nomina non entra qui:
# quello che resta fuori finisce nel segnaposto del suo verso.
RADICI: dict[str, str] = {
    "Cibo/Bevande": "Food & Dining",
    "Spesa": "Shopping",
    "Trasporti": "Transportation",
    "Casa": "Housing",
    "Divertimento": "Entertainment",
    "Salute/Sport": "Health & Wellness",
    "Viaggi": "Travels",
    "Altro (Spese)": "Other Expenses",
    "Guadagni": "Employment",
    "Proventi finanziari": "Investment Income",
    "Altro (Guadagni)": "Other Income",
}


class BackupIllegibile(ValueError):
    """Il file non e' un backup di FastBudget, o non e' leggibile."""


def _fuso(nome: str | None) -> ZoneInfo:
    """Il fuso di chi carica il file.

    FastBudget scrive un istante in millisecondi e la data che l'utente vede e'
    quella del suo orologio. Leggerla in UTC sposterebbe indietro di un giorno
    ogni movimento fatto dopo le 23, e l'import non quadraterebbe con quello che
    FastBudget mostra. Il nome arriva dal browser, che e' l'unico posto dove il
    fuso di chi guarda lo schermo si conosce; se non arriva o non si conosce si
    legge in UTC, che sbaglia qualche data ma non ferma l'import.
    """
    try:
        return ZoneInfo((nome or "").strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _valuta(campo: str | None) -> str:
    """La valuta di un conto del file: il campo e' "CHF - CHF".

    Il codice sta prima del separatore. Vuoto o illeggibile vale euro, come per
    i conti dell'app: una valuta inventata convertirebbe il saldo a un cambio che
    non esiste, e nessuno se ne accorgerebbe.
    """
    codice = (campo or "").split(" - ")[0].strip().upper()
    return codice if len(codice) == 3 and codice.isalpha() else "EUR"


def _giorno(millisecondi: Any, fuso: ZoneInfo) -> date | None:
    try:
        return datetime.fromtimestamp(int(millisecondi) / 1000, tz=fuso).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _descrizione(altro: str | None, note: str | None) -> str:
    """Il testo da mostrare: chi ha preso o dato i soldi, e la nota scritta sopra.

    FastBudget li tiene in due campi e l'app in uno: il nome di chi ha incassato
    e' quello che si riconosce in un elenco, e la nota e' quello che ci ha
    aggiunto chi l'ha registrata.
    """
    parti = [parte.strip() for parte in (altro or "", note or "") if parte and parte.strip()]
    return " - ".join(parti)


def _categoria(nome: str, radice: str | None, entrata: bool) -> tuple[str, str | None]:
    """Dove va una voce del file: il nome per l'app, e sotto cosa farla nascere.

    Il primo valore e' quello che l'anteprima mostra e che il salvataggio cerca:
    se e' il nome di una categoria che esiste gia', il movimento ci finisce
    dentro. Il secondo serve solo se quel nome non esiste ancora: senza, la
    categoria nuova nascerebbe in cima all'albero, e "Tasse" accanto a "Housing"
    non direbbe a nessuno che e' una spesa che l'utente aveva messo sotto
    "Altro".
    """
    voce = VOCI.get(nome)
    if voce is not None:
        padre, figlio = voce
        if padre in (RADICI_ENTRATE if entrata else RADICI_SPESE):
            return (figlio or padre), (padre if figlio else None)
        # La voce corrisponde, ma sta nell'altro albero: il nome non si porta
        # dietro, perche' cercandolo l'app ritroverebbe l'omonimo dell'albero
        # sbagliato e ci metterebbe dentro un movimento del verso opposto.
        return SEGNALIBRI[entrata], None
    return nome, RADICI.get(radice or nome) or SEGNALIBRI[entrata]


def _albero(conn: sqlite3.Connection) -> dict[str, str]:
    """Di ogni categoria del file, la radice sotto cui sta.

    La radice e' quella che dice dove far nascere una voce senza corrispondenza:
    il nome da solo non basta, "Tasse" e "Tasse" sotto due genitori diversi sono
    due cose diverse.
    """
    righe = conn.execute("SELECT _id, name, parent_id FROM categories_table").fetchall()
    nomi = {riga[0]: (riga[1] or "").strip() for riga in righe}
    albero: dict[str, str] = {}
    for _, nome, padre_id in righe:
        albero[(nome or "").strip()] = nomi.get(padre_id, (nome or "").strip())
    return albero


def leggi_backup(percorso: str, fuso_orario: str | None = None) -> dict[str, Any]:
    """Le righe del backup piu' i conti che servono per importarle.

    ``percorso`` e' il file su disco: il caricamento lo scrive in una copia
    temporanea, cosi' il backup di chi lo carica non viene mai aperto in
    scrittura nemmeno per sbaglio.
    """
    fuso = _fuso(fuso_orario)
    try:
        conn = sqlite3.connect(f"file:{quote(percorso)}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as errore:
        raise BackupIllegibile(str(errore)) from errore
    try:
        try:
            conti_del_file = _conti(conn)
            albero = _albero(conn)
            righe = _movimenti(conn, fuso, albero)
        except sqlite3.Error as errore:
            raise BackupIllegibile(str(errore)) from errore
    finally:
        conn.close()
    return {"transactions": _accoppia(righe, conti_del_file), "accounts": _conti_nominati(righe, conti_del_file)}


def _conti(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """I conti del file: valuta, saldo iniziale e saldo dichiarato.

    `initial_funds` diventa il saldo iniziale del conto nell'app: e' il punto da
    cui FastBudget ha cominciato a contare, e prenderlo da li' e' quello che fa
    arrivare il saldo calcolato dove FastBudget dice che e' arrivato.
    """
    conti = {}
    for nome, valore, iniziale, valuta in conn.execute(
            "SELECT name, value, initial_funds, currency FROM account").fetchall():
        conti[(nome or "").strip()] = {
            "currency": _valuta(valuta), "startingBalance": float(iniziale or 0),
            "balance": float(valore or 0),
        }
    return conti


def _movimenti(conn: sqlite3.Connection, fuso: ZoneInfo, albero: dict[str, str]) -> list[dict]:
    """Ogni riga del file come riga grezza, con quello che serve per accoppiarla."""
    righe = []
    for identificativo, verso, valore, categoria, conto, istante, altro, note in conn.execute(
            "SELECT _id, i_e, value, category, account, date, from_or_to, notes FROM income_or_expense").fetchall():
        giorno = _giorno(istante, fuso)
        if giorno is None:
            continue
        entrata = verso == 1
        categoria = (categoria or "").strip()
        conto = (conto or "").strip()
        altro = (altro or "").strip() or None
        trasferimento = categoria == CATEGORIA_TRASFERIMENTO
        nome_categoria, padre = (None, None) if trasferimento else _categoria(
            categoria, albero.get(categoria, categoria), entrata)
        importo = abs(float(valore or 0))
        descrizione = _descrizione(altro, note) or categoria
        righe.append({
            "_id": identificativo, "_trasferimento": trasferimento, "_entrata": entrata,
            "_conto": conto, "_altro": altro,
            "sourceRow": identificativo,
            "occurredOn": giorno.isoformat(),
            "description": descrizione, "details": descrizione,
            "amount": importo if entrata else -importo, "rawAmount": importo,
            "type": "income" if entrata else "expense",
            "transactionType": "Income" if entrata else "Expenses",
            "category": nome_categoria, "categoryParent": padre,
            "categoryRaw": "Other",
            "accountName": conto, "destinationName": None, "goal": None,
        })
    return righe


def _conti_nominati(righe: list[dict], conti_del_file: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """I conti che i movimenti nominano, con quello che il file ne sa.

    Un conto che i movimenti nominano ma che nel file non c'e' piu' e' la
    controparte di un trasferimento: non si puo' saltare, perche' senza di lei i
    soldi usciti da un conto vero non arriverebbero da nessuna parte e il suo
    saldo non tornerebbe. Si mostra, e chi importa decide se crearlo, puntarlo a
    un conto che esiste o lasciare quelle righe fuori.
    """
    nominati: dict[str, int] = {}
    for riga in righe:
        # La controparte conta solo per i trasferimenti: su una spesa quel campo
        # e' il nome del negozio, e una tendina di conti piena di supermercati
        # sarebbe una domanda senza senso.
        for nome in ((riga["_conto"], riga["_altro"]) if riga["_trasferimento"] else (riga["_conto"],)):
            if nome:
                nominati[nome] = nominati.get(nome, 0) + 1
    conti = []
    for nome, movimenti in nominati.items():
        noto = conti_del_file.get(nome, {})
        conti.append({
            "name": nome,
            # Un conto che il file non ha piu' non dichiara nemmeno la valuta:
            # euro e' la valuta dei conti che c'erano, e chi importa la corregge
            # nell'anteprima se non e' quella giusta.
            "currency": noto.get("currency", "EUR"),
            "startingBalance": noto.get("startingBalance"),
            # Il saldo che FastBudget mostra, per poterlo confrontare con quello
            # che l'app calcolera' dai movimenti importati: e' l'unico modo che
            # chi importa ha di vedere se l'import ha portato tutto.
            "declaredBalance": noto.get("balance"),
            "movements": movimenti,
            "inFile": nome in conti_del_file,
        })
    return sorted(conti, key=lambda conto: (not conto["inFile"], conto["name"]))


def _compatibili(uscita: dict, entrata: dict, valute: dict[str, str]) -> bool:
    """Se due gambe possono essere lo stesso giro di soldi.

    Fra conti nella stessa valuta gli importi devono coincidere, e la data non
    conta: fra l'uscita e la registrazione dell'arrivo puo' passare anche un
    mese, e una cifra che coincide e' una prova piu' forte di dieci giorni di
    distanza. Il limite di giorni serve dove l'importo non puo' dire niente,
    cioe' quando il cambio trasforma la cifra in un'altra: li' l'unica prova
    rimasta e' la vicinanza nel tempo.

    Senza la condizione sull'importo l'accoppiamento prendeva il primo uscito e
    il primo entrato dello stesso periodo, e il piu' vicino nel tempo non e'
    quasi mai il piu' giusto: duecento franchi e milleseicento non sono lo stesso
    giro di soldi solo perche' sono capitati a tre giorni di distanza.
    """
    distanza = abs((date.fromisoformat(entrata["occurredOn"])
                    - date.fromisoformat(uscita["occurredOn"])).days)
    if valute.get(uscita["_conto"]) != valute.get(entrata["_conto"]):
        return distanza <= GIORNI_ACCOPPIAMENTO
    return abs(entrata["rawAmount"] - uscita["rawAmount"]) <= max(0.02, uscita["rawAmount"] * 0.01)


def _accoppia(righe: list[dict], conti_del_file: dict[str, dict[str, Any]]) -> list[dict]:
    """Ricompone le due gambe di un trasferimento in una riga sola.

    Nel file ogni gamba e' un movimento a se': i soldi che escono da un conto con
    il nome dell'altro in `from_or_to`, e quelli che entrano nell'altro con il
    nome del primo. Niente li lega - i campi della sincronizzazione bancaria sono
    vuoti - quindi si accoppiano per conti, verso opposto e importo compatibile:
    fra i candidati vince il piu' vicino nel tempo, e a parita' di giorni
    l'importo piu' simile.

    Un conto che riceve e non registra la partenza resta con il suo numero di
    FastBudget solo se la partenza si trova: le due righe sono lo stesso giro di
    soldi registrato a dieci giorni di distanza, e lasciarle separate muoverebbe
    due volte i soldi del conto da cui sono partiti.

    Quello che resta spaiato si tiene, a una gamba sola, con lo stesso importo da
    tutte e due le parti: sono i soldi usciti verso un conto che il file non ha
    piu' (o entrati da lui), e buttarli via farebbe mentire il saldo del conto
    vero, che invece si e' mosso davvero.
    """
    valute = {nome: (conto or {}).get("currency", "EUR") for nome, conto in conti_del_file.items()}
    movimenti = [riga for riga in righe if riga["_trasferimento"]]
    uscite = [riga for riga in movimenti if not riga["_entrata"]]
    entrate = [riga for riga in movimenti if riga["_entrata"]]
    abbinate: dict[int, dict] = {}
    usate: set[int] = set()
    for uscita in uscite:
        giorno = date.fromisoformat(uscita["occurredOn"])
        candidati = []
        for entrata in entrate:
            if entrata["_id"] in usate or entrata["_conto"] != uscita["_altro"]:
                continue
            if entrata["_altro"] != uscita["_conto"]:
                continue
            if not _compatibili(uscita, entrata, valute):
                continue
            distanza = abs((date.fromisoformat(entrata["occurredOn"]) - giorno).days)
            candidati.append((distanza, abs(entrata["rawAmount"] - uscita["rawAmount"]), entrata))
        if candidati:
            entrata = min(candidati, key=lambda voce: (voce[0], voce[1], voce[2]["_id"]))[2]
            usate.add(entrata["_id"])
            abbinate[uscita["_id"]] = entrata
    risultato = []
    for riga in righe:
        if not riga["_trasferimento"]:
            risultato.append(_pulita(riga))
            continue
        if riga["_entrata"] and riga["_id"] in usate:
            # La gamba di arrivo e' gia' dentro la riga di uscita: da sola
            # sarebbe lo stesso giro di soldi contato due volte.
            continue
        arrivo = abbinate.get(riga["_id"])
        if arrivo is not None:
            riga["destinationAmount"] = arrivo["rawAmount"]
            riga["destinationName"] = arrivo["_conto"]
        elif riga["_entrata"]:
            # L'arrivo c'e' e la partenza no: i soldi sono entrati da un conto che
            # il file non ha piu'. Il movimento si scrive lo stesso, con la
            # controparte al posto di partenza.
            riga["accountName"], riga["destinationName"] = riga["_altro"], riga["_conto"]
            riga["destinationAmount"] = None
        else:
            riga["destinationName"] = riga["_altro"]
            riga["destinationAmount"] = None
        # Su un trasferimento l'importo e' positivo da tutte e due le parti: il
        # verso lo dice il campo, non il segno.
        riga["amount"] = riga["rawAmount"]
        riga["type"] = "transfer"
        riga["transactionType"] = "Transfers"
        riga["category"] = CATEGORIA_TRASFERIMENTO
        riga["categoryParent"] = None
        riga["description"] = f"{riga['accountName']} -> {riga['destinationName']}"
        riga["details"] = riga["description"]
        risultato.append(_pulita(riga))
    return risultato


def _pulita(riga: dict) -> dict:
    """La riga senza i campi di servizio: servono ad accoppiare, non a descrivere
    un movimento, e il salvataggio non li conosce."""
    return {chiave: valore for chiave, valore in riga.items() if not chiave.startswith("_")}
