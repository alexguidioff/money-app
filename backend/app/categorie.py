"""L'albero delle categorie: le letture che lo usano e le rotte che lo cambiano.

Una categoria e' una riga, non piu' una stringa scritta dentro i movimenti. Qui
stanno le due cose che servono a tutti: le letture dell'albero (le radici, i
figli, il nome di una categoria) e le rotte che lo modificano. L'elenco a
tendina, i budget e i report leggono da qui invece di ricavare le categorie da
cosa e' gia' stato speso: un elenco che emerge dai movimenti non sa mostrare
una categoria appena creata e non ancora usata.

Due livelli, non di piu': radice e figlio. L'albero dice a cosa servono i soldi,
e il verso (`scope`) dice se sono soldi che entrano o che escono: sono due
domande diverse, e una categoria di spesa non appartiene all'albero delle
entrate. Quanto una spesa sia essenziale - bisogno o piacere - e' un'altra cosa
ancora, ed e' un campo a parte: `essenziale`.

Il nome si confronta senza badare alle maiuscole. E' la chiave con cui l'import
di un estratto conto ritrova una categoria, e "Casa" e "casa" sarebbero due voci
identiche nell'elenco a tendina: chi le vede non sa quale ha scelto.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import get_session
from .models import BudgetPlan, CategorizationRule, Category, Transaction

router = APIRouter()

# Bisogni e piaceri, e il residuo. "Other" non e' una terza categoria di spesa:
# e' dove finisce cio' che non hai ancora classificato. Sono tre parole
# dell'interfaccia, non tre posti nell'albero: la classificazione sta sulla
# categoria, e una radice qualunque vale "Other" finche' nessuno dice altro.
GRUPPI = ("Needs", "Wants", "Other")

# Le tre parole dell'interfaccia e quello che si scrive nella colonna: "Other"
# vuol dire "non detto", e nella colonna e' vuoto. In mezzo sta l'unica
# traduzione fra i due vocabolari, cosi' non ce n'e' una per rotta.
ESSENZIALE = {"Needs": "needs", "Wants": "wants", "Other": None}


class CategoryPayload(BaseModel):
    """Cosa si puo' cambiare di una categoria.

    ``parentId`` assente e ``parentId`` nullo sono due cose diverse: il primo
    lascia il padre com'e', il secondo la riporta fra le radici. Per questo la
    rotta guarda quali campi sono arrivati, non solo il loro valore.

    ``scope`` si sceglie solo creando una radice: un figlio sta nell'albero del
    padre, e chiederlo al modulo vorrebbe dire ammettere una risposta sbagliata.
    """

    name: str = ""
    parentId: int | None = None
    position: int | None = None
    active: bool | None = None
    scope: str = "expense"


def categoria_json(riga: Category, *, movimenti: int = 0, budget: int = 0, regole: int = 0,
                   figli: int = 0, effettivo: str | None = None) -> dict[str, Any]:
    """Una categoria come la vuole l'interfaccia.

    Gli usi viaggiano con la categoria perche' servono a decidere *prima* di
    provare a cancellarla: l'interfaccia sa dire "usata da 12 movimenti" invece
    di far scoprire il rifiuto dopo il clic.

    Viaggiano anche le due facce del bisogno/piacere: quello che la categoria
    dichiara (``essential``, vuoto se non dice niente) e quello che vale per
    lei (``essentialEffective``, che e' quello del padre quando lei tace).
    Servono tutti e due perche' il modulo li mostra diversi: il valore
    ereditato si vede in grigio, come una cosa che viene da sopra.
    """
    return {"id": riga.id, "name": riga.name, "parentId": riga.parent_id, "position": riga.position,
            "active": riga.active, "movements": movimenti, "budgets": budget, "rules": regole,
            "children": figli, "scope": riga.scope, "essential": riga.essenziale,
            "essentialEffective": effettivo}


def _usi(session: Session, colonna) -> dict[int, int]:
    return dict(session.execute(select(colonna, func.count()).where(colonna.is_not(None))
                                .group_by(colonna)).all())


def elenco(session: Session) -> list[Category]:
    """Tutte le categorie in ordine d'albero: ogni radice seguita dai suoi figli.

    L'ordine lo decide chi usa l'app (le frecce su e giu'), non l'alfabeto: e'
    la ragione per cui `position` esiste. Dentro la stessa posizione si ordina
    per nome, cosi' due categorie appena create escono sempre nello stesso
    ordine invece che a caso.
    """
    righe = list(session.scalars(select(Category).order_by(Category.position, Category.name,
                                                           Category.id)).all())
    radici = [riga for riga in righe if riga.parent_id is None]
    return [riga for radice in radici for riga in (radice, *(r for r in righe if r.parent_id == radice.id))]


def radici_con_figli(session: Session) -> list[dict[str, Any]]:
    """Le radici con i nomi dei loro figli, in ordine d'albero.

    E' la forma che serve a un elenco a tendina: i figli si mostrano indentati
    sotto il padre, e il padre resta il titolo che dice a cosa appartengono -
    anche quando non e' una scelta, perche' il verso non lo offre qui. I nomi e
    non gli id perche' l'interfaccia di oggi sceglie una categoria scrivendone il
    nome; le categorie spente restano dentro, altrimenti i figli di una radice
    spenta sparirebbero dall'elenco.
    """
    righe = elenco(session)
    figli: dict[int, list[str]] = defaultdict(list)
    for riga in righe:
        if riga.parent_id is not None:
            figli[riga.parent_id].append(riga.name)
    return [{"name": riga.name, "children": figli.get(riga.id, [])}
            for riga in righe if riga.parent_id is None]


def nomi(session: Session) -> dict[int, str]:
    """Da id a nome: le somme si fanno sugli id e i nomi si scrivono alla fine.

    Serve a chi serializza tanti movimenti: una mappa sola invece di una query
    per riga.
    """
    return dict(session.execute(select(Category.id, Category.name)).all())


def nome_di(session: Session, category_id: int | None) -> str:
    """Il nome di una categoria, vuoto quando non c'e'.

    Vuoto e non "Da categorizzare": l'assenza di categoria e' un'informazione -
    uno spostamento fra conti non ne ha una, e non e' un dato mancante - e un
    nome inventato al posto suo la cancellerebbe. Serve a chi serializza una
    riga sola; chi ne serializza tante si porta la mappa `nomi` e la legge da
    li', invece di interrogare il database una volta per movimento.
    """
    return nomi(session).get(category_id, "") if category_id is not None else ""


def radici(session: Session) -> dict[int, str]:
    """Da ogni categoria al nome della sua radice: se stessa, se e' una radice.

    Serve a chi somma per gruppi: le voci di un report si sommano sotto il
    totale della radice, e per farlo basta sapere a quale radice appartiene
    ogni foglia.
    """
    righe = session.scalars(select(Category)).all()
    nome_radice = {riga.id: riga.name for riga in righe if riga.parent_id is None}
    return {riga.id: nome_radice.get(riga.parent_id) or riga.name for riga in righe}


def padri(session: Session) -> dict[int, int | None]:
    """Da ogni categoria al suo padre: l'albero nudo, per chi deve percorrerlo.

    ``None`` vale per le radici e per un id che non e' una categoria: chi lo usa
    non deve distinguere "e' una radice" da "non lo conosco", perche' in
    entrambi i casi non c'e' nessun padre sotto cui sommare.
    """
    return dict(session.execute(select(Category.id, Category.parent_id)).all())


def con_i_figli(session: Session, totali: dict[int | None, Any]) -> dict[int | None, Any]:
    """I totali per categoria, piu' il totale di ogni padre con i figli dentro.

    Una funzione sola perche' ogni report deve rispondere alle stesse due
    domande: quanto ho speso in Supermercato, e quanto in Alimentari tutto
    compreso. Chi ha gia' i suoi totali per categoria li passa qui e riceve la
    stessa mappa con in piu' le voci dei padri: trenta in Supermercato e venti in
    Mensa fanno cinquanta in Alimentari. Una radice senza figli resta quella che
    era - conta per se', non sparisce - e un id che non e' nell'albero non entra
    in nessuna somma.

    Chi somma le righe di un report somma i valori **propri**, quelli passati
    qui dentro: il totale di un padre contiene gia' i suoi figli, e sommare
    anche quelli conterebbe due volte la stessa spesa.
    """
    genitori = padri(session)
    risultato = dict(totali)
    for categoria_id, valore in totali.items():
        genitore = genitori.get(categoria_id) if categoria_id is not None else None
        if genitore is not None:
            risultato[genitore] = risultato.get(genitore, 0) + valore
    return risultato


def essenziale_di_categoria(session: Session) -> dict[int, str | None]:
    """Se ogni categoria e' un bisogno, un piacere, o non si sa.

    Vale quello che la categoria dichiara; se non dichiara niente, vale quello
    del padre. Un solo gradino di risalita: l'albero e' a due livelli, e leggere
    oltre vorrebbe dire inventare una gerarchia che non c'e'. Dire "Housing e'
    un bisogno" basta per tutti i suoi figli, e Arredamento puo' smentirlo per
    conto suo senza toccare gli altri.

    ``None`` vuol dire "non detto", e non e' un piacere di comodo: un giudizio
    che l'utente non ha dato non si scrive nella sua contabilita'.
    """
    dichiarato = dict(session.execute(select(Category.id, Category.essenziale)).all())
    genitori = padri(session)
    return {category_id: valore if valore is not None else dichiarato.get(genitori.get(category_id))
            for category_id, valore in dichiarato.items()}


def gruppo_di_categoria(session: Session) -> dict[int, str]:
    """Bisogni, piaceri, o il resto: il gruppo di ogni categoria.

    Quello che non e' ne' un bisogno ne' un piacere - non detto - e' "Other",
    dove finisce cio' che non e' stato classificato: tenerlo visibile serve
    proprio a farlo svuotare.

    Prima il gruppo era scritto su ogni riga di budget, una per mese, e
    descriveva la categoria: la spesa non e' un bisogno a gennaio e un piacere
    a febbraio. Poi e' stato il nome della radice sotto cui la categoria stava,
    che confondeva due domande diverse. Adesso e' un attributo, con
    l'ereditarieta' del padre, e vale per tutti i mesi insieme.
    """
    noto = {valore: gruppo for gruppo, valore in ESSENZIALE.items() if valore}
    return {category_id: noto.get(valore, "Other")
            for category_id, valore in essenziale_di_categoria(session).items()}


def _dal_payload(session: Session, category_id: int | None, nome: str | None) -> Category:
    """La categoria nominata da un payload: per id se c'e', altrimenti per nome.

    Non la crea: chi classifica una categoria deve nominarne una che esiste, e
    un nome scritto male deve dare un errore invece di una categoria vuota in
    mezzo all'albero.
    """
    if category_id is not None:
        return _categoria(session, category_id)
    pulito = (nome or "").strip()
    if not pulito:
        raise HTTPException(status_code=422, detail="category obbligatoria")
    # A qualunque livello, con la radice che vince: una categoria dell'albero
    # nuovo puo' essere un figlio, e classificare "Vestiti" sotto Acquisti deve
    # funzionare come classificare una radice.
    riga = session.scalar(select(Category).where(func.lower(Category.name) == pulito.casefold())
                          .order_by(Category.parent_id.is_not(None), Category.id).limit(1))
    if riga is None:
        raise HTTPException(status_code=404, detail="categoryNotFound")
    return riga


def _verso(scope: str) -> str:
    """Il verso scritto nel payload, se e' uno dei due che esistono.

    Un verso inventato non e' una categoria che non compare in nessuna tendina:
    e' un errore, e va detto subito invece di scriverlo e scoprirlo dopo.
    """
    pulito = (scope or "").strip()
    if pulito not in ("expense", "income"):
        raise HTTPException(status_code=422, detail="categoryScopeUnknown")
    return pulito


def _pulito(nome: str) -> str:
    pulito = nome.strip()
    if not pulito or len(pulito) > 255:
        raise HTTPException(status_code=422, detail="categoryNameRequired")
    return pulito


def _senza_omonimi(session: Session, nome: str, parent_id: int | None, *, esclusa: int | None = None) -> None:
    """Due categorie con lo stesso nome nello stesso posto sono un doppione.

    Sotto un padre diverso invece e' legittimo: "Altro" sotto Alimentari e
    "Altro" sotto Trasporti sono due cose diverse, e chi le guarda le distingue
    dal padre.

    409 e non 422: non e' un modulo scritto male, e' un nome gia' preso, e
    l'interfaccia deve poterlo dire senza inventarsi una regola.
    """
    condizione = [func.lower(Category.name) == nome.lower(),
                  Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id]
    if esclusa is not None:
        condizione.append(Category.id != esclusa)
    if session.scalar(select(Category.id).where(*condizione)) is not None:
        raise HTTPException(status_code=409, detail="categoryExists")


def _padre(session: Session, parent_id: int | None, *, esclusa: int | None = None) -> Category | None:
    """Il padre scelto, se puo' fare il padre. ``None`` vuol dire radice."""
    if parent_id is None:
        return None
    padre = session.get(Category, parent_id)
    if padre is None:
        raise HTTPException(status_code=404, detail="categoryNotFound")
    if padre.id == esclusa:
        raise HTTPException(status_code=422, detail="categoryOwnParent")
    if padre.parent_id is not None:
        # Due livelli: un figlio non puo' avere figli. Con ventisei categorie un
        # terzo livello non serve a niente e raddoppia i casi in ogni somma.
        raise HTTPException(status_code=422, detail="categoryTooDeep")
    return padre


def _prossima_posizione(session: Session, parent_id: int | None) -> int:
    condizione = (Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id)
    massimo = session.scalar(select(func.max(Category.position)).where(condizione))
    return (massimo or 0) + 1


def _categoria(session: Session, category_id: int) -> Category:
    riga = session.get(Category, category_id)
    if riga is None:
        raise HTTPException(status_code=404, detail="categoryNotFound")
    return riga


@router.get("/api/categories")
def elenco_categorie(session: Session = Depends(get_session)) -> dict[str, Any]:
    movimenti, budget, regole = (_usi(session, Transaction.category_id), _usi(session, BudgetPlan.category_id),
                                _usi(session, CategorizationRule.category_id))
    figli = Counter(riga.parent_id for riga in session.scalars(select(Category)).all()
                    if riga.parent_id is not None)
    effettivi = essenziale_di_categoria(session)
    return {"items": [categoria_json(riga, movimenti=movimenti.get(riga.id, 0),
                                     budget=budget.get(riga.id, 0), regole=regole.get(riga.id, 0),
                                     figli=figli.get(riga.id, 0), effettivo=effettivi.get(riga.id))
                      for riga in elenco(session)]}


@router.post("/api/categories", status_code=201)
def crea_categoria(payload: CategoryPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    nome = _pulito(payload.name)
    padre = _padre(session, payload.parentId)
    _senza_omonimi(session, nome, padre.id if padre else None)
    riga = Category(name=nome, parent_id=padre.id if padre else None,
                    # Il verso lo decide il padre: una voce di una radice di
                    # entrate e' una voce di entrate. Chiederlo al modulo
                    # vorrebbe dire poter creare un figlio che non compare
                    # nell'albero dove sta il padre.
                    scope=padre.scope if padre else _verso(payload.scope),
                    position=payload.position if payload.position is not None
                    else _prossima_posizione(session, padre.id if padre else None))
    session.add(riga)
    session.commit()
    return categoria_json(riga)


@router.patch("/api/categories/{category_id}")
def aggiorna_categoria(category_id: int, payload: CategoryPayload,
                       session: Session = Depends(get_session)) -> dict[str, Any]:
    riga = _categoria(session, category_id)
    campi = payload.model_fields_set
    if "name" in campi:
        nome = _pulito(payload.name)
        _senza_omonimi(session, nome, riga.parent_id, esclusa=riga.id)
        riga.name = nome
    if "parentId" in campi:
        padre = _padre(session, payload.parentId, esclusa=riga.id)
        # Un figlio sotto un altro padre sarebbe un nipote: chi ha gia' figli
        # resta dov'e', altrimenti l'albero si accartoccia di nascosto.
        if padre is not None and session.scalar(
                select(func.count(Category.id)).where(Category.parent_id == riga.id)):
            raise HTTPException(status_code=422, detail="categoryTooDeep")
        _senza_omonimi(session, riga.name, padre.id if padre else None, esclusa=riga.id)
        # Spostandola sotto un altro padre si cambia albero: la voce segue il
        # padre, altrimenti resterebbe scritta in un albero in cui non sta e
        # sparirebbe dalla tendina del verso in cui adesso si trova.
        if padre is not None:
            riga.scope = padre.scope
        riga.parent_id = padre.id if padre else None
    if payload.position is not None:
        riga.position = payload.position
    if payload.active is not None:
        riga.active = payload.active
    session.commit()
    return categoria_json(riga)


@router.delete("/api/categories/{category_id}")
def cancella_categoria(category_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Cancella una categoria che nessuno usa, e spiega perche' quando non si puo'.

    Un genitore con i figli attaccati non si cancella: i figli resterebbero
    senza padre, cioe' radici, e l'albero cambierebbe forma senza che nessuno
    l'abbia chiesto. Una categoria con movimenti, budget o regole nemmeno: quei
    riferimenti punterebbero a una riga che non c'e' piu', e il movimento
    perderebbe la categoria che gli era stata data. Per mettere via una
    categoria che non si usa piu' c'e' `active`.
    """
    riga = _categoria(session, category_id)
    figli = session.scalar(select(func.count(Category.id)).where(Category.parent_id == riga.id)) or 0
    if figli:
        raise HTTPException(status_code=409, detail={"code": "categoryHasChildren", "children": figli})
    usi = {"movements": session.scalar(select(func.count(Transaction.id))
                                       .where(Transaction.category_id == riga.id)) or 0,
           "budgets": session.scalar(select(func.count(BudgetPlan.id))
                                     .where(BudgetPlan.category_id == riga.id)) or 0,
           "rules": session.scalar(select(func.count(CategorizationRule.id))
                                   .where(CategorizationRule.category_id == riga.id)) or 0}
    if any(usi.values()):
        raise HTTPException(status_code=409, detail={"code": "categoryInUse", **usi})
    session.delete(riga)
    session.commit()
    return {"success": True}


class CategoryGroupPayload(BaseModel):
    """Quale categoria, e in quale gruppo."""

    category: str = ""
    categoryId: int | None = None
    category_group: str | None = None


@router.put("/api/category-groups")
def classifica_categoria(payload: CategoryGroupPayload,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
    """Classifica una categoria come bisogno o piacere.

    La classificazione ha cambiato casa due volte: era scritta su ogni riga di
    budget - la stessa parola ripetuta per mese su una riga che descriveva la
    categoria e non il mese - poi era la posizione nell'albero, cioe' il nome
    della radice sotto cui la categoria stava. Adesso e' un attributo della
    categoria, ed e' l'unica delle tre che non confonde due domande diverse:
    a cosa servono quei soldi, e quanto sono essenziali.

    "Other" non e' un terzo valore: vuol dire "non detto", ed e' quello che si
    scrive quando l'utente ritira la sua risposta.

    Solo le spese si classificano. Su un'entrata la domanda non ha senso - il
    rimborso di una spesa non e' un bisogno - e la risposta e' un errore, non
    un valore scritto e poi ignorato.
    """
    riga = _dal_payload(session, payload.categoryId, payload.category)
    gruppo = (payload.category_group or "").strip()
    if gruppo not in GRUPPI:
        raise HTTPException(status_code=422, detail="categoryGroupUnknown")
    if gruppo != "Other" and riga.scope != "expense":
        raise HTTPException(status_code=422, detail="essentialOnlyForExpenses")
    riga.essenziale = ESSENZIALE[gruppo]
    session.commit()
    return {"success": True, "categoryId": riga.id, "category": riga.name,
            "categoryGroup": gruppo, "parentId": riga.parent_id}


def register_categorie_routes(app) -> None:
    app.include_router(router)
