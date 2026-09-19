"""Chi sta usando l'app, e come lo si riconosce.

Tre scelte di fondo, tutte pensate per un'app di casa che un giorno vivra' su
un NAS raggiungibile dai dispositivi di famiglia:

- **la password si puo' non avere**. Un utente senza password entra senza
  chiedere niente, esattamente come funzionava prima che esistessero gli
  utenti. Serve a non trasformare l'aggiunta degli account in un muro il
  giorno in cui viene attivata: si mette la password quando si vuole.
- **la sessione sta sul server**, non dentro un gettone firmato. Una riga in
  tabella si cancella, e cancellarla scollega davvero quel dispositivo. Con un
  gettone firmato bisognerebbe aspettarne la scadenza.
- **l'hash e' scrypt della libreria standard**. Niente dipendenze nuove da
  tenere aggiornate per una cosa che deve funzionare fra cinque anni.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .database import (SessionLocal, current_user_id, dimentica_utente_predefinito, get_session,
                       reset_current_user, set_current_user)
from .migrations import ALBERO_ENTRATE, ALBERO_SPESE
from .models import AppSetting, Category, LookupOption, User, UserSession

router = APIRouter()

COOKIE = "money_session"
SESSION_DAYS = 30


def _cookie_sicuro() -> bool:
    """Il flag Secure del cookie di sessione.

    Su HTTP il flag impedirebbe al browser di mandare il cookie, e non si
    riuscirebbe piu' a entrare: va acceso solo dietro HTTPS. Invece di
    ricordarselo, si ricava dall'indirizzo da cui l'app viene aperta - il
    giorno in cui davanti c'e' Tailscale con il suo certificato, si accende da
    solo. ``MONEY_COOKIE_SECURE`` resta come scavalco per i casi storti, per
    esempio un proxy che parla HTTPS al browser e HTTP all'app.
    """
    scelta = os.getenv("MONEY_COOKIE_SECURE", "").strip().lower()
    if scelta in {"true", "1", "yes"}:
        return True
    if scelta in {"false", "0", "no"}:
        return False
    return os.getenv("MONEY_APP_ORIGIN", "").strip().lower().startswith("https://")


COOKIE_SECURE = _cookie_sicuro()

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derivata = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derivata.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algoritmo, n, r, p, salt, atteso = stored.split("$")
        if algoritmo != "scrypt":
            return False
        derivata = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                  n=int(n), r=int(r), p=int(p), dklen=len(atteso) // 2)
    except (ValueError, TypeError):
        return False
    # Confronto a tempo costante: un confronto normale perde informazione
    # sulla lunghezza del prefisso corretto.
    return secrets.compare_digest(derivata.hex(), atteso)


def user_from_request(request: Request, session: Session) -> User | None:
    """L'utente del cookie, se la sessione e' valida e non scaduta."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    riga = session.scalar(select(UserSession).where(UserSession.token == token))
    if riga is None:
        return None
    if riga.expires_at and riga.expires_at < datetime.now(timezone.utc):
        session.delete(riga)
        session.commit()
        return None
    return session.get(User, riga.user_id)


def _apri_sessione(response: Response, session: Session, user: User) -> None:
    token = secrets.token_urlsafe(32)
    session.add(UserSession(token=token, user_id=user.id,
                           expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    session.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE, max_age=SESSION_DAYS * 24 * 3600, path="/")


def utente_della_richiesta(request: Request, session: Session) -> User | None:
    """L'utente autenticato; in mancanza, quello predefinito solo se e' l'unico.

    Il ripiego sull'utente predefinito e' pericoloso appena gli account sono
    piu' di uno: un'azione senza sessione finirebbe sull'account sbagliato.
    E' successo davvero mentre provavo, e ha messo una password su un account
    che non doveva averla. Con un solo utente l'ambiguita' non esiste, e li'
    il ripiego serve a tenere l'app aperta come e' sempre stata.
    """
    user = user_from_request(request, session)
    if user is not None:
        return user
    utenti = session.scalars(select(User)).all()
    return utenti[0] if len(utenti) == 1 and not utenti[0].password_hash else None


def _to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "hasPassword": bool(user.password_hash),
        "sharesTotals": user.shares_totals,
    }


class LoginPayload(BaseModel):
    username: str
    password: str | None = None


class PasswordPayload(BaseModel):
    current: str | None = None
    new: str


class UserPayload(BaseModel):
    username: str
    display_name: str


class UserUpdatePayload(BaseModel):
    display_name: str | None = None
    shares_totals: bool | None = None


@router.get("/api/auth/me")
def chi_sono(request: Request, session: Session = Depends(get_session)) -> dict:
    """Chi sta guardando, e chi altro potrebbe entrare.

    L'elenco serve al selettore di profilo e non espone nulla di finanziario:
    solo nome visualizzato e se quell'account ha una password.
    """
    utenti = session.scalars(select(User).order_by(User.id)).all()
    serve_accesso = any(u.password_hash for u in utenti)
    user = user_from_request(request, session)
    if user is None and not serve_accesso:
        # Nessuno ha una password: si entra come prima, senza chiedere niente.
        user = session.get(User, current_user_id())
    return {
        "user": _to_dict(user) if user else None,
        "users": [{"id": u.id, "username": u.username, "displayName": u.display_name,
                   "hasPassword": bool(u.password_hash)} for u in utenti],
        # Finche' nessuno ha una password l'app si apre come prima, senza
        # schermata di accesso: gli account non devono essere un ostacolo
        # finche' non si decide di usarli davvero.
        "loginRequired": serve_accesso,
        "canManageBackups": bool(user and utenti and user.id == utenti[0].id),
    }


# Tentativi sbagliati per nome utente, in memoria. Senza, una password di sei
# caratteri si prova a tappeto in poche ore da qualunque dispositivo che vede
# l'app.
# ponytail: in memoria e per processo; va in tabella se l'API gira con piu' worker.
TENTATIVI_MAX = 10
TENTATIVI_FINESTRA = 15 * 60
_tentativi: dict[str, list[float]] = {}


def _tentativi_recenti(username: str) -> list[float]:
    adesso = time.monotonic()
    recenti = [t for t in _tentativi.get(username, []) if adesso - t < TENTATIVI_FINESTRA]
    _tentativi[username] = recenti
    return recenti


@router.post("/api/auth/login")
def entra(payload: LoginPayload, response: Response, session: Session = Depends(get_session)) -> dict:
    username = payload.username.strip().lower()
    if len(_tentativi_recenti(username)) >= TENTATIVI_MAX:
        raise HTTPException(status_code=429, detail="too_many_attempts")
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if user.password_hash:
        if not payload.password or not verify_password(payload.password, user.password_hash):
            _tentativi[username].append(time.monotonic())
            raise HTTPException(status_code=401, detail="invalid_credentials")
    _tentativi.pop(username, None)
    _apri_sessione(response, session, user)
    return {"user": _to_dict(user)}


@router.post("/api/auth/logout")
def esci(request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        riga = session.scalar(select(UserSession).where(UserSession.token == token))
        if riga is not None:
            session.delete(riga)
            session.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"success": True}


@router.post("/api/auth/password")
def cambia_password(payload: PasswordPayload, request: Request,
                    session: Session = Depends(get_session)) -> dict:
    """Imposta o cambia la propria password.

    Chi una password ce l'ha deve dire quella vecchia: senza, chiunque trovi
    il computer sbloccato potrebbe chiudere fuori il proprietario.
    """
    user = utente_della_richiesta(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="not_logged_in")
    if user.password_hash and not verify_password(payload.current or "", user.password_hash):
        raise HTTPException(status_code=403, detail="wrong_current_password")
    if len(payload.new) < 6:
        raise HTTPException(status_code=422, detail="password_too_short")
    user.password_hash = hash_password(payload.new)
    # Le altre sessioni decadono: cambiare password serve anche a questo.
    for riga in session.scalars(select(UserSession).where(UserSession.user_id == user.id)).all():
        if riga.token != request.cookies.get(COOKIE):
            session.delete(riga)
    session.commit()
    return {"success": True}


# Un account appena creato deve essere gia' usabile. Senza queste righe la
# persona nuova si trova un'app senza colori, senza valute, senza tipi di conto:
# non "vuota" come ci si aspetta, proprio rotta.
OPZIONI_INIZIALI = {
    "colors": ["Blue", "Orange", "Green", "Yellow", "Purple", "Light Blue"],
    # Senza queste non si puo' registrare il primo movimento: le categorie
    # nascono dall'uso, ma per usare l'app serve gia' averne una.
    "categories_expenses": ["Groceries", "Eating out", "Housing", "Utilities", "Transportation",
                            "Health", "Leisure", "Travels", "Gifts", "Clothes", "Sports",
                            "Study", "Insurance", "Commissions", "Recurrings", "Other"],
    "categories_income": ["Salary", "Others"],
    "categories_savings": ["Savings"],
}


def prepara_account(user_id: int) -> None:
    """Impostazioni e vocabolari di partenza per una persona nuova.

    Si apre una sessione a nome suo: le politiche di riga rifiuterebbero righe
    con l'utente di qualcun altro, e fanno bene - e' esattamente cio' da cui
    devono proteggere.
    """
    anno = datetime.now(timezone.utc).year
    iniziali = {
        "header_color": "Blue",
        "late_income_shift": "Inactive",
        "late_income_day": "20",
        "savings_default_category": "Savings",
        "net_worth_currencies": "USD,CHF,BTC",
    }
    etichette = {
        "header_color": "Colore principale",
        "late_income_shift": "Shift entrate tardive", "late_income_day": "Giorno dello shift",
        "savings_default_category": "Categoria di default del risparmio",
        "net_worth_currencies": "Valute nel patrimonio",
    }
    gettone = set_current_user(user_id)
    try:
        with SessionLocal() as sessione:
            for chiave, valore in iniziali.items():
                sessione.add(AppSetting(user_id=user_id, key=chiave, label=etichette[chiave], value=valore))
            for gruppo, valori in OPZIONI_INIZIALI.items():
                for posizione, valore in enumerate(valori, start=1):
                    sessione.add(LookupOption(user_id=user_id, option_group=gruppo, position=posizione, value=valore))
            # Le stesse categorie anche come righe dell'albero. Il vocabolario
            # dice quali nomi l'app offre, l'albero quali esistono: senza questa
            # copia una persona appena creata avrebbe le tendine piene e l'albero
            # vuoto, e non potrebbe organizzare nemmeno una delle categorie che
            # sta usando.
            #
            # I due alberi sono gli stessi che pianta la migrazione su un
            # database che c'e' gia': qui nascono gia' ordinati, altrimenti una
            # persona nuova avrebbe un albero piatto e in disordine mentre chi
            # usa l'app da prima ce l'ha fatto, e la stessa app avrebbe due
            # forme diverse a seconda di quando ci si e' iscritti.
            posizione_categoria = 0
            for albero, verso in ((ALBERO_SPESE, "expense"), (ALBERO_ENTRATE, "income")):
                for radice, figli in albero.items():
                    posizione_categoria += 1
                    padre = Category(user_id=user_id, name=radice, parent_id=None,
                                     position=posizione_categoria, scope=verso)
                    sessione.add(padre)
                    # Il padre serve subito: i figli lo nominano per id.
                    sessione.flush()
                    for posizione_figlio, figlio in enumerate(figli, start=1):
                        sessione.add(Category(user_id=user_id, name=figlio, parent_id=padre.id,
                                              position=posizione_figlio, scope=verso))
            for posizione, anno_opzione in enumerate(range(anno - 2, anno + 25), start=1):
                sessione.add(LookupOption(user_id=user_id, option_group="years",
                                          position=posizione, value=str(anno_opzione)))
            sessione.commit()
    finally:
        reset_current_user(gettone)


@router.get("/api/users")
def elenco_utenti(session: Session = Depends(get_session)) -> dict:
    return {"items": [_to_dict(u) for u in session.scalars(select(User).order_by(User.id)).all()]}


# Il nome utente finisce nel nome dei file di backup e di export: con una
# barra o un `..` si scriverebbe fuori dalla cartella. Stesse regole del form.
USERNAME_VALIDO = re.compile(r"[a-z0-9]{1,40}")


@router.post("/api/users", status_code=201)
def crea_utente(payload: UserPayload, response: Response, session: Session = Depends(get_session)) -> dict:
    username = payload.username.strip().lower()
    if not username or not payload.display_name.strip():
        raise HTTPException(status_code=422, detail="missing_fields")
    if not USERNAME_VALIDO.fullmatch(username):
        raise HTTPException(status_code=422, detail="invalid_username")
    if session.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="username_taken")
    primo = session.scalar(select(User.id).limit(1)) is None
    user = User(username=username, display_name=payload.display_name.strip(), shares_totals=False)
    session.add(user)
    session.commit()
    session.refresh(user)
    prepara_account(user.id)
    if primo:
        # Chi apre l'app per la prima volta entra subito: chiedergli di
        # rifare l'accesso un secondo dopo averlo creato non serve a nulla.
        _apri_sessione(response, session, user)
    return _to_dict(user)


@router.patch("/api/users/{user_id}")
def aggiorna_utente(user_id: int, payload: UserUpdatePayload, request: Request,
                    session: Session = Depends(get_session)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    corrente = utente_della_richiesta(request, session)
    if corrente is None or corrente.id != user.id:
        raise HTTPException(status_code=403, detail="not_your_account")
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or user.display_name
    if payload.shares_totals is not None:
        user.shares_totals = payload.shares_totals
    session.commit()
    return _to_dict(user)


# Le tabelle che contengono i dati di una persona. La cancellazione le svuota
# tutte: lasciarne fuori una vorrebbe dire lasciare righe orfane, che nessuno
# vedrebbe piu' e che il prossimo utente con lo stesso id si ritroverebbe.
TABELLE_PERSONALI = [
    # Le categorie sono di chi le ha create: cancellando una persona senza
    # svuotarle, il prossimo account con lo stesso id se le ritroverebbe fra i
    # propri dati.
    "categories",
    "transaction_ledger_links", "investment_transaction_details", "investment_transactions",
    "investment_instruments", "transactions", "budget_plans", "goals", "accounts",
    "notes", "dismissed_notifications", "lookup_options", "app_settings",
    "account_valuations", "liability_transaction_details", "liability_profiles",
    "income_streams", "retirement_profiles", "categorization_rules",
    "events", "transaction_events", "goal_milestones",
]


@router.delete("/api/users/{user_id}")
def cancella_persona(user_id: int, request: Request, response: Response, confirm: str = "",
                     session: Session = Depends(get_session)) -> dict:
    """Cancella un account e tutto cio' che contiene, dopo averlo messo al sicuro.

    Tre precauzioni, perche' questa e' l'unica azione dell'app che distrugge
    dati e non si annulla:

    - **si cancella solo se stessi**. Non esiste un amministratore: nessuno
      deve poter cancellare i numeri di un'altra persona.
    - **si scrive il nome per conferma**. Un clic solo e' troppo poco.
    - **prima si salva**: un backup dell'intero database e un export dei dati
      di quella persona nel formato di scambio, cosi' puo' portarseli via o
      reimportarli altrove. Sono nella stessa cartella dei backup.
    """
    from .backup import BACKUPS_DIR, _safe_label, create_backup
    from .interchange import build_export

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    corrente = utente_della_richiesta(request, session)
    if corrente is None or corrente.id != user.id:
        raise HTTPException(status_code=403, detail="not_your_account")
    if confirm.strip().lower() != (user.display_name or "").strip().lower():
        raise HTTPException(status_code=422, detail="confirm_mismatch")

    momento = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    esito_backup = create_backup(f"pre-cancellazione-{user.username}")

    # L'export gira nella sessione di chi sta cancellando, quindi le politiche
    # di riga gli fanno vedere - e quindi esportare - soltanto i propri dati.
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    nome_export = f"dati-{_safe_label(user.username)}-{momento}.xlsx"
    (BACKUPS_DIR / nome_export).write_bytes(build_export(session).getvalue())

    for tabella in TABELLE_PERSONALI:
        session.execute(text(f"delete from {tabella} where user_id = :u"), {"u": user_id})
    session.execute(text("delete from user_sessions where user_id = :u"), {"u": user_id})
    session.execute(text("delete from users where id = :u"), {"u": user_id})
    session.commit()

    # L'utente predefinito tenuto in memoria poteva essere proprio questo.
    dimentica_utente_predefinito()
    response.delete_cookie(COOKIE, path="/")
    return {"deleted": True, "export": nome_export,
            "backup": esito_backup.get("filename") if isinstance(esito_backup, dict) else None}


async def middleware_utente(request: Request, call_next):
    """Stabilisce di chi sono i dati di questa richiesta, prima di servirla.

    E' l'unico punto in cui si decide: da qui in giu' tutto - i valori di
    default delle colonne e le politiche di riga del database - legge da
    ``current_user_id`` e non ha bisogno di sapere che esistono gli utenti.
    """
    from .database import SessionLocal, reset_current_user, set_current_user

    if request.url.path.startswith("/api/") and not _richiesta_dall_app(request):
        return JSONResponse({"detail": "forbidden_origin"}, status_code=403)
    token = request.cookies.get(COOKIE)
    gettone = None
    if token:
        try:
            with SessionLocal() as sessione:
                riga = sessione.scalar(select(UserSession).where(UserSession.token == token))
                scaduta = riga is not None and riga.expires_at and riga.expires_at < datetime.now(timezone.utc)
                if riga is not None and not scaduta:
                    gettone = set_current_user(riga.user_id)
        except Exception:  # noqa: BLE001 - un problema qui non deve impedire di servire la pagina
            gettone = None
    # Senza nemmeno un account non si scrive niente: i dati finirebbero
    # attribuiti a un utente che non esiste, e nessuno saprebbe di chi sono.
    if _protetto(request.url.path) and _nessun_utente():
        return JSONResponse({"detail": "no_account_yet"}, status_code=401)
    if gettone is None and _serve_accesso() and _protetto(request.url.path):
        # Senza questo la schermata di accesso sarebbe un disegno: i dati
        # uscirebbero comunque, attribuiti al primo utente.
        return JSONResponse({"detail": "not_logged_in"}, status_code=401)
    try:
        return await call_next(request)
    finally:
        if gettone is not None:
            reset_current_user(gettone)


def _host_ammessi() -> set[str]:
    """I nomi con cui l'app puo' essere aperta.

    Quelli di ``CORS_ORIGINS`` e ``MONEY_APP_ORIGIN``, piu' ``api``: e' il nome
    con cui il server web chiama l'API dentro Docker.
    """
    indirizzi = os.getenv("CORS_ORIGINS", "").split(",") + [os.getenv("MONEY_APP_ORIGIN", "")]
    return {"localhost", "127.0.0.1", "api"} | {
        urlsplit(i.strip()).hostname for i in indirizzi if urlsplit(i.strip()).hostname}


def _richiesta_dall_app(request: Request) -> bool:
    """Vero se la richiesta arriva dall'app e non da un altro sito.

    Finche' nessuno ha una password l'API risponde senza cookie, quindi
    qualunque pagina aperta nello stesso browser potrebbe chiamarla:

    - con un nome di dominio che punta a 127.0.0.1 (DNS rebinding) leggerebbe
      tutto: lo ferma il controllo dell'``Host``;
    - con un modulo mandato di nascosto sostituirebbe i dati con un import:
      lo ferma il controllo dell'``Origin``, che i browser mettono su ogni
      richiesta che scrive. Le porte non contano: lo stesso host e' la stessa
      macchina.
    """
    ammessi = _host_ammessi()
    host = urlsplit(f"//{request.headers.get('host', '')}").hostname
    if host not in ammessi:
        return False
    origine = request.headers.get("origin")
    if request.method in {"GET", "HEAD", "OPTIONS"} or origine is None:
        return True
    return urlsplit(origine).hostname in ammessi


# Le rotte che devono restare aperte: servono proprio per entrare.
APERTE = ("/api/auth/me", "/api/auth/login", "/api/auth/logout", "/api/users", "/health")


def _protetto(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith(APERTE)


def _nessun_utente() -> bool:
    """Vero su un'installazione appena messa in piedi, prima del primo account."""
    from .database import SessionLocal

    try:
        with SessionLocal() as sessione:
            return sessione.scalar(select(User.id).limit(1)) is None
    except Exception:  # noqa: BLE001
        return False


def _serve_accesso() -> bool:
    """Vero se qualcuno ha impostato una password.

    Finche' nessuno l'ha fatto l'app resta aperta come e' sempre stata: gli
    account non devono cambiare le abitudini di chi non li usa.
    """
    from .database import SessionLocal

    try:
        with SessionLocal() as sessione:
            return bool(sessione.scalar(
                select(User.id).where(User.password_hash.is_not(None)).limit(1)))
    except Exception:  # noqa: BLE001
        return False
