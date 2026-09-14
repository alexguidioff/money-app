import os
from collections.abc import Generator
from contextvars import ContextVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/money.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Connessione separata con i privilegi del proprietario. Serve solo per creare
# tabelle e politiche all'avvio e per i dump: l'applicazione vera parla al
# database con un ruolo che non puo' scavalcare l'isolamento fra utenti.
DATABASE_ADMIN_URL = os.getenv("DATABASE_ADMIN_URL", DATABASE_URL)
admin_engine = (engine if DATABASE_ADMIN_URL == DATABASE_URL
                else create_engine(DATABASE_ADMIN_URL, connect_args=connect_args, pool_pre_ping=True))


class Base(DeclarativeBase):
    pass


# Finche' l'autenticazione non c'e', l'utente e' sempre quello iniziale. Quando
# ci sara', l'unica cosa che cambia e' chi scrive in questa variabile: tutto il
# resto - i valori di default delle colonne e le politiche di riga del
# database - legge da qui e continua a funzionare senza modifiche.
_default_user_id: int | None = None
_active_user: ContextVar[int | None] = ContextVar("money_active_user", default=None)


def set_default_user(user_id: int) -> None:
    """L'utente usato quando la richiesta non ne indica uno."""
    global _default_user_id
    _default_user_id = user_id


def dimentica_utente_predefinito() -> None:
    """Butta via l'utente predefinito tenuto in memoria.

    Serve dopo aver cancellato una persona: il valore in cache potrebbe essere
    proprio il suo, e resterebbe li' a indicare qualcuno che non esiste piu'.
    Alla prossima richiesta viene riletto dal database.
    """
    global _default_user_id
    _default_user_id = None


def _primo_utente() -> int:
    """Legge dal database chi e' il primo utente, una volta sola.

    Non si puo' scrivere 1 a mano: l'id dipende da come e' andata la creazione
    della tabella. E non basta impostarlo all'avvio dell'applicazione, perche'
    script e test non ci passano e scriverebbero righe di un utente inesistente.
    La lettura usa una connessione diretta, non una Session, altrimenti
    l'ascoltatore qui sotto la richiamerebbe all'infinito.
    """
    global _default_user_id
    if _default_user_id is None:
        try:
            with engine.connect() as conn:
                _default_user_id = conn.execute(text("select min(id) from users")).scalar() or 1
        except Exception:  # noqa: BLE001 - database non ancora pronto
            return 1
    return _default_user_id


def current_user_id() -> int:
    """L'utente a cui appartengono i dati di questa richiesta."""
    scelto = _active_user.get()
    return scelto if scelto is not None else _primo_utente()


def set_current_user(user_id: int):
    """Imposta l'utente della richiesta; ritorna il gettone per ripristinarlo."""
    return _active_user.set(user_id)


def reset_current_user(token) -> None:
    _active_user.reset(token)


@event.listens_for(Session, "after_begin")
def _annuncia_utente(session: Session, transaction, connection) -> None:
    """Dice al database chi sta guardando, all'inizio di ogni transazione.

    Su questo valore si appoggiano le politiche di riga: se una query dimentica
    il filtro sull'utente non vede i dati altrui, semplicemente non trova nulla.
    La separazione non dipende quindi dal fatto che 123 query se lo ricordino.
    """
    if connection.dialect.name != "postgresql":
        return
    connection.execute(text("select set_config('app.user_id', :valore, false)"),
                       {"valore": str(current_user_id())})


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
