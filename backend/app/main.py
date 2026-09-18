import asyncio
import csv
import json
import logging
import os
import re
import tempfile
import time
from calendar import monthrange
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Form, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import distinct, extract, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .calculation_engine import (account_balances_series, calculate_account_balance, debito_pianificato_al, effective_date, piano_ammortamento,
                                 normalized_name, stato_debito_registrato)
from .categorization import PENDING_CATEGORY, applica, carica_regole, scartate
from .core_routes import (CATEGORY_GROUPS, GOAL_KINDS, MAX_SELEZIONE_MASSA, display_currencies, fx_symbols,
                          movimenti_per_saldi, num, sync_savings_plan)
from .database import Base, admin_engine, engine, get_session, set_default_user, current_user_id
from .migrations import accendi_isolamento, aggiungi_colonna_utente, tracked_changes
from .transaction_rules import (REAL_MOVEMENT, BUDGET_MOVEMENT, SPOSTAMENTI, TIPI_MOVIMENTO,
                                missing_fields, validate_movement, set_refund, validate_refund,
                                verso_debito)
from .market_data import MarketDataError, fetch_instrument_profile, fetch_price_history, fetch_yahoo_quote, search_yahoo_symbols
from .yahoo_profile import YahooProfileError, YahooRateLimited, fetch_profile
from .market_cache import get_or_fetch_price, list_cached_symbols
from .models import Account, AccountValuation, AppSetting, BudgetPlan, InstrumentProfile, LiabilityProfile, LiabilityTransactionDetail, MarketPrice, Goal, InvestmentInstrument, InvestmentTransaction, InvestmentTransactionDetail, Note, Transaction, TransactionLedgerLink
from .interchange import FORMAT_VERSION, build_export
from .interchange_import import InterchangeError, read_and_validate, write_imported_data, summarize_state
from .models import User
from .reports import excel_report, pdf_report
from .pdf_importer import BankStatementParser
from .csv_importer import CSVStatementParser
from .statement_parsing import parse_amount, parse_date
from .auth import middleware_utente, router as auth_router
from .notifications import router as notifications_router
from .shared import router as shared_router
from .backup import create_backup, delete_backup, ensure_daily_backup, list_backups, restore_backup


MONTHS_IT = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]


logger = logging.getLogger("money.api")

# Categoria segnaposto per i movimenti importati o creati senza categoria:
# l'utente la riconosce nella lista e la sistema a mano. Coerente con la
# stringa che il workbook usava nella stessa colonna. Il valore e' definito in
# ``categorization`` perche' lo leggono anche le regole, ed e' importato qui
# perche' e' da qui che lo prendono gli altri moduli.


# Respiro fra due richieste consecutive alla fonte, per non farsi bloccare.
SOURCE_REQUEST_PAUSE_SECONDS = 1.0

BACKUP_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def read_upload(file: UploadFile, extension: str) -> bytes:
    if not (file.filename or '').lower().endswith(extension):
        raise HTTPException(400, detail="uploadFormat")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail="uploadTooLarge")
    if not payload:
        raise HTTPException(400, detail="uploadEmpty")
    return payload


def require_backup_admin(session: Session = Depends(get_session)) -> None:
    allowed = session.scalar(select(User.id).order_by(User.id).limit(1)) == current_user_id()
    # Rilascia il lock di lettura su users prima che pg_restore la ricrei.
    session.rollback()
    if not allowed:
        raise HTTPException(403, detail="backupAdminOnly")


async def _backup_loop() -> None:
    """Tiene in vita il dump giornaliero.

    Il controllo e' frequente e la creazione no: ``ensure_daily_backup`` non fa
    nulla se un dump recente esiste gia'. Serve perche' un'app locale viene
    spenta e riaccesa di continuo, e un timer a 24 ore secche non scatterebbe
    mai. Un errore qui non deve fermare l'app: si annota e si riprova dopo.
    """
    while True:
        try:
            result = await asyncio.to_thread(ensure_daily_backup)
            if result:
                logger.info("backup automatico creato: %s", result["filename"])
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning("backup automatico non riuscito: %s", error)
        await asyncio.sleep(BACKUP_CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create any newly introduced tables without overwriting existing data."""
    if str(engine.url).startswith("sqlite"):
        database_path = str(engine.url).removeprefix("sqlite:///")
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    # Schema e politiche si creano con i privilegi del proprietario; tutto il
    # resto dell'applicazione parla al database con il ruolo ristretto.
    Base.metadata.create_all(admin_engine)
    tracked_changes(admin_engine)
    # Le tabelle che esistevano prima non le tocca create_all: le modifiche di
    # schema su un database gia' popolato stanno qui, e sono ripetibili.
    esito = aggiungi_colonna_utente(admin_engine)
    if esito.get("utente"):
        set_default_user(esito["utente"])
    if admin_engine is not engine:
        accendi_isolamento(admin_engine, password=os.getenv("MONEY_APP_DB_PASSWORD", "money-app-local"))
    backups = asyncio.create_task(_backup_loop())
    try:
        yield
    finally:
        backups.cancel()


app = FastAPI(title="Money API", version="0.2.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3010,http://127.0.0.1:3010").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(middleware_utente)
app.include_router(auth_router)
app.include_router(shared_router)
app.include_router(notifications_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/summary")
def health_summary(session: Session = Depends(get_session)) -> dict:
    """Riepilogo rapido del contenuto del DB.

    Usato dalla UI per distinguere un database vuoto (primo avvio reale) da
    un database popolato con problemi di rete o sync Excel.
    """
    counts = {
        "transactions": session.scalar(select(func.count(Transaction.id)).where(REAL_MOVEMENT)) or 0,
        "goals": session.scalar(select(func.count(Goal.id))) or 0,
        "notes": session.scalar(select(func.count(Note.id))) or 0,
        "accounts": session.scalar(select(func.count(Account.id))) or 0,
        "budgets": session.scalar(select(func.count(BudgetPlan.id))) or 0,
        "investments": session.scalar(select(func.count(InvestmentTransaction.id))) or 0,
        "instruments": session.scalar(select(func.count(InvestmentInstrument.id))) or 0,
        "categories": len(session.execute(select(Transaction.category).distinct().where(REAL_MOVEMENT)).all()),
    }
    return {
        "status": "ok",
        "counts": counts,
        "isEmpty": counts["transactions"] == 0 and counts["accounts"] == 0,
    }


@app.get("/api/export/data")
def export_data(session: Session = Depends(get_session)):
    """Esporta tutti i dati dell'app nel formato di scambio.

    Diverso dal report: quello serve a leggere un periodo, questo a trasportare
    l'intero contenuto e a poterlo reimportare.
    """
    stream = build_export(session)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="money-dati-{stamp}.xlsx"',
                 "X-Money-Format-Version": FORMAT_VERSION},
    )


@app.post("/api/import/data")
async def import_data_route(file: UploadFile = File(...), session: Session = Depends(get_session)) -> dict:
    """Rimpiazza i dati dell'app con quelli di un file di scambio.

    Il file viene letto e validato per intero prima di toccare il database: se
    qualcosa non torna, l'import si ferma e i dati esistenti restano come sono.
    """
    payload = await read_upload(file, '.xlsx')
    try:
        meta, data = await asyncio.to_thread(read_and_validate, BytesIO(payload))
    except InterchangeError as error:
        raise HTTPException(400, detail={"code": "importInvalid", "reason": str(error)}) from error
    before = summarize_state(session)

    # Il backup segue la validazione e precede qualsiasi scrittura.
    try:
        backup = await asyncio.to_thread(create_backup, "pre-import")
        backup_name = backup["filename"]
    except Exception as error:  # noqa: BLE001
        logger.warning("backup pre-import non riuscito: %s", error)
        raise HTTPException(503, detail="importBackupFailed") from error
    try:
        result = write_imported_data(session, meta, data, source_name=file.filename or "")
        for (periodo,) in session.execute(select(distinct(BudgetPlan.period))).all():
            sync_savings_plan(session, periodo)
        session.commit()
    except InterchangeError as error:
        session.rollback()
        raise HTTPException(400, detail={"code": "importInvalid", "reason": str(error)}) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, detail="importConflict") from error
    except Exception as error:  # noqa: BLE001
        session.rollback()
        logger.exception("Import non riuscito")
        raise HTTPException(500, detail="importDataFailed") from error
    result["before"] = before
    result["backup"] = backup_name
    return result


@app.post("/api/backups", dependencies=[Depends(require_backup_admin)])
def create_backup_endpoint(label: str = Query("manual", min_length=1, max_length=60)) -> dict:
    """Crea un dump del database e lo salva nella cartella backup condivisa."""
    try:
        return create_backup(label)
    except Exception as exc:  # noqa: BLE001 - rimappiamo a HTTP 500 con messaggio
        raise HTTPException(status_code=500, detail=f"Backup fallito: {exc}") from exc


@app.get("/api/backups", dependencies=[Depends(require_backup_admin)])
def list_backups_endpoint() -> dict:
    return {"items": list_backups()}


@app.delete("/api/backups/{filename}", dependencies=[Depends(require_backup_admin)])
def delete_backup_endpoint(filename: str) -> dict:
    """Cancella un dump. Irreversibile: la conferma la chiede l'interfaccia.

    Serve perche' la conservazione automatica tocca solo `auto` e `pre-import`:
    tutti gli altri dump restano per sempre, e finora non c'era modo di
    sfoltirli senza entrare nel container.
    """
    try:
        return delete_backup(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Cancellazione fallita: {exc}") from exc


@app.post("/api/backups/{filename}/restore", dependencies=[Depends(require_backup_admin)])
def restore_backup_endpoint(filename: str) -> dict:
    """Ripristina un dump. Operazione distruttiva: richiede conferma lato UI."""
    try:
        return restore_backup(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Restore fallito: {exc}") from exc


# Quanti giorni possono separare lo stesso movimento fra l'estratto conto e l'app:
# la banca registra alla contabilizzazione, a mano si scrive il giorno della spesa
# (l'affitto segnato il primo del mese e addebitato il cinque).
GIORNI_DUPLICATO = 5


def _parole(testo: str | None) -> set[str]:
    return set(re.findall(r"[^\W_]{3,}", (testo or "").lower()))


def _coppia(per_giorno, usati: set[int], giorno: date, importo: Decimal, opposto: str | None) -> list:
    """Due movimenti dello stesso giorno e conto, non ancora usati, che sommano l'importo."""
    for distanza in sorted(range(-GIORNI_DUPLICATO, GIORNI_DUPLICATO + 1), key=abs):
        liberi = [item for item in per_giorno.get(giorno + timedelta(days=distanza), [])
                  if item.id not in usati and item.transaction_type != opposto]
        for i, primo in enumerate(liberi):
            for secondo in liberi[i + 1:]:
                if primo.account_name == secondo.account_name and abs(primo.amount) + abs(secondo.amount) == importo:
                    return [primo, secondo]
    return []


def statement_preview(raw_transactions: list[dict], session: Session) -> dict:
    """Le righe lette da un estratto conto, con i possibili doppioni gia' segnati.

    Un doppione si riconosce da importo e data, non dalla descrizione: quella
    della banca ("Hai ricevuto un bonifico da ...") non somiglia mai a quella
    scritta a mano ("affitto maggio"), che spesso nemmeno c'e'. La descrizione
    serve solo a scegliere fra piu' candidati. Ogni movimento gia' presente vale
    per una riga sola: due caffe' da 1,50 nello stesso giorno sono due, e se
    nell'app ce n'e' uno solo, il secondo va importato.

    Una spesa divisa si registra spesso in due movimenti (la propria parte e
    quella da farsi restituire): se nessun movimento ha l'importo della riga,
    vale anche una coppia dello stesso giorno e dello stesso conto che lo somma.
    """
    if not raw_transactions:
        raise HTTPException(422, detail="statementEmpty")
    per_importo = defaultdict(list)
    per_giorno = defaultdict(list)
    for item in session.execute(select(Transaction.id, Transaction.occurred_on, Transaction.amount,
                                       Transaction.details, Transaction.transaction_type, Transaction.account_name)
                                .where(REAL_MOVEMENT)).all():
        per_importo[abs(item.amount)].append(item)
        per_giorno[item.occurred_on].append(item)
    regole = carica_regole(session)
    usati: set[int] = set()
    rows = []
    for tx in raw_transactions:
        occurred = tx.get("occurredOn")
        description = tx.get("details") or tx.get("description") or ""
        amount = abs(Decimal(str(tx.get("rawAmount", tx.get("amount", 0)))))
        day = date.fromisoformat(occurred) if occurred else None
        # Un'entrata non e' mai il doppione di una spesa, ne' il contrario; un
        # giroconto puo' essere l'uno o l'altro, a seconda del conto.
        opposto = {"Income": "Expenses", "Expenses": "Income"}.get(tx.get("transactionType"))
        candidati = [item for item in per_importo[amount]
                     if item.id not in usati and day and item.occurred_on
                     and abs((day - item.occurred_on).days) <= GIORNI_DUPLICATO
                     and item.transaction_type != opposto]
        parole = _parole(description)
        match = min(candidati, key=lambda item: (-len(parole & _parole(item.details)),
                                                 abs((day - item.occurred_on).days), item.id), default=None)
        coppia = [] if match or not day else _coppia(per_giorno, usati, day, amount, opposto)
        if match is not None:
            usati.add(match.id)
        usati.update(item.id for item in coppia)
        match = match or (coppia[0] if coppia else None)
        # Nessuno ha scelto a mano questa categoria: se una regola decide, la
        # categoria resta automatica e il pattern dice da quale regola viene.
        tipo = tx.get("transactionType", "Expenses")
        automatica = not (tx.get("category") or "").strip() \
                     or (tx.get("category") or "").strip().casefold() == PENDING_CATEGORY.casefold()
        decisione = applica(regole, description, tipo, amount)
        categoria = _resolve_category(tx.get("category"), tipo, decisione[0] if decisione else None)
        rows.append({
            "id": None, "date": occurred, "description": description,
            "details": description,
            "category": categoria,
            "categoryAutomatic": automatica,
            # La regola si nomina solo quando ha davvero deciso: se la riga
            # portava gia' una categoria, quella vince e la regola non c'entra.
            "categoryRule": decisione[1] if decisione and automatica and categoria == decisione[0] else None,
            "amount": float(amount), "transactionType": tipo,
            "type": tx.get("type", "expense"), "accountName": tx.get("accountName"),
            "destinationName": tx.get("destinationName"), "goal": tx.get("goal"),
            "duplicate": match is not None,
            "duplicateOf": {"id": match.id, "date": match.occurred_on.isoformat(),
                            "amount": float(sum(abs(item.amount) for item in coppia) if coppia else match.amount),
                            "description": " + ".join(filter(None, (item.details for item in coppia))) if coppia
                            else match.details} if match else None,
        })
    # Le regole che non si sono potute compilare: l'interfaccia le segnala,
    # perche' altrimenti sarebbero regole che non fanno niente e non lo dicono.
    return {"success": True, "transactions": rows, "count": len(rows), "rulesDiscarded": scartate(regole)}


@app.post("/api/import/pdf")
async def import_pdf_statement(file: UploadFile = File(...), session: Session = Depends(get_session)):
    content = await read_upload(file, '.pdf')
    try:
        with tempfile.NamedTemporaryFile(suffix='.pdf') as temp_file:
            temp_file.write(content)
            temp_file.flush()
            rows = await asyncio.to_thread(BankStatementParser.extract_transactions_from_pdf, temp_file.name)
        return statement_preview(rows, session)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Parsing PDF fallito")
        raise HTTPException(422, detail="statementParseFailed") from error


@app.post("/api/transactions/pdf-import")
async def save_pdf_transactions(transactions: List[Dict[str, Any]], session: Session = Depends(get_session)):
    """Salva solo righe valide; restituisce gli indici per ritentare solo quelle fallite."""
    saved_count = 0
    errors = []
    accounts = {a.name: a for a in session.scalars(select(Account)).all()}
    for index, tx_data in enumerate(transactions):
        try:
            occurred = date.fromisoformat(str(tx_data.get('date') or ''))
            transaction_type = tx_data.get('transactionType')
            amount = Decimal(str(tx_data.get('amount', '')))
            account = accounts.get(tx_data.get('accountName'))
            destination = accounts.get(tx_data.get('destinationName'))
            if transaction_type not in VALID_TRANSACTION_TYPES:
                raise ValueError("statementInvalidType")
            if not amount.is_finite() or amount <= 0 or amount >= Decimal('100000000000000') or amount != amount.quantize(Decimal('.01')):
                raise ValueError("statementInvalidAmount")
            if account is None or account.is_active is False:
                raise ValueError("statementAccountRequired")
            if transaction_type in SPOSTAMENTI and (destination is None or destination.is_active is False or destination.name == account.name):
                raise ValueError("statementDestinationRequired")
            transaction = Transaction(
                occurred_on=occurred,
                effective_on=compute_effective_on(session, occurred, transaction_type),
                transaction_type=transaction_type,
                # La categoria che l'anteprima ha mostrato e' quella che si
                # scrive: se l'ha decisa una regola, buttarla via qui vorrebbe
                # dire che l'anteprima l'ha mostrata per niente. Una riga senza
                # categoria resta senza: il segnaposto lo scioglie
                # `_resolve_category`.
                category=_resolve_category(tx_data.get('category'), transaction_type),
                amount=amount, account_name=account.name, account_type=account.source_group.title(),
                destination_name=destination.name if transaction_type in SPOSTAMENTI else None,
                destination_type=destination.source_group.title() if transaction_type in SPOSTAMENTI else None,
                goal=tx_data.get('goal'), details=tx_data.get('details') or tx_data.get('description') or None,
            )
            session.add(transaction)
            validate_movement(session, transaction)
            session.flush()
            _sync_liability_detail(session, transaction, TransactionPayload(
                occurred_on=occurred.isoformat(), transaction_type=transaction_type,
                category=transaction.category, amount=float(amount), account_name=transaction.account_name,
                destination_name=transaction.destination_name, details=transaction.details))
            session.commit()
            saved_count += 1
        except Exception as error:
            session.rollback()
            # Le regole del movimento rispondono con un codice nel dettaglio: un
            # investimento senza conto broker deve dire questo, non "riga non valida".
            codice = error.detail.get("code") if isinstance(error, HTTPException) and isinstance(error.detail, dict) else str(error)
            code = codice if codice in {
                "statementInvalidType", "statementInvalidAmount", "statementAccountRequired",
                "statementDestinationRequired", "investmentNeedsBroker", "debtNeedsLiability"} else "statementRowInvalid"
            errors.append({"index": index, "code": code})
    return {"success": not errors, "saved": saved_count, "errors": errors}


# ---------------------------------------------------------------------------
# CRUD transazioni (Fase 1)
# ---------------------------------------------------------------------------


# I template delle ricorrenze non sono movimenti reali: fuori da conteggi e report.


def _parse_iso_date(value: str | date | None, field: str) -> date:
    if value is None or value == "":
        raise HTTPException(status_code=422, detail=f"Campo '{field}' obbligatorio")
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Data '{field}' non valida: {value}") from exc


LATE_INCOME_DEFAULT_DAY = 20


def late_income_settings(session: Session) -> tuple[str, int]:
    """Parametri dello shift entrate tardive (Settings E27/E29 nel workbook)."""
    rows = {
        row.key: row.value
        for row in session.scalars(select(AppSetting).where(AppSetting.key.in_(("late_income_shift", "late_income_day")))).all()
    }
    shift = (rows.get("late_income_shift") or "Inactive").strip()
    raw_day = (rows.get("late_income_day") or "").strip()
    return shift, int(raw_day) if raw_day.isdigit() else LATE_INCOME_DEFAULT_DAY


def compute_effective_on(session: Session, occurred: date, transaction_type: str) -> date:
    """Data di competenza, come la colonna N del foglio Transactions: un'entrata
    incassata dal giorno configurato in poi pesa sul mese successivo. In Excel e'
    una formula, quindi qui e' sempre derivata e mai impostata dal client.
    """
    shift, day = late_income_settings(session)
    return effective_date(occurred, transaction_type, shift, day)


def recompute_effective_dates(session: Session) -> int:
    """Riapplica la regola a tutti i movimenti gia' registrati e restituisce
    quanti sono cambiati. Serve quando cambiano i parametri: in Excel le formule
    di colonna N si ricalcolano da sole, qui il ricalcolo va fatto a mano.
    """
    shift, day = late_income_settings(session)
    changed = 0
    for tx in session.scalars(select(Transaction)).all():
        expected = effective_date(tx.occurred_on, tx.transaction_type, shift, day)
        if tx.effective_on != expected:
            tx.effective_on = expected
            changed += 1
    return changed


def _to_decimal(value: Any, field: str, *, allow_negative: bool = True) -> Decimal:
    if value is None or value == "":
        raise HTTPException(status_code=422, detail=f"Campo '{field}' obbligatorio")
    try:
        amount = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Importo '{field}' non valido") from exc
    if not amount.is_finite() or abs(amount) >= Decimal("100000000000000"):
        raise HTTPException(422, detail="statementInvalidAmount")
    if not allow_negative and amount < 0:
        raise HTTPException(status_code=422, detail=f"Importo '{field}' non può essere negativo")
    return amount.quantize(Decimal("0.01"))


VALID_TRANSACTION_TYPES = set(TIPI_MOVIMENTO)


def _transaction_to_dict(tx: Transaction, session: Session | None = None) -> dict:
    payload = {
        "id": tx.id,
        "description": tx.details or "",
        "category": tx.category or "Da categorizzare",
        "categoryRaw": (tx.category or "Da categorizzare"),
        "date": tx.occurred_on.isoformat() if tx.occurred_on else None,
        "effectiveOn": tx.effective_on.isoformat() if tx.effective_on else None,
        "amount": float(tx.amount or 0),
        "rawAmount": float(abs(tx.amount or 0)),
        "type": ("income" if (tx.amount or 0) > 0 else "expense"),
        "transactionType": tx.transaction_type,
        "accountType": tx.account_type,
        "accountName": tx.account_name,
        "destinationType": tx.destination_type,
        "destinationName": tx.destination_name,
        "goal": tx.goal,
        "details": tx.details,
        "balance": float(tx.balance) if tx.balance is not None else None,
        "recurrenceRule": tx.recurrence_rule,
        "isRecurringTemplate": bool(tx.is_recurring_template),
        "sourceRow": tx.source_row,
        "countsInBudget": tx.counts_in_budget,
        "refundOfId": tx.refund_of_id,
        "incomplete": bool(missing_fields(tx)),
        "missingFields": missing_fields(tx),
    }
    # Popola le righe del ledger collegate (se la session è disponibile).
    if session is not None:
        links = session.execute(
            select(TransactionLedgerLink, InvestmentTransaction, InvestmentTransactionDetail)
            .join(InvestmentTransaction, TransactionLedgerLink.ledger_id == InvestmentTransaction.id)
            .outerjoin(InvestmentTransactionDetail, InvestmentTransactionDetail.transaction_id == InvestmentTransaction.id)
            .where(TransactionLedgerLink.transaction_id == tx.id)
            .order_by(InvestmentTransaction.occurred_on.desc(), InvestmentTransaction.id.desc())
        ).all()
        payload["linkedLedger"] = [
            {
                "linkId": link.id,
                "id": itx.id,
                "occurredOn": itx.occurred_on.isoformat(),
                "name": itx.name,
                "transactionType": itx.transaction_type,
                "amount": num(itx.amount),
                "units": num(itx.units) if itx.units is not None else None,
                "price": num(itx.price) if itx.price is not None else None,
                "currency": itx.currency or "EUR",
                "fee": float(detail.fee) if detail else 0,
                "notes": detail.notes if detail else None,
            }
            for link, itx, detail in links
        ]
    else:
        payload["linkedLedger"] = []
    return payload


class TransactionPayload(BaseModel):
    occurred_on: str
    transaction_type: str
    # Assente sui trasferimenti, che non hanno categoria; obbligatoria per il
    # resto, ma il controllo sta nel gestore perche' dipende dal tipo.
    category: str | None = None
    amount: float
    account_name: str | None = None
    destination_name: str | None = None
    goal: str | None = None
    details: str | None = None
    account_type: str | None = None
    destination_type: str | None = None
    balance: float | None = None
    is_recurring_template: bool | None = None
    recurrence_rule: str | None = None
    recurrence_end_date: str | None = None
    counts_in_budget: bool | None = None
    refund_of_id: int | None = None
    debt_principal: float | None = None
    debt_interest: float | None = None


def _apply_transaction_payload(tx: Transaction, payload: TransactionPayload, session: Session) -> None:
    previous = (tx.account_name, tx.destination_name) if tx.id else ()
    occurred = _parse_iso_date(payload.occurred_on, "occurred_on")
    if payload.transaction_type not in VALID_TRANSACTION_TYPES:
        raise HTTPException(status_code=422, detail=f"transaction_type non valido: {payload.transaction_type}")
    # Spostare denaro fra due conti non ha una categoria: vale per i giroconti
    # e per i versamenti su un broker allo stesso modo. Il segnaposto "_" e'
    # quello che il workbook usava nella stessa colonna; l'API lo rimuove in
    # lettura, cosi' non si vede un trattino dove non c'e' niente.
    if tx.id is None:
        category = _resolve_category(payload.category, payload.transaction_type)
    elif payload.transaction_type in SPOSTAMENTI:
        category = "_"
    elif not payload.category or not payload.category.strip():
        raise HTTPException(status_code=422, detail="category obbligatoria")
    else:
        category = payload.category.strip()

    tx.occurred_on = occurred
    tx.transaction_type = payload.transaction_type
    tx.category = category
    tx.effective_on = compute_effective_on(session, occurred, payload.transaction_type)
    tx.amount = _to_decimal(payload.amount, "amount", allow_negative=False)
    tx.account_name = (payload.account_name or None) or None
    tx.destination_name = (payload.destination_name or None) or None
    if payload.transaction_type not in SPOSTAMENTI:
        # Entrate e uscite toccano un conto solo: una destinazione rimasta da
        # un tipo precedente va tolta, o il movimento resta a meta' fra due
        # forme diverse. Il ramo per il vecchio tipo Savings e' sparito con lui.
        tx.destination_name = None
    if payload.transaction_type in SPOSTAMENTI:
        tx.counts_in_budget = False
    elif payload.counts_in_budget is not None:
        tx.counts_in_budget = payload.counts_in_budget
    elif tx.counts_in_budget is None:
        tx.counts_in_budget = True
    tx.goal = (payload.goal or None) or None
    tx.details = (payload.details or None) or None
    tx.account_type = (payload.account_type or None) or None
    tx.destination_type = (payload.destination_type or None) or None
    if payload.balance is not None:
        tx.balance = _to_decimal(payload.balance, "balance")
    if payload.is_recurring_template is not None:
        tx.is_recurring_template = bool(payload.is_recurring_template)
        tx.recurrence_rule = payload.recurrence_rule
        if payload.recurrence_end_date:
            tx.recurrence_end_date = _parse_iso_date(payload.recurrence_end_date, "recurrence_end_date")
    with session.no_autoflush:
        validate_movement(session, tx, previous)
        if "refund_of_id" in payload.model_fields_set:
            set_refund(session, tx, payload.refund_of_id)
        validate_refund(session, tx)


def _sync_liability_detail(session: Session, tx: Transaction, payload: TransactionPayload) -> None:
    detail = session.scalar(select(LiabilityTransactionDetail).where(
        LiabilityTransactionDetail.transaction_id == tx.id)) if tx.id else None
    if tx.transaction_type == "Expenses":
        # Un addebito sul conto del debito - interessi, spese di gestione,
        # imposte - diventa una riga dichiarata come le erogazioni e i
        # rimborsi. Prima si deduceva in lettura da "e' una spesa su questo
        # conto": una spesa estranea finita li' entrava fra gli interessi e
        # nulla lo segnalava. Se il movimento non dichiara quanto e'
        # interesse, la riga nasce da classificare e il banner la mostra.
        liability = session.scalar(select(Account).where(
            Account.source_group == "liability", Account.name == tx.account_name))
        if liability is None:
            if detail:
                session.delete(detail)
            return
        dichiarato = "debt_interest" in payload.model_fields_set
        if detail is None:
            detail = LiabilityTransactionDetail(transaction_id=tx.id)
            session.add(detail)
        detail.liability_account_id = liability.id
        detail.kind = "charge"
        detail.principal_amount = Decimal("0")
        detail.interest_amount = (_to_decimal(payload.debt_interest or 0, "debt_interest", allow_negative=False)
                                  if dichiarato else tx.amount)
        detail.refund_of_id = None
        detail.is_classified = dichiarato
        return

    if tx.transaction_type != "Debt":
        if detail:
            session.delete(detail)
        return

    liabilities = session.scalars(select(Account).where(
        Account.source_group == "liability",
        Account.name.in_([tx.account_name, tx.destination_name]))).all()
    inferred = verso_debito(tx, [row.name for row in liabilities])
    liability = next((row for row in liabilities if row.name in {tx.account_name, tx.destination_name}), None)
    if liability is None:
        raise HTTPException(422, detail={"code": "debtNeedsLiability"})

    split_given = bool({"debt_principal", "debt_interest"} & payload.model_fields_set)
    if split_given:
        principal = _to_decimal(payload.debt_principal or 0, "debt_principal", allow_negative=False)
        interest = _to_decimal(payload.debt_interest or 0, "debt_interest", allow_negative=False)
        if principal + interest != tx.amount:
            raise HTTPException(422, detail="liabilityInvalidSplit")
        classified = True
    elif detail and detail.principal_amount + detail.interest_amount == tx.amount:
        principal, interest, classified = detail.principal_amount, detail.interest_amount, detail.is_classified
    else:
        principal, interest, classified = tx.amount, Decimal("0"), inferred == "drawdown"
    if inferred == "drawdown" and interest:
        raise HTTPException(422, detail="liabilityInvalidSplit")

    if detail is None:
        detail = LiabilityTransactionDetail(transaction_id=tx.id)
        session.add(detail)
    detail.liability_account_id = liability.id
    detail.kind, detail.principal_amount, detail.interest_amount = inferred, principal, interest
    detail.refund_of_id, detail.is_classified = None, classified


@app.post("/api/transactions", status_code=201)
def create_transaction(payload: TransactionPayload, session: Session = Depends(get_session)):
    """Crea un nuovo movimento."""
    tx = Transaction()
    _apply_transaction_payload(tx, payload, session)
    session.add(tx)
    try:
        session.flush()
        _sync_liability_detail(session, tx, payload)
        session.commit()
        session.refresh(tx)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto sui dati del movimento: {exc.orig}") from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore salvataggio movimento: {exc}") from exc
    return _transaction_to_dict(tx, session)


class BulkTransactionsPayload(BaseModel):
    ids: list[int] = Field(min_length=1)
    changes: dict


@app.patch("/api/transactions/bulk")
def bulk_transactions(payload: BulkTransactionsPayload, session: Session = Depends(get_session)):
    allowed = {"category", "account_name", "transaction_type", "counts_in_budget", "incomplete_accepted"}
    booleani = {"counts_in_budget", "incomplete_accepted"}
    if not payload.changes or set(payload.changes) - allowed:
        raise HTTPException(422, detail="bulkInvalidFields")
    # Il tetto vale anche qui: l'interfaccia non ci arriva, ma l'endpoint e'
    # raggiungibile lo stesso e una lista senza limiti bloccherebbe la tabella.
    if len(payload.ids) > MAX_SELEZIONE_MASSA:
        raise HTTPException(422, detail="bulkTooMany")
    for key, value in payload.changes.items():
        if (key in booleani and type(value) is not bool) or (key not in booleani and not isinstance(value, str)):
            raise HTTPException(422, detail="bulkInvalidFields")
    # Accettare un movimento incompleto non e' modificarlo: e' dire che lo si e'
    # guardato. Passare da `validate_movement` lo rifiuterebbe proprio perche'
    # e' incompleto, cioe' l'unico caso in cui questo campo serve.
    solo_accettazione = set(payload.changes) == {"incomplete_accepted"}
    try:
        ids = set(payload.ids)
        if session.scalar(select(LiabilityTransactionDetail.id).where(
                LiabilityTransactionDetail.transaction_id.in_(ids)).limit(1)):
            raise HTTPException(409, detail="liabilityPaymentManaged")
        rows = session.scalars(select(Transaction).where(Transaction.id.in_(ids), REAL_MOVEMENT)
                               .order_by(Transaction.id).with_for_update()).all()
        if len(rows) != len(ids):
            raise HTTPException(404, detail="movementNotFound")
        with session.no_autoflush:
            for tx in rows:
                previous = (tx.account_name, tx.destination_name)
                for key, value in payload.changes.items():
                    setattr(tx, key, value.strip() if isinstance(value, str) else value)
                if tx.transaction_type in SPOSTAMENTI:
                    tx.counts_in_budget = False
                if solo_accettazione:
                    continue
                validate_movement(session, tx, previous)
                validate_refund(session, tx)
                tx.effective_on = compute_effective_on(session, tx.occurred_on, tx.transaction_type)
        session.commit()
        return {"updated": len(rows)}
    except Exception:
        session.rollback()
        raise


class BulkInvestmentTransactionsPayload(BaseModel):
    ids: list[int] = Field(min_length=1)
    changes: dict


@app.post("/api/investments/ledger/bulk")
def bulk_investment_transactions(payload: BulkInvestmentTransactionsPayload,
                                 session: Session = Depends(get_session)):
    allowed = {"name", "transaction_type", "currency"}
    if not payload.changes or set(payload.changes) - allowed or not all(
            isinstance(value, str) and value.strip() for value in payload.changes.values()):
        raise HTTPException(422, detail="bulkInvalidFields")
    if "transaction_type" in payload.changes and payload.changes["transaction_type"] not in VALID_INVESTMENT_TX_TYPES:
        raise HTTPException(422, detail="bulkInvalidFields")
    ids = set(payload.ids)
    try:
        rows = session.scalars(select(InvestmentTransaction).where(InvestmentTransaction.id.in_(ids))
                               .order_by(InvestmentTransaction.id).with_for_update()).all()
        if len(rows) != len(ids):
            raise HTTPException(404, detail="ledgerNotFound")
        for row in rows:
            for key, value in payload.changes.items():
                setattr(row, key, value.strip().upper() if key == "currency" else value.strip())
        session.commit()
        return {"updated": len(rows)}
    except Exception:
        session.rollback()
        raise


class BulkLedgerLinkPayload(BaseModel):
    ids: list[int] = Field(min_length=1)
    transaction_id: int


@app.post("/api/investments/ledger/bulk-link")
def bulk_link_investment_transactions(payload: BulkLedgerLinkPayload,
                                      session: Session = Depends(get_session)):
    tx = session.get(Transaction, payload.transaction_id)
    if tx is None:
        raise HTTPException(404, detail="movementNotFound")
    solo_investimenti_si_collegano(tx)
    ids = set(payload.ids)
    try:
        rows = session.scalars(select(InvestmentTransaction).where(InvestmentTransaction.id.in_(ids))
                               .order_by(InvestmentTransaction.id).with_for_update()).all()
        if len(rows) != len(ids):
            raise HTTPException(404, detail="ledgerNotFound")
        existing = set(session.scalars(select(TransactionLedgerLink.ledger_id).where(
            TransactionLedgerLink.transaction_id == tx.id,
            TransactionLedgerLink.ledger_id.in_(ids))).all())
        for ledger_id in ids - existing:
            session.add(TransactionLedgerLink(transaction_id=tx.id, ledger_id=ledger_id))
        session.flush()
        pretendi_quadratura(session, tx.id)
        session.commit()
        return {"linked": len(ids - existing), "alreadyLinked": len(existing)}
    except Exception:
        session.rollback()
        raise


@app.get("/api/transactions/{tx_id}")
def get_transaction(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, detail="movementNotFound")
    from .core_routes import transaction_json
    return transaction_json(tx, session)


@app.patch("/api/transactions/{tx_id}")
def update_transaction(tx_id: int, payload: TransactionPayload, session: Session = Depends(get_session)):
    """Aggiorna un movimento esistente."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    _apply_transaction_payload(tx, payload, session)
    try:
        # Cambiare l'importo di un movimento gia' collegato rompe il gruppo
        # tanto quanto collegargli l'operazione sbagliata: stessa regola.
        session.flush()
        _sync_liability_detail(session, tx, payload)
        pretendi_quadratura(session, tx_id)
        session.commit()
        session.refresh(tx)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto sui dati del movimento: {exc.orig}") from exc
    except HTTPException:
        # Il motivo lo ha gia' detto chi l'ha sollevata: incartarlo in un 500
        # generico lo farebbe sparire proprio dove serve leggerlo.
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento movimento: {exc}") from exc
    return _transaction_to_dict(tx, session)


class SplitPayload(BaseModel):
    amount: float
    transaction_type: str
    category: str | None = None
    destination_name: str | None = None
    details: str | None = None


DIVISIBILI = {"Expenses", "Income", "Transfers"}


@app.post("/api/transactions/{tx_id}/split", status_code=201)
def split_transaction(tx_id: int, payload: SplitPayload, session: Session = Depends(get_session)) -> dict:
    """Toglie una parte a un movimento e ne fa un movimento a se'.

    Serve alla spesa pagata per intero ma solo in parte propria: la carta paga
    40, 20 sono una spesa e 20 un trasferimento verso chi li deve restituire.
    Stesso conto e stessa data dell'originale; tipo, categoria e destinazione
    sono quelli della parte nuova. Tutto in una transazione: o si dividono
    entrambi, o non cambia niente.

    Restano interi i movimenti la cui cifra e' legata ad altro: investimenti e
    debiti (operazioni e piani collegati), rimborsi, e quelli con un dettaglio
    di debito o con operazioni del ledger agganciate.
    """
    tx = session.get(Transaction, tx_id)
    if tx is None or tx.is_recurring_template:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    legato = (tx.transaction_type not in DIVISIBILI or tx.refund_of_id is not None
              or session.scalar(select(Transaction.id).where(Transaction.refund_of_id == tx.id).limit(1)) is not None
              or session.scalar(select(TransactionLedgerLink.id).where(TransactionLedgerLink.transaction_id == tx.id).limit(1)) is not None
              or session.scalar(select(LiabilityTransactionDetail.id).where(LiabilityTransactionDetail.transaction_id == tx.id).limit(1)) is not None)
    if legato:
        raise HTTPException(status_code=409, detail="splitNotAllowed")
    if payload.transaction_type not in DIVISIBILI:
        raise HTTPException(status_code=422, detail="statementInvalidType")
    parte = Decimal(str(payload.amount))
    if not parte.is_finite() or parte != parte.quantize(Decimal(".01")) or not Decimal("0") < parte < tx.amount:
        raise HTTPException(status_code=422, detail="splitInvalidAmount")

    nuovo = Transaction()
    _apply_transaction_payload(nuovo, TransactionPayload(
        occurred_on=tx.occurred_on.isoformat(), transaction_type=payload.transaction_type,
        category=payload.category, amount=float(parte), account_name=tx.account_name,
        destination_name=payload.destination_name, details=payload.details if payload.details is not None else tx.details,
    ), session)
    tx.amount -= parte
    session.add(nuovo)
    try:
        session.flush()
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto sui dati del movimento: {exc.orig}") from exc
    session.refresh(tx)
    session.refresh(nuovo)
    return {"original": _transaction_to_dict(tx, session), "created": _transaction_to_dict(nuovo, session)}


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int, session: Session = Depends(get_session)):
    """Elimina un movimento."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    detail = session.scalar(select(LiabilityTransactionDetail).where(
        LiabilityTransactionDetail.transaction_id == tx_id))
    transactions = [tx]
    if detail:
        for refund in session.scalars(select(LiabilityTransactionDetail).where(
                LiabilityTransactionDetail.refund_of_id == tx_id)):
            refund.refund_of_id = None
        session.delete(detail)
    for row in transactions:
        set_refund(session, row, None)
        for refund in session.scalars(select(Transaction).where(Transaction.refund_of_id == row.id)):
            refund.refund_of_id = None
            refund.counts_in_budget = True
        session.delete(row)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione movimento: {exc}") from exc
    return {"success": True, "deleted_ids": [row.id for row in transactions]}


class TransferPayload(BaseModel):
    occurred_on: str
    amount: float
    source_account: str
    destination_account: str
    details: str | None = None


@app.post("/api/transfers", status_code=201)
def create_transfer(payload: TransferPayload, session: Session = Depends(get_session)):
    tx = create_transaction(TransactionPayload(occurred_on=payload.occurred_on,
        transaction_type="Transfers", amount=payload.amount,
        account_name=payload.source_account, destination_name=payload.destination_account,
        details=payload.details), session)
    return {"success": True, "id": tx["id"], "transaction": tx}


class LiabilityTransferPayload(BaseModel):
    occurred_on: str
    source_account: str
    destination_account: str
    principal_amount: float
    interest_amount: float = 0
    details: str | None = None


@app.post("/api/liabilities/transfers", status_code=201)
def create_liability_transfer(payload: LiabilityTransferPayload, session: Session = Depends(get_session)):
    principal = _to_decimal(payload.principal_amount, "principal_amount", allow_negative=False)
    interest = _to_decimal(payload.interest_amount, "interest_amount", allow_negative=False)
    total = principal + interest
    if total <= 0:
        raise HTTPException(422, detail="liabilityInvalidSplit")
    tx = create_transaction(TransactionPayload(
        occurred_on=payload.occurred_on, transaction_type="Debt", amount=float(total),
        account_name=payload.source_account, destination_name=payload.destination_account,
        details=(payload.details or "").strip() or "Movimento debito",
        debt_principal=float(principal), debt_interest=float(interest)), session)
    return {"success": True, "transactionIds": [tx["id"]],
            "principal": float(principal), "interest": float(interest)}


# ---------------------------------------------------------------------------
# CRUD Goals (Fase 1 - sotto-step 1.2)
# ---------------------------------------------------------------------------


def _goal_to_dict(goal: Goal) -> dict:
    return {
        "id": goal.id,
        "name": goal.name,
        "startingAmount": float(goal.starting_amount or 0),
        "targetAmount": float(goal.target_amount or 0),
        "startDate": goal.start_date.isoformat() if goal.start_date else None,
        "targetDate": goal.target_date.isoformat() if goal.target_date else None,
        "completedAt": goal.completed_at.isoformat() if goal.completed_at else None,
    }


class GoalPayload(BaseModel):
    name: str
    starting_amount: float = 0
    target_amount: float = 0
    start_date: str | None = None
    target_date: str | None = None
    completed_at: str | None = None
    kind: str = "contributions"
    target_account: str | None = None


def _apply_goal_payload(goal: Goal, payload: GoalPayload) -> None:
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="Nome obiettivo obbligatorio")
    goal.name = payload.name.strip()
    if payload.kind not in GOAL_KINDS:
        raise HTTPException(status_code=422, detail=f"Tipo obiettivo non valido: {payload.kind}")
    goal.kind = payload.kind
    goal.target_account = (payload.target_account or "").strip() or None
    goal.starting_amount = _to_decimal(payload.starting_amount, "starting_amount", allow_negative=False)
    goal.target_amount = _to_decimal(payload.target_amount, "target_amount", allow_negative=False)
    if payload.start_date:
        goal.start_date = _parse_iso_date(payload.start_date, "start_date")
    else:
        goal.start_date = None
    if payload.target_date:
        goal.target_date = _parse_iso_date(payload.target_date, "target_date")
    else:
        goal.target_date = None
    if payload.completed_at:
        goal.completed_at = _parse_iso_date(payload.completed_at, "completed_at")
    else:
        goal.completed_at = None


@app.post("/api/goals", status_code=201)
def create_goal(payload: GoalPayload, session: Session = Depends(get_session)):
    existing = session.scalar(select(Goal).where(Goal.name == payload.name.strip()))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Obiettivo '{payload.name}' già esistente")
    goal = Goal()
    _apply_goal_payload(goal, payload)
    session.add(goal)
    try:
        session.flush()
        session.commit()
        session.refresh(goal)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione obiettivo: {exc}") from exc
    return _goal_to_dict(goal)


@app.patch("/api/goals/{goal_id}")
def update_goal(goal_id: int, payload: GoalPayload, session: Session = Depends(get_session)):
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Obiettivo {goal_id} non trovato")
    if payload.name.strip() != goal.name:
        existing = session.scalar(select(Goal).where(Goal.name == payload.name.strip(), Goal.id != goal_id))
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"Obiettivo '{payload.name}' già esistente")
    _apply_goal_payload(goal, payload)
    try:
        session.commit()
        session.refresh(goal)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento obiettivo: {exc}") from exc
    return _goal_to_dict(goal)


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, session: Session = Depends(get_session)):
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Obiettivo {goal_id} non trovato")
    # Se ci sono transazioni collegate a questo goal, il vincolo è solo logico.
    session.delete(goal)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione obiettivo: {exc}") from exc
    return {"success": True, "deleted_id": goal_id}


# ---------------------------------------------------------------------------
# CRUD Accounts
# ---------------------------------------------------------------------------


class AccountPayload(BaseModel):
    name: str
    source_group: str
    starting_balance: float = 0
    # False per i conti che tracciano un accantonamento: il denaro sta gia'
    # altrove, contarlo qui lo conterebbe due volte.
    counts_in_net_worth: bool = True
    # Un conto chiuso resta nei movimenti storici ma sparisce dalle tendine:
    # archiviare non e' cancellare.
    is_active: bool = True
    is_liquid: bool | None = None
    notes: str | None = None
    # Casa, auto, quote non quotate: il saldo non si deduce dai movimenti.
    needs_manual_valuation: bool = False
    # Il conto dove stanno i titoli: e' li' che puo' puntare un Investment.
    is_broker: bool = False


def _liquidita_predefinita(payload: AccountPayload) -> bool:
    if payload.source_group == "bank":
        return True
    return bool(payload.is_liquid) if payload.source_group == "asset" else False


# Il gruppo decide se una domanda ha senso porla. Un conto corrente e un debito
# hanno un saldo che i movimenti sanno gia' calcolare: la valutazione a mano
# riguarda solo cio' che un prezzo non ce l'ha (casa, auto, quote non quotate) e
# il patrimonio dichiarato.
GRUPPI_VALUTABILI = {"asset"}


def quadratura_gruppo(session: Session, tx_id: int) -> dict[str, Any]:
    """Bonifici e operazioni di un gruppo collegato, e se tornano.

    Il gruppo non e' "questo movimento e le sue operazioni": un'operazione puo'
    essere finanziata da due bonifici dello stesso giorno, e allora nessuno dei
    due da solo pareggia. Si segue la catena movimento -> operazione ->
    movimento finche' non si chiude.

    Il verso lo da' il conto broker: se e' l'origine i soldi escono e le
    operazioni devono fare un netto negativo (vendite), se e' la destinazione il
    contrario. L'importo del movimento e' sempre positivo.
    """
    movimenti, operazioni, da_visitare = set(), set(), {tx_id}
    while da_visitare:
        corrente = da_visitare.pop()
        if corrente in movimenti:
            continue
        movimenti.add(corrente)
        nuove = {r[0] for r in session.execute(
            select(TransactionLedgerLink.ledger_id)
            .where(TransactionLedgerLink.transaction_id == corrente)).all()} - operazioni
        operazioni |= nuove
        if nuove:
            da_visitare |= {r[0] for r in session.execute(
                select(TransactionLedgerLink.transaction_id)
                .where(TransactionLedgerLink.ledger_id.in_(nuove))).all()} - movimenti

    broker = {normalized_name(a.name) for a in session.scalars(
        select(Account).where(Account.is_broker.is_(True))).all()}
    trasferito = Decimal("0")
    for riga in session.scalars(select(Transaction).where(Transaction.id.in_(movimenti))).all():
        verso = -1 if normalized_name(riga.account_name) in broker else 1
        trasferito += verso * abs(Decimal(str(riga.amount or 0)))
    netto = Decimal("0")
    for riga in session.scalars(select(InvestmentTransaction).where(
            InvestmentTransaction.id.in_(operazioni))).all() if operazioni else []:
        segno = 1 if (riga.transaction_type or "").strip().lower() in {"buy", "acquisto"} else -1
        netto += segno * abs(Decimal(str(riga.amount or 0)))
    scarto = (netto - trasferito).quantize(Decimal("0.01"))
    return {"transfers": float(trasferito.quantize(Decimal("0.01"))),
            "operations": float(netto.quantize(Decimal("0.01"))),
            "difference": float(scarto), "balanced": scarto == 0,
            "transactionCount": len(movimenti), "operationCount": len(operazioni)}


def pretendi_quadratura(session: Session, tx_id: int) -> None:
    """Un gruppo collegato deve tornare: i bonifici e le operazioni che
    finanziano devono dire la stessa cifra.

    Si controlla dopo la scrittura, dentro la transazione del database, e si
    annulla tutto se non torna: il gruppo puo' essere rotto sia collegando
    un'operazione sbagliata sia cambiando l'importo di un movimento gia'
    collegato, e controllare prima significherebbe controllare due volte in due
    modi diversi.

    Un gruppo senza operazioni non e' un gruppo: un Investment appena creato non
    ha ancora niente da pareggiare.
    """
    quadratura = quadratura_gruppo(session, tx_id)
    if quadratura["operationCount"] and not quadratura["balanced"]:
        session.rollback()
        raise HTTPException(status_code=422, detail={
            "code": "ledgerGroupUnbalanced", **quadratura})


def solo_investimenti_si_collegano(tx: Transaction) -> None:
    """Chi puo' essere agganciato a un'operazione del ledger.

    Il collegamento dice a quale conto attribuire il guadagno di quell'operazione,
    e solo un Investment ha un conto broker fra i suoi due lati: e' l'unico che
    sa rispondere. Collegare una spesa o un giroconto qualunque attribuirebbe la
    rivalutazione a un conto a caso, o a nessuno.

    Sta qui e non nei tre endpoint perche' le porte per collegare sono tre - dal
    movimento, dall'operazione, e creando i due insieme - e una regola scritta
    in due posti su tre e' una regola che non c'e'.
    """
    if tx.transaction_type != "Investment":
        raise HTTPException(status_code=422, detail={"code": "linkNeedsInvestment"})


def _broker(payload) -> bool:
    """Solo un'attivita' puo' essere un conto broker: una banca e' il posto da
    cui partono i soldi, non quello dove stanno i titoli."""
    return bool(getattr(payload, "is_broker", False)) if payload.source_group == "asset" else False


def _valutazione_manuale(payload: AccountPayload) -> bool:
    return bool(payload.needs_manual_valuation) if payload.source_group in GRUPPI_VALUTABILI else False


def _account_to_dict(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "name": account.name,
        "group": account.source_group,
        "startingBalance": float(account.starting_balance or 0),
        "currentBalance": float(account.current_balance or 0),
        "countsInNetWorth": account.counts_in_net_worth,
        "isActive": account.is_active,
        "isLiquid": account.is_liquid,
        "isBroker": account.is_broker,
        "needsManualValuation": account.needs_manual_valuation,
        "notes": account.notes,
    }


@app.post("/api/accounts", status_code=201)
def create_account(payload: AccountPayload, session: Session = Depends(get_session)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome conto obbligatorio")
    if payload.source_group not in {"bank", "asset", "liability"}:
        raise HTTPException(status_code=422, detail="Gruppo conto non valido")
    existing = session.scalar(
        select(Account).where(Account.source_group == payload.source_group, Account.name == name)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Conto '{name}' già presente nel gruppo {payload.source_group}")
    starting = _to_decimal(payload.starting_balance, "starting_balance")
    account = Account(
        source_group=payload.source_group,
        name=name,
        starting_balance=starting,
        current_balance=starting,
        status=None,
        counts_in_net_worth=payload.counts_in_net_worth,
        is_liquid=_liquidita_predefinita(payload),
        notes=(payload.notes or "").strip() or None,
        needs_manual_valuation=_valutazione_manuale(payload),
        is_broker=_broker(payload),
    )
    session.add(account)
    try:
        session.flush()
        session.commit()
        session.refresh(account)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione conto: {exc}") from exc
    return _account_to_dict(account)


@app.patch("/api/accounts/{account_id}")
def update_account(account_id: int, payload: AccountPayload, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Conto {account_id} non trovato")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome conto obbligatorio")
    if payload.source_group not in {"bank", "asset", "liability"}:
        raise HTTPException(status_code=422, detail="Gruppo conto non valido")
    duplicate = session.scalar(
        select(Account).where(
            Account.source_group == payload.source_group,
            Account.name == name,
            Account.id != account_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail=f"Conto '{name}' già presente nel gruppo {payload.source_group}")
    account.source_group = payload.source_group
    account.name = name
    account.counts_in_net_worth = payload.counts_in_net_worth
    account.is_active = payload.is_active
    account.is_liquid = _liquidita_predefinita(payload)
    account.notes = (payload.notes or "").strip() or None
    # Spostare un conto in un gruppo che non si valuta a mano spegne il flag:
    # lasciarlo acceso lo terrebbe invisibile nel form e attivo nei calcoli.
    account.needs_manual_valuation = _valutazione_manuale(payload)
    account.is_broker = _broker(payload)
    # Il saldo iniziale e' l'unica leva per far quadrare un conto la cui storia
    # comincia prima dei movimenti registrati: senza, l'unico modo di correggerlo
    # e' una UPDATE a mano sul database.
    account.starting_balance = _to_decimal(payload.starting_balance, "starting_balance")
    # current_balance NON si tocca: e' il saldo dichiarato, e la riconciliazione
    # confronta starting + movimenti contro di lui. Riallinearlo qui farebbe
    # sparire proprio la differenza che si sta cercando di capire.
    # Lo status viene gestito solo dall'import Excel; l'editor UI non lo tocca.
    try:
        session.commit()
        session.refresh(account)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento conto: {exc}") from exc
    return _account_to_dict(account)


class ValuationPayload(BaseModel):
    observed_on: str
    value: float
    notes: str | None = None


@app.post("/api/accounts/{account_id}/valuations", status_code=201)
def create_valuation(account_id: int, payload: ValuationPayload, session: Session = Depends(get_session)):
    """Registra quanto vale un conto a una data.

    La data e' obbligatoria e non e' un dettaglio: e' cio' che distingue questa
    tabella dall'alzare `starting_balance`, che cambierebbe anche il patrimonio
    degli anni in cui quel valore non lo conoscevi.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Conto {account_id} non trovato")
    quando = _parse_iso_date(payload.observed_on, "observed_on")
    esistente = session.scalar(select(AccountValuation).where(
        AccountValuation.account_id == account_id, AccountValuation.observed_on == quando))
    # Due valutazioni dello stesso conto nello stesso giorno non sono due fatti:
    # e' una correzione. L'ultima scritta vince.
    riga = esistente or AccountValuation(account_id=account_id, observed_on=quando)
    riga.value = _to_decimal(payload.value, "value")
    riga.notes = (payload.notes or "").strip() or None
    session.add(riga)
    try:
        session.commit()
        session.refresh(riga)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore salvataggio valutazione: {exc}") from exc
    return {"id": riga.id, "accountId": riga.account_id, "observedOn": riga.observed_on.isoformat(),
            "value": float(riga.value), "notes": riga.notes}


@app.delete("/api/accounts/{account_id}/valuations/{valuation_id}")
def delete_valuation(account_id: int, valuation_id: int, session: Session = Depends(get_session)):
    riga = session.scalar(select(AccountValuation).where(
        AccountValuation.id == valuation_id, AccountValuation.account_id == account_id))
    if riga is None:
        raise HTTPException(status_code=404, detail=f"Valutazione {valuation_id} non trovata")
    session.delete(riga)
    session.commit()
    return {"deleted": valuation_id}


class LiabilityDrawdownPayload(BaseModel):
    occurred_on: str
    amount: float = Field(gt=0)


class LiabilityPayload(BaseModel):
    # Una linea di credito non ha capitale originario: `original_principal` resta
    # obbligatorio per i prestiti, e per le linee si ignora.
    kind: str = "term_loan"
    credit_limit: float | None = Field(default=None, ge=0)
    debt_type: str = "other"
    original_principal: float = Field(gt=0)
    annual_rate: float = Field(ge=0)
    rate_type: str = "fixed"
    payment_frequency: str = "monthly"
    payment_structure: str = "amortizing"
    grace_interest: str = "paid"
    start_date: str
    repayment_start_date: str | None = None
    end_date: str
    planned_drawdowns: list[LiabilityDrawdownPayload] = Field(default_factory=list)
    status: str = "active"
    notes: str | None = None


def _liability_drawdowns(row: LiabilityProfile) -> list[dict[str, Any]]:
    try:
        values = json.loads(row.planned_drawdowns or "[]")
        return [{"occurredOn": str(item["occurredOn"]), "amount": float(item["amount"])}
                for item in values if float(item.get("amount", 0)) > 0]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return []


def _liability_profile_dict(row: LiabilityProfile) -> dict[str, Any]:
    return {"debtType": row.debt_type, "originalPrincipal": float(row.original_principal),
            "annualRate": float(row.annual_rate), "rateType": row.rate_type,
            "paymentFrequency": row.payment_frequency, "paymentStructure": row.payment_structure,
            "graceInterest": row.grace_interest,
            "startDate": row.start_date.isoformat(), "endDate": row.end_date.isoformat(),
            "repaymentStartDate": (row.repayment_start_date or row.start_date).isoformat(),
            "plannedDrawdowns": _liability_drawdowns(row),
            "kind": row.kind,
            "creditLimit": float(row.credit_limit) if row.credit_limit is not None else None,
            "status": row.status, "notes": row.notes}


def _month_ends(start: date, end: date) -> list[date]:
    result = []
    cursor = date(start.year, start.month, monthrange(start.year, start.month)[1])
    while cursor < end:
        result.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        cursor = date(cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1])
    result.append(end)
    return result


def _esposizione_nel_tempo(session: Session, account_id: int,
                           name: str) -> tuple[Decimal, list[dict[str, Any]]]:
    """Picco e andamento dell'esposizione, da una sola lettura dei saldi.

    Passa dalla stessa funzione che calcola i saldi della pagina Patrimonio,
    con una data per ogni movimento che tocca il conto: scriverne un'altra
    vorrebbe dire riscrivere la convenzione dei segni e prima o poi divergere.
    I giroconti contano - sul conto passano e il saldo lo muovono - ed e' il
    punto cieco che faceva divergere questa pagina dal saldo del conto.

    Il picco vuole la granularita' dei movimenti, il grafico un punto al mese:
    entrambi escono dalla stessa passata invece di leggere i saldi due volte.
    """
    movimenti = movimenti_per_saldi(session)
    oggi = date.today()
    date_utili = sorted({oggi} | {tx.effective_on for tx in movimenti
                         if tx.effective_on <= oggi and name in {tx.account_name, tx.destination_name}})
    conto = session.get(Account, account_id)
    serie = account_balances_series([conto], movimenti, date_utili)
    picco = max(-conto.starting_balance, Decimal("0"))
    per_mese: dict[tuple[int, int], Decimal] = {}
    for quando, riga in zip(date_utili, serie):
        # I saldi delle passivita' tornano col segno girato: il debito e' positivo.
        valore = Decimal(str(riga["totals"]["liability"]))
        picco = max(picco, valore)
        # L'ultimo movimento del mese vince: e' l'esposizione a fine mese.
        per_mese[(quando.year, quando.month)] = valore
    andamento = [{"period": f"{anno}-{mese:02d}",
                  "label": f"{mese:02d}/{anno}",
                  "exposure": float(valore)}
                 for (anno, mese), valore in sorted(per_mese.items())]
    return picco, andamento


def _voce_linea_di_credito(account: dict[str, Any], profile: LiabilityProfile,
                           dettagli: list[dict[str, Any]], movimenti: list[dict[str, Any]],
                           picco: Decimal, andamento: list[dict[str, Any]],
                           oggi: date) -> dict[str, Any]:
    """Lo stato di uno scoperto: un saldo, non un piano.

    Niente tranche, niente scadenze, niente residuo teorico: di una linea
    interessano l'esposizione di adesso, quella piu' alta che si e' raggiunta,
    quanto sono costati gli interessi e a che tasso. Il limite e' facoltativo -
    uno scoperto che cresce con gli interessi che ci si aggiungono non ne ha.
    """
    esposizione = Decimal(str(account["value"]))
    addebiti_anno = sum((Decimal(str(riga["interest"])) for riga in dettagli
                         if riga["kind"] == "charge" and riga["classified"]
                         and date.fromisoformat(riga["occurredOn"]).year == oggi.year), Decimal("0"))
    limite = Decimal(str(profile.credit_limit)) if profile.credit_limit is not None else None
    non_classificati = [riga for riga in dettagli
                        if riga["kind"] in ("repayment", "charge") and not riga["classified"]]
    return {"accountId": account["id"], "name": account["name"], "kind": "credit_line",
            "accountNotes": account.get("notes"),
            "profile": _liability_profile_dict(profile),
            "exposure": float(esposizione),
            "creditLimit": float(limite) if limite is not None else None,
            # Percentuale solo se un limite c'e': inventarne uno per poter
            # mostrare un utilizzo direbbe una cosa che non sappiamo.
            "utilisation": float((esposizione / limite * 100).quantize(Decimal("0.1")))
                           if limite and limite > 0 else None,
            "peakExposure": float(picco),
            "interestThisYear": float(addebiti_anno.quantize(Decimal("0.01"))),
            "rate": float(profile.annual_rate),
            "unclassified": {"count": len(non_classificati),
                             "amount": round(sum(riga["total"] for riga in non_classificati), 2)},
            "trend": andamento,
            # Gli stessi dettagli del prestito: servono a marcare in tabella le
            # righe ancora da classificare.
            "payments": [riga for riga in dettagli if riga["transactionIds"][0] in
                         {movimento["id"] for movimento in movimenti[:50]}],
            "movements": movimenti[:50]}


@app.get("/api/liabilities")
def liabilities(session: Session = Depends(get_session)):
    from .core_routes import accounts as account_rows, valutazioni_per_conto, rivalutazioni_per_conto

    today = date.today()
    accounts_data = [row for row in account_rows(at=None, session=session)["items"] if row["group"] == "liability"]
    profiles = {row.account_id: row for row in session.scalars(select(LiabilityProfile)).all()}
    names = [row["name"] for row in accounts_data]
    transactions = session.scalars(select(Transaction).where(
        REAL_MOVEMENT,
        Transaction.effective_on <= today,
        or_(Transaction.account_name.in_(names), Transaction.destination_name.in_(names)))
        .order_by(Transaction.effective_on.desc(), Transaction.id.desc())).all() if names else []
    conti = {row.id: row for row in session.scalars(select(Account)).all()}
    stime, rivalutazioni = valutazioni_per_conto(session), rivalutazioni_per_conto(session)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names_set = set(names)
    for tx in transactions:
        item = {"id": tx.id, "occurredOn": tx.effective_on.isoformat(), "type": tx.transaction_type,
                "amount": float(tx.amount), "description": tx.details or tx.category,
                "accountName": tx.account_name, "destinationName": tx.destination_name}
        for name in {tx.account_name, tx.destination_name} & names_set:
            by_name[name].append({**item, "effect": float(-calculate_account_balance(0, name, [tx]))})

    payment_rows = session.scalars(select(LiabilityTransactionDetail).order_by(
        LiabilityTransactionDetail.id.desc())).all()
    payment_transactions = {row.id: row for row in transactions}
    payments: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in payment_rows:
        principal_tx = payment_transactions.get(row.transaction_id)
        if principal_tx is None:
            continue
        payments[row.liability_account_id].append({
            "id": row.id, "occurredOn": principal_tx.effective_on.isoformat(), "kind": row.kind,
            "principal": float(row.principal_amount), "interest": float(row.interest_amount),
            "total": float(row.principal_amount + row.interest_amount),
            "classified": row.is_classified,
            "description": principal_tx.details or "",
            "sourceAccount": principal_tx.account_name, "destinationAccount": principal_tx.destination_name,
            "transactionIds": [principal_tx.id],
        })

    items, total_debt, rated_debt, weighted_rate, monthly_service = [], Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    for account in accounts_data:
        opening_principal = max(-Decimal(str(account["startingBalance"])), Decimal("0"))
        profile = profiles.get(account["id"])
        planned_drawdowns = _liability_drawdowns(profile) if profile else []
        if profile and not planned_drawdowns:
            planned_drawdowns = [{"occurredOn": profile.start_date.isoformat(),
                                  "amount": float(profile.original_principal)}]
        schedule, motivo_piano = (piano_ammortamento(profile.original_principal, profile.annual_rate,
                                                     profile.start_date, profile.end_date,
                                                     profile.payment_frequency, profile.payment_structure,
                                                     planned_drawdowns, profile.repayment_start_date,
                                                     profile.grace_interest)
                                  if profile and profile.kind != "credit_line" else ([], None))
        actual_rows = payments.get(account["id"], [])
        if profile is not None and profile.kind == "credit_line":
            picco, andamento = _esposizione_nel_tempo(session, account["id"], account["name"])
            items.append(_voce_linea_di_credito(
                account, profile, actual_rows, by_name.get(account["name"], []),
                picco, andamento, today))
            exposure = Decimal(str(account["value"]))
            total_debt += exposure
            rated_debt += max(exposure, Decimal("0"))
            weighted_rate += max(exposure, Decimal("0")) * profile.annual_rate
            continue
        # Il saldo iniziale esiste prima dei movimenti, non dalla data del piano.
        opening_on = min([today, profile.start_date if profile else today]
                         + [date.fromisoformat(row["occurredOn"]) for row in actual_rows])
        actual_drawdowns = ([{"occurredOn": opening_on.isoformat(), "amount": float(opening_principal)}]
                            if opening_principal else [])
        actual_drawdowns += [{"occurredOn": row["occurredOn"], "amount": row["principal"]}
                             for row in actual_rows if row["kind"] == "drawdown"]
        actual_repayments = [{"occurredOn": row["occurredOn"], "principal": row["principal"],
                              "interest": row["interest"]}
                             for row in actual_rows if row["kind"] == "repayment"]
        account_transactions = [tx for tx in transactions
                                if account["name"] in {tx.account_name, tx.destination_name}]
        # Solo gli addebiti dichiarati: un `Expenses` sul conto senza riga di
        # dettaglio classificata non e' un interesse finche' non lo si dice.
        actual_charges = [{"occurredOn": row["occurredOn"], "amount": row["interest"]}
                          for row in actual_rows if row["kind"] == "charge" and row["classified"]]
        actual_state = stato_debito_registrato(actual_drawdowns, actual_repayments,
                                               actual_charges, today)
        drawn_principal = actual_state["drawnPrincipal"]
        principal_repaid = actual_state["repaidPrincipal"]
        outstanding = actual_state["principalOutstanding"]
        interest_accrued = actual_state["interestCharged"]
        interest_paid = actual_state["interestPaid"]
        interest_outstanding = actual_state["interestOutstanding"]
        # Il saldo e' quello dei Conti. La classificazione lo spiega, senza
        # cancellare dal totale giroconti o addebiti ancora da riconciliare.
        actual_total_debt = Decimal(str(account["value"]))
        reconciliation = actual_total_debt - actual_state["totalDebt"]
        first_repayment = min((tx.occurred_on for tx in account_transactions
                               if tx.transaction_type == "Debt" and tx.destination_name == account["name"]),
                              default=date.max)
        suggested_drawdowns = [{"occurredOn": tx.occurred_on.isoformat(), "amount": float(tx.amount)}
                               for tx in reversed(account_transactions)
                               if tx.transaction_type == "Debt" and tx.account_name == account["name"]
                               and tx.occurred_on < first_repayment]
        comparison = None
        trend = []
        if profile:
            first_date = min([date.fromisoformat(row["occurredOn"]) for row in planned_drawdowns + actual_drawdowns])
            final_date = max(today, profile.end_date)
            cutoffs = sorted(set(_month_ends(first_date, final_date)) | {today})
            balances = account_balances_series([conti[account["id"]]], transactions, cutoffs,
                                                stime, rivalutazioni)
            planned_principal = planned_interest = Decimal("0")
            for cutoff, balance in zip(cutoffs, balances):
                fino_a = cutoff.isoformat()
                due = [row for row in schedule if row["dueOn"][:7] == cutoff.isoformat()[:7]]
                past_rows = [row for row in schedule if row["dueOn"] <= fino_a]
                planned_debt = debito_pianificato_al(schedule, planned_drawdowns,
                                                     profile.annual_rate, cutoff)
                planned_interest_cum = sum((Decimal(str(row["interest"])) for row in past_rows), Decimal("0"))
                state_at = stato_debito_registrato(actual_drawdowns, actual_repayments,
                                                   actual_charges, cutoff)
                passato = cutoff <= today
                trend.append({"period": cutoff.isoformat(), "label": cutoff.strftime("%d/%m/%Y"),
                              "plannedDebt": float(planned_debt),
                              "actualDebt": balance["totals"]["liability"] if passato else None,
                              "actualDrawn": float(state_at["drawnPrincipal"]) if passato else None,
                              "actualRepaid": float(state_at["repaidPrincipal"]) if passato else None,
                              "plannedInterest": float(planned_interest_cum.quantize(Decimal("0.01"))),
                              "actualInterestCharged": float(state_at["interestCharged"]) if passato else None,
                              "actualInterestPaid": float(state_at["interestPaid"]) if passato else None,
                              "plannedPayment": round(sum(row["payment"] for row in due), 2)})
            planned_principal = sum((Decimal(str(row["principal"])) for row in schedule
                                     if row["dueOn"] <= today.isoformat()), Decimal("0"))
            planned_interest = sum((Decimal(str(row["interest"])) for row in schedule
                                    if row["dueOn"] <= today.isoformat()), Decimal("0"))
            planned_total = sum((Decimal(str(row["payment"])) for row in schedule
                                 if row["dueOn"] <= today.isoformat()), Decimal("0"))
            comparison = {"plannedDebt": float(debito_pianificato_al(schedule, planned_drawdowns,
                                                                      profile.annual_rate, today)),
                          "actualDebt": float(actual_total_debt), "plannedPrincipal": float(planned_principal),
                          "actualPrincipal": float(principal_repaid), "plannedInterest": float(planned_interest),
                          "actualInterest": float(interest_paid), "plannedTotal": float(planned_total),
                          "actualTotal": float(principal_repaid + interest_paid)}
        next_payment = (next((row for row in schedule if row["dueOn"] >= today.isoformat()), None)
                        if profile and profile.status == "active" else None)
        theoretical = comparison["plannedDebt"] if comparison else None
        frequency = {"monthly": 12, "quarterly": 4, "annual": 1}.get(profile.payment_frequency, 12) if profile else 0
        monthly = (Decimal(str(next_payment["payment"])) * Decimal(frequency) / Decimal(12)
                   if next_payment and profile.status == "active" else Decimal("0"))
        total_debt += actual_total_debt
        if profile:
            rated_debt += max(actual_total_debt, Decimal("0"))
            weighted_rate += max(actual_total_debt, Decimal("0")) * profile.annual_rate
            monthly_service += monthly
        unclassified = [row for row in payments.get(account["id"], [])
                        if row["kind"] in ("repayment", "charge") and not row["classified"]]
        items.append({"accountId": account["id"], "name": account["name"], "kind": "term_loan",
                      "outstanding": float(outstanding),
                      "startingBalance": abs(account["startingBalance"]), "accountNotes": account.get("notes"),
                      "profile": _liability_profile_dict(profile) if profile else None,
                      "payment": schedule[0]["payment"] if schedule else None,
                      "monthlyService": float(monthly.quantize(Decimal("0.01"))),
                      "nextPayment": next_payment, "totalInterest": round(sum(row["interest"] for row in schedule), 2),
                      "theoreticalRemaining": theoretical,
                      "difference": round(float(actual_total_debt) - theoretical, 2) if theoretical is not None else None,
                      "drawnPrincipal": float(drawn_principal), "principalRepaid": float(principal_repaid),
                      "interestCharged": float(interest_accrued),
                      "interestPaid": float(interest_paid),
                      "interestOutstanding": float(interest_outstanding),
                      "actualTotalDebt": float(actual_total_debt),
                      "reconciliationDifference": float(reconciliation),
                      "unclassified": {"count": len(unclassified),
                                       "amount": round(sum(row["total"] for row in unclassified), 2)},
                      "suggestedDrawdowns": suggested_drawdowns, "trend": trend, "comparison": comparison,
                      "schedule": schedule, "scheduleIssue": motivo_piano,
                      "payments": [row for row in actual_rows if row["transactionIds"][0] in
                                   {movement["id"] for movement in by_name.get(account["name"], [])[:50]}],
                      "movements": by_name.get(account["name"], [])[:50]})
    all_unclassified = [row for rows in payments.values() for row in rows
                        if row["kind"] in ("repayment", "charge") and not row["classified"]]
    return {"asOf": today.isoformat(), "summary": {"totalDebt": float(total_debt),
                        "weightedRate": float((weighted_rate / rated_debt).quantize(Decimal("0.01"))) if rated_debt else None,
                        "monthlyService": float(monthly_service.quantize(Decimal("0.01"))),
                        "configured": sum(account["id"] in profiles for account in accounts_data),
                        "total": len(accounts_data),
                        "unclassifiedCount": len(all_unclassified),
                        "unclassifiedAmount": round(sum(row["total"] for row in all_unclassified), 2)},
            "orphaned": [{"accountId": row.account_id, "kind": row.kind, "notes": row.notes}
                         for row in profiles.values() if row.account_id not in conti],
            "items": items}


@app.put("/api/liabilities/{account_id}")
def save_liability(account_id: int, payload: LiabilityPayload, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None or account.source_group != "liability":
        raise HTTPException(404, detail="liabilityAccountNotFound")
    allowed = ({"mortgage", "personal", "leasing", "revolving", "margin", "informal", "other"},
               {"fixed", "variable", "mixed"}, {"monthly", "quarterly", "annual"},
               {"amortizing", "constant_principal", "interest_only", "bullet"}, {"active", "paid", "suspended"})
    values = (payload.debt_type, payload.rate_type, payload.payment_frequency,
              payload.payment_structure, payload.status)
    if any(value not in choices for value, choices in zip(values, allowed)):
        raise HTTPException(422, detail="liabilityInvalidFields")
    if payload.kind not in {"term_loan", "credit_line"}:
        raise HTTPException(422, detail="liabilityInvalidFields")
    start, end = _parse_iso_date(payload.start_date, "start_date"), _parse_iso_date(payload.end_date, "end_date")
    if end <= start:
        raise HTTPException(422, detail="liabilityEndBeforeStart")
    if payload.kind == "credit_line":
        # Niente piano da validare: di una linea servono il tasso, le date e -
        # se esiste - il limite. Le tranche restano vuote di proposito.
        row = session.scalar(select(LiabilityProfile).where(LiabilityProfile.account_id == account_id))
        if row is None:
            row = LiabilityProfile(account_id=account_id)
            session.add(row)
        row.kind, row.debt_type = "credit_line", payload.debt_type
        row.credit_limit = (_to_decimal(payload.credit_limit, "credit_limit", allow_negative=False)
                            if payload.credit_limit else None)
        row.original_principal = _to_decimal(payload.original_principal, "original_principal", allow_negative=False)
        row.annual_rate, row.rate_type = _to_decimal(payload.annual_rate, "annual_rate", allow_negative=False), payload.rate_type
        row.start_date, row.end_date, row.repayment_start_date = start, end, None
        row.planned_drawdowns = "[]"
        row.status, row.notes = payload.status, (payload.notes or "").strip() or None
        session.commit(); session.refresh(row)
        return _liability_profile_dict(row)
    repayment_start = (_parse_iso_date(payload.repayment_start_date, "repayment_start_date")
                       if payload.repayment_start_date else start)
    drawdowns = sorted(({"occurredOn": _parse_iso_date(item.occurred_on, "planned_drawdowns").isoformat(),
                         "amount": float(_to_decimal(item.amount, "planned_drawdowns", allow_negative=False))}
                        for item in payload.planned_drawdowns), key=lambda item: item["occurredOn"])
    if not drawdowns:
        drawdowns = [{"occurredOn": start.isoformat(), "amount": payload.original_principal}]
    # Un codice per regola: con uno solo ("date non valide") il modulo poteva
    # dire soltanto "impossibile salvare", e con piu' tranche e l'inizio dei
    # rimborsi vuoto non c'era modo di capire quale campo correggere.
    if (payload.payment_structure != "bullet" and repayment_start >= end) or repayment_start > end:
        raise HTTPException(422, detail="liabilityRepaymentAfterEnd")
    if any(date.fromisoformat(item["occurredOn"]) > end for item in drawdowns):
        raise HTTPException(422, detail="liabilityDrawdownAfterEnd")
    if (payload.payment_structure in {"amortizing", "constant_principal"}
            and any(date.fromisoformat(item["occurredOn"]) > repayment_start for item in drawdowns)):
        raise HTTPException(422, detail="liabilityDrawdownAfterRepaymentStart")
    if abs(sum(item["amount"] for item in drawdowns) - payload.original_principal) > .01:
        raise HTTPException(422, detail="liabilityDrawdownsNotPrincipal")
    row = session.scalar(select(LiabilityProfile).where(LiabilityProfile.account_id == account_id))
    if row is None:
        row = LiabilityProfile(account_id=account_id)
        session.add(row)
    row.debt_type, row.original_principal, row.annual_rate = payload.debt_type, _to_decimal(payload.original_principal, "original_principal"), _to_decimal(payload.annual_rate, "annual_rate", allow_negative=False)
    row.rate_type, row.payment_frequency, row.payment_structure = payload.rate_type, payload.payment_frequency, payload.payment_structure
    row.grace_interest = payload.grace_interest if payload.grace_interest in {"paid", "capitalised"} else "paid"
    row.start_date, row.repayment_start_date, row.end_date = start, repayment_start, end
    row.planned_drawdowns = json.dumps(drawdowns, separators=(",", ":"))
    row.kind, row.credit_limit = "term_loan", None
    row.status, row.notes = payload.status, (payload.notes or "").strip() or None
    session.commit(); session.refresh(row)
    return _liability_profile_dict(row)


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session),
                   confirm_liability_account: str | None = None):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Conto {account_id} non trovato")
    if account.source_group == "liability":
        return delete_liability(account_id, LiabilityDeletePayload(account_name=confirm_liability_account), session)
    linked_transactions = session.scalar(
        select(func.count(Transaction.id)).where(
            or_(Transaction.account_name == account.name, Transaction.destination_name == account.name)
        )
    ) or 0
    payload = {
        "id": account.id, "name": account.name, "group": account.source_group,
        "linked_transactions": int(linked_transactions),
    }
    profile = session.scalar(select(LiabilityProfile).where(LiabilityProfile.account_id == account_id))
    if profile:
        session.delete(profile)
    session.delete(account)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione conto: {exc}") from exc
    return {"deleted": True, **payload}


class LiabilityDeletePayload(BaseModel):
    account_name: str | None = None


@app.delete("/api/liabilities/{account_id}")
def delete_liability(account_id: int, payload: LiabilityDeletePayload,
                     session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    profile = session.scalar(select(LiabilityProfile).where(LiabilityProfile.account_id == account_id))
    if profile is None and (account is None or account.source_group != "liability"):
        raise HTTPException(404, detail="liabilityAccountNotFound")
    # La conferma riguarda il conto attuale, non quello che il client ricorda:
    # un nome cambiato nel frattempo richiede una nuova conferma.
    if account is not None and payload.account_name != account.name:
        raise HTTPException(409, detail="debtDeleteAccountConfirmation")
    try:
        backup = create_backup(f"pre-delete-debt-{account_id}-{time.time_ns()}")
        if not backup.get("success"):
            raise RuntimeError("backup-failed")
    except Exception:
        raise HTTPException(503, detail="debtDeleteBackupFailed") from None
    # Nessuna riga viene modificata prima della copia riuscita. I movimenti
    # restano nello storico; i dettagli del debito non devono restare orfani.
    for detail in session.scalars(select(LiabilityTransactionDetail).where(
            LiabilityTransactionDetail.liability_account_id == account_id)).all():
        session.delete(detail)
    if profile is not None:
        session.delete(profile)
    if account is not None:
        for valuation in session.scalars(select(AccountValuation).where(
                AccountValuation.account_id == account_id)).all():
            session.delete(valuation)
        session.delete(account)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(500, detail="debtDeleteFailed") from None
    return {"deleted": True, "backup": backup["filename"]}


# ---------------------------------------------------------------------------
# CRUD Notes (Fase 1 - sotto-step 1.2)
# ---------------------------------------------------------------------------


def _note_to_dict(note: Note) -> dict:
    return {
        "id": note.id,
        "section": note.section,
        "title": note.title,
        "body": note.body or "",
        "status": note.status,
        "updatedAt": note.updated_at.isoformat() if note.updated_at else None,
    }


class NotePayload(BaseModel):
    section: str = "Appunti"
    title: str
    body: str | None = None
    status: str | None = None


def _apply_note_payload(note: Note, payload: NotePayload) -> None:
    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=422, detail="Titolo nota obbligatorio")
    note.section = (payload.section or "Appunti").strip()
    note.title = payload.title.strip()
    note.body = payload.body
    note.status = payload.status


@app.post("/api/notes", status_code=201)
def create_note(payload: NotePayload, session: Session = Depends(get_session)):
    note = Note()
    _apply_note_payload(note, payload)
    session.add(note)
    try:
        session.flush()
        session.commit()
        session.refresh(note)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione nota: {exc}") from exc
    return _note_to_dict(note)


@app.patch("/api/notes/{note_id}")
def update_note(note_id: int, payload: NotePayload, session: Session = Depends(get_session)):
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Nota {note_id} non trovata")
    _apply_note_payload(note, payload)
    try:
        session.commit()
        session.refresh(note)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento nota: {exc}") from exc
    return _note_to_dict(note)


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, session: Session = Depends(get_session)):
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Nota {note_id} non trovata")
    session.delete(note)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione nota: {exc}") from exc
    return {"success": True, "deleted_id": note_id}


# ---------------------------------------------------------------------------
# Settings & LookupOptions (Fase 1 - sotto-step 1.2)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Preferenze applicative
# ---------------------------------------------------------------------------


class SettingValueUpdate(BaseModel):
    value: str
    label: str | None = None


@app.put("/api/settings/{key}")
def update_setting(key: str, payload: SettingValueUpdate, session: Session = Depends(get_session)):
    """Aggiorna una singola AppSetting (pannello Preferenze)."""
    if not key or not key.strip():
        raise HTTPException(status_code=422, detail="Chiave impostazione obbligatoria")
    setting = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if setting is None:
        setting = AppSetting(key=key, label=payload.label or key, value=payload.value)
        session.add(setting)
    else:
        setting.value = payload.value
        if payload.label is not None:
            setting.label = payload.label
    try:
        # Come per l'aggiornamento in blocco: i parametri dello shift entrate
        # tardive cambiano la competenza dei movimenti gia' registrati.
        recalculated = recompute_effective_dates(session) if key in ("late_income_shift", "late_income_day") else 0
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore salvataggio impostazione: {exc}") from exc
    return {"success": True, "key": key, "value": payload.value, "effectiveDatesRecalculated": recalculated}




# ---------------------------------------------------------------------------
# Investments: ledger, instruments, market-data (Fase 1 - sotto-step 1.3)
# ---------------------------------------------------------------------------


VALID_INVESTMENT_TX_TYPES = {"Buy", "Sell", "Dividend", "Fee", "Deposit", "Withdrawal", "Split"}

# Il rapporto di uno split sta nelle quote: 2 = due nuove per una vecchia, 0,5 =
# un raggruppamento. Oltre mille non e' un frazionamento, e' un errore di
# battitura, e le quote non si aggiustano a mano da sole.
RAPPORTO_SPLIT_MASSIMO = Decimal("1000")


def _investment_tx_to_dict(tx: InvestmentTransaction, detail: InvestmentTransactionDetail | None = None) -> dict:
    return {
        "id": tx.id,
        "ticker": tx.ticker,
        "name": tx.name,
        "occurredOn": tx.occurred_on.isoformat(),
        "transactionType": tx.transaction_type,
        "amount": float(tx.amount or 0),
        "units": float(tx.units or 0) if tx.units is not None else None,
        "price": float(tx.price or 0) if tx.price is not None else None,
        "currency": tx.currency or "EUR",
        "fee": float(detail.fee) if detail else 0,
        "notes": detail.notes if detail else None,
        "sourceRow": tx.source_row,
    }


def _instrument_to_dict(inst: InvestmentInstrument) -> dict:
    return {
        "id": inst.id,
        "name": inst.name,
        "providerSymbol": inst.provider_symbol,
        "isin": inst.isin,
        "assetClass": inst.asset_class,
        "area": inst.area,
        "sector": inst.sector,
        "currency": inst.currency,
        "targetWeight": float(inst.target_weight) if inst.target_weight is not None else None,
    }


class InvestmentTxPayload(BaseModel):
    name: str
    transaction_type: str
    amount: float
    occurred_on: str
    ticker: str | None = None
    units: float | None = None
    price: float | None = None
    currency: str | None = "EUR"
    fee: float = 0
    notes: str | None = None


def _valida_split(session: Session, payload: InvestmentTxPayload) -> None:
    """Uno split non muove denaro e parla di uno strumento che esiste.

    Un rapporto fuori scala moltiplicherebbe le quote per un numero che non
    vuol dire niente, e il prezzo medio (costo diviso quote) mentirebbe senza
    che si veda niente; un importo diverso da zero direbbe che quello split e'
    stato pagato.
    """
    rapporto = _to_decimal(payload.units, "units") if payload.units is not None else None
    if rapporto is None or rapporto <= 0 or rapporto >= RAPPORTO_SPLIT_MASSIMO:
        raise HTTPException(status_code=422, detail={"code": "ledgerSplitRatioNonValido"})
    if _to_decimal(payload.amount, "amount") != 0:
        raise HTTPException(status_code=422, detail={"code": "ledgerSplitAmountNonZero"})
    # Uno split su uno strumento mai comprato non aggiusta niente: il motore lo
    # scarterebbe, e chi l'ha registrato crederebbe di averlo fatto.
    filtro = (InvestmentTransaction.ticker == payload.ticker if payload.ticker
              else InvestmentTransaction.name == payload.name.strip())
    esistente = session.scalar(select(InvestmentTransaction.id).where(
        InvestmentTransaction.transaction_type.in_(("Buy", "Sell")), filtro).limit(1))
    if esistente is None:
        raise HTTPException(status_code=422, detail={"code": "ledgerSplitStrumentoInesistente"})


def _apply_investment_tx(tx: InvestmentTransaction, payload: InvestmentTxPayload, session: Session) -> None:
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="name obbligatorio")
    if payload.transaction_type not in VALID_INVESTMENT_TX_TYPES:
        raise HTTPException(status_code=422, detail=f"transaction_type non valido: {payload.transaction_type}")
    if not payload.occurred_on:
        raise HTTPException(status_code=422, detail="occurred_on obbligatorio")
    # Prima di toccare la riga: se lo split non e' valido la transazione non
    # deve restare modificata a meta'.
    if payload.transaction_type == "Split":
        _valida_split(session, payload)
    tx.name = payload.name.strip()
    tx.transaction_type = payload.transaction_type
    tx.amount = _to_decimal(payload.amount, "amount")
    tx.occurred_on = _parse_iso_date(payload.occurred_on, "occurred_on")
    tx.ticker = (payload.ticker or None) or None
    tx.currency = (payload.currency or "EUR").strip() or "EUR"
    if payload.units is not None:
        tx.units = _to_decimal(payload.units, "units")
    else:
        tx.units = None
    if payload.price is not None:
        tx.price = _to_decimal(payload.price, "price")
    else:
        tx.price = None


def operazione_gia_presente(session: Session, payload: InvestmentTxPayload) -> InvestmentTransaction | None:
    """Trova la stessa operazione entro i tre giorni di tolleranza del broker."""
    giorno = _parse_iso_date(payload.occurred_on, "occurred_on")
    quote = _to_decimal(payload.units, "units") if payload.units is not None else None
    condizioni = [
        InvestmentTransaction.name == payload.name.strip(),
        InvestmentTransaction.transaction_type == payload.transaction_type,
        InvestmentTransaction.amount == _to_decimal(payload.amount, "amount"),
        InvestmentTransaction.occurred_on.between(giorno - timedelta(days=3), giorno + timedelta(days=3)),
        InvestmentTransaction.units.is_(None) if quote is None else InvestmentTransaction.units == quote,
    ]
    return session.scalar(select(InvestmentTransaction).where(*condizioni).order_by(
        InvestmentTransaction.occurred_on, InvestmentTransaction.id).limit(1))


def _duplicate_detail(tx: InvestmentTransaction) -> dict[str, Any]:
    return {"code": "ledgerDuplicate", "duplicate": _investment_tx_to_dict(tx)}


def _ledger_csv_rows(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text_content = None
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            text_content = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text_content is None:
        raise HTTPException(422, detail="ledgerCsvEncoding")
    try:
        dialect = csv.Sniffer().sniff(text_content[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text_content), dialect=dialect)
    headers = [str(header).strip() for header in (reader.fieldnames or []) if header is not None]
    rows = [{str(key).strip(): str(value or "").strip() for key, value in row.items() if key is not None}
            for row in reader]
    if not headers or not rows:
        raise HTTPException(422, detail="ledgerCsvEmpty")
    return headers, rows


def _ledger_csv_preview(rows: list[dict[str, str]], mapping: dict[str, str], session: Session) -> list[dict[str, Any]]:
    required = {"date", "name", "type", "amount", "units", "price", "currency"}
    headers = set(rows[0]) if rows else set()
    if set(mapping) != required or set(mapping.values()) - headers or len(set(mapping.values())) != len(required):
        raise HTTPException(422, detail="ledgerCsvInvalidMapping")
    tipi = {"buy": "Buy", "acquisto": "Buy", "bought": "Buy",
            "sell": "Sell", "vendita": "Sell", "sold": "Sell"}
    preview: list[dict[str, Any]] = []
    seen: list[tuple[str, str, Decimal, Decimal | None, date, int]] = []
    for row_number, row in enumerate(rows, 2):
        try:
            occurred_on = parse_date(row[mapping["date"]])
            transaction_type = tipi.get(row[mapping["type"]].strip().lower())
            amount = parse_amount(row[mapping["amount"]])
            units = parse_amount(row[mapping["units"]])
            price = parse_amount(row[mapping["price"]])
            if not occurred_on or not row[mapping["name"]].strip() or not transaction_type or amount <= 0 or units == 0 or price <= 0:
                raise ValueError
            payload = InvestmentTxPayload(occurred_on=occurred_on, name=row[mapping["name"]].strip(),
                                          transaction_type=transaction_type, amount=amount, units=units,
                                          price=price, currency=row[mapping["currency"]].strip().upper() or "EUR")
            duplicate = operazione_gia_presente(session, payload)
            values = (payload.name, payload.transaction_type, _to_decimal(payload.amount, "amount"),
                      _to_decimal(payload.units, "units") if payload.units is not None else None,
                      date.fromisoformat(payload.occurred_on))
            internal = next((item for item in seen if item[:4] == values[:4]
                             and abs((item[4] - values[4]).days) <= 3), None)
            duplicate_of = (_investment_tx_to_dict(duplicate) if duplicate else
                            ({"id": None, "occurredOn": internal[4].isoformat(),
                              "name": payload.name, "sourceRow": internal[5]} if internal else None))
            preview.append({**payload.model_dump(), "row": row_number, "duplicate": duplicate_of is not None,
                            "duplicateOf": duplicate_of, "error": None})
            seen.append((*values, row_number))
        except (ValueError, ArithmeticError):
            preview.append({"row": row_number, "duplicate": False, "duplicateOf": None,
                            "error": "ledgerCsvInvalidRow", "raw": row})
    return preview


@app.post("/api/investments/ledger", status_code=201)
def create_investment_tx(payload: InvestmentTxPayload, force: bool = False,
                         session: Session = Depends(get_session)):
    """Crea un movimento di investimento. Aggiorna anche il detail se fee/notes presenti."""
    tx = InvestmentTransaction()
    _apply_investment_tx(tx, payload, session)
    if not force and (duplicate := operazione_gia_presente(session, payload)):
        raise HTTPException(status_code=409, detail=_duplicate_detail(duplicate))
    session.add(tx)
    try:
        session.flush()
        if payload.fee or payload.notes:
            detail = InvestmentTransactionDetail(
                transaction_id=tx.id,
                fee=_to_decimal(payload.fee, "fee", allow_negative=False),
                notes=payload.notes,
            )
            session.add(detail)
            session.flush()
        session.commit()
        session.refresh(tx)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto movimento investimento: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione movimento investimento: {exc}") from exc
    detail = session.scalar(select(InvestmentTransactionDetail).where(InvestmentTransactionDetail.transaction_id == tx.id))
    return _investment_tx_to_dict(tx, detail)


# --- Collegamento Transazione <-> Ledger investimenti --------------------------

class LinkedLedgerRow(BaseModel):
    """Una riga del ledger da creare insieme alla Transazione bank."""
    name: str
    transaction_type: str
    units: float
    price: float
    currency: str = "EUR"
    fee: float = 0
    notes: str | None = None


class CreateTransactionWithLedger(TransactionPayload):
    """Crea una Transazione + N righe del ledger collegate, in modo atomico."""
    linked_ledger: list[LinkedLedgerRow] | None = None


class CreateLedgerWithTransaction(InvestmentTxPayload):
    """Crea un'operazione del ledger + 1 Transazione bank collegata, in modo atomico."""
    linked_transaction: TransactionPayload | None = None


def _materialize_linked_ledger(session: Session, tx: Transaction, rows: list[LinkedLedgerRow]) -> list[InvestmentTransaction]:
    """Crea N InvestmentTransaction + relativi detail, collegate alla Transazione. Non fa commit."""
    created: list[InvestmentTransaction] = []
    for row in rows:
        if row.transaction_type not in VALID_INVESTMENT_TX_TYPES:
            raise HTTPException(status_code=422, detail=f"transaction_type ledger non valido: {row.transaction_type}")
        if not row.name or not row.name.strip():
            raise HTTPException(status_code=422, detail="name strumento obbligatorio per ogni riga ledger")
        itx = InvestmentTransaction(
            occurred_on=tx.occurred_on,
            name=row.name.strip(),
            transaction_type=row.transaction_type,
            amount=Decimal(str(row.units)) * Decimal(str(row.price)),
            units=Decimal(str(row.units)),
            price=Decimal(str(row.price)),
            currency=(row.currency or "EUR").strip() or "EUR",
        )
        session.add(itx)
        session.flush()
        if row.fee or row.notes:
            detail = InvestmentTransactionDetail(
                transaction_id=itx.id,
                fee=Decimal(str(row.fee or 0)),
                notes=row.notes,
            )
            session.add(detail)
            session.flush()
        created.append(itx)
    return created


@app.post("/api/transactions/with-ledger", status_code=201)
def create_transaction_with_linked_ledger(payload: CreateTransactionWithLedger, session: Session = Depends(get_session)):
    """Crea Transazione + righe del ledger collegate, in modo atomico.

    Solo un Investment si collega. Se linked_ledger e' vuoto si comporta come il
    POST /api/transactions.
    """
    tx = Transaction()
    _apply_transaction_payload(tx, payload, session)
    try:
        session.add(tx)
        session.flush()
        if payload.linked_ledger:
            solo_investimenti_si_collegano(tx)
            ledger_rows = _materialize_linked_ledger(session, tx, payload.linked_ledger)
            for itx in ledger_rows:
                session.add(TransactionLedgerLink(transaction_id=tx.id, ledger_id=itx.id))
                session.flush()
        session.commit()
        session.refresh(tx)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto dati movimento/ledger: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore salvataggio movimento+ledger: {exc}") from exc
    return _transaction_to_dict(tx, session)


def _materialize_linked_transaction(session: Session, itx: InvestmentTransaction, payload: TransactionPayload) -> Transaction:
    """Crea una Transazione bank collegata a un'InvestmentTransaction. Non fa commit."""
    tx = Transaction()
    _apply_transaction_payload(tx, payload, session)
    tx.occurred_on = itx.occurred_on
    tx.amount = itx.amount
    session.add(tx)
    session.flush()
    return tx


@app.post("/api/investments/ledger/with-transaction", status_code=201)
def create_ledger_with_linked_transaction(payload: CreateLedgerWithTransaction, force: bool = False,
                                          session: Session = Depends(get_session)):
    """Crea un'operazione del ledger + 1 Transazione bank collegata, in modo atomico."""
    itx = InvestmentTransaction()
    _apply_investment_tx(itx, payload, session)
    if not force and (duplicate := operazione_gia_presente(session, payload)):
        raise HTTPException(status_code=409, detail=_duplicate_detail(duplicate))
    try:
        session.add(itx)
        session.flush()
        if payload.fee or payload.notes:
            detail = InvestmentTransactionDetail(
                transaction_id=itx.id,
                fee=_to_decimal(payload.fee, "fee", allow_negative=False),
                notes=payload.notes,
            )
            session.add(detail)
            session.flush()
        if payload.linked_transaction:
            tx = _materialize_linked_transaction(session, itx, payload.linked_transaction)
            session.add(TransactionLedgerLink(transaction_id=tx.id, ledger_id=itx.id))
            session.flush()
        session.commit()
        session.refresh(itx)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto ledger/transazione: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore salvataggio ledger+transazione: {exc}") from exc
    detail = session.scalar(select(InvestmentTransactionDetail).where(InvestmentTransactionDetail.transaction_id == itx.id))
    return _investment_tx_to_dict(itx, detail)


@app.post("/api/transactions/{tx_id}/ledger-links", status_code=201)
def link_existing_ledger_to_transaction(tx_id: int, payload: dict, session: Session = Depends(get_session)):
    """Collega una riga ledger esistente a una Transazione esistente (utile per agganciare transazioni passate)."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    ledger_id = payload.get("ledger_id")
    if not isinstance(ledger_id, int):
        raise HTTPException(status_code=422, detail="ledger_id obbligatorio (intero)")
    itx = session.get(InvestmentTransaction, ledger_id)
    if itx is None:
        raise HTTPException(status_code=404, detail=f"Operazione ledger {ledger_id} non trovata")
    solo_investimenti_si_collegano(tx)
    existing = session.scalar(
        select(TransactionLedgerLink).where(
            TransactionLedgerLink.transaction_id == tx_id,
            TransactionLedgerLink.ledger_id == ledger_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Link già esistente")
    link = TransactionLedgerLink(transaction_id=tx_id, ledger_id=ledger_id)
    session.add(link)
    try:
        session.flush()
        pretendi_quadratura(session, tx_id)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto creazione link: {exc.orig}") from exc
    return {"id": link.id, "transaction_id": tx_id, "ledger_id": ledger_id}


@app.delete("/api/transactions/{tx_id}/ledger-links/{link_id}")
def unlink_ledger_from_transaction(tx_id: int, link_id: int, session: Session = Depends(get_session)):
    """Scollega una riga ledger da una Transazione (NON cancella il ledger)."""
    link = session.get(TransactionLedgerLink, link_id)
    if link is None or link.transaction_id != tx_id:
        raise HTTPException(status_code=404, detail=f"Link {link_id} non trovato per movimento {tx_id}")
    session.delete(link)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore scollegamento: {exc}") from exc
    return {"deleted": True, "id": link_id}


@app.get("/api/transactions/{tx_id}/ledger-links")
def list_ledger_links_for_transaction(tx_id: int, session: Session = Depends(get_session)):
    """Lista le righe del ledger collegate a una Transazione."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    rows = session.execute(
        select(TransactionLedgerLink, InvestmentTransaction, InvestmentTransactionDetail)
        .join(InvestmentTransaction, TransactionLedgerLink.ledger_id == InvestmentTransaction.id)
        .outerjoin(InvestmentTransactionDetail, InvestmentTransactionDetail.transaction_id == InvestmentTransaction.id)
        .where(TransactionLedgerLink.transaction_id == tx_id)
        .order_by(InvestmentTransaction.occurred_on.desc(), InvestmentTransaction.id.desc())
    ).all()
    items = []
    for link, itx, detail in rows:
        items.append({
            "linkId": link.id,
            "id": itx.id,
            "occurredOn": itx.occurred_on.isoformat(),
            "name": itx.name,
            "transactionType": itx.transaction_type,
            "amount": num(itx.amount),
            "units": num(itx.units) if itx.units is not None else None,
            "price": num(itx.price) if itx.price is not None else None,
            "currency": itx.currency or "EUR",
            "fee": float(detail.fee) if detail else 0,
            "notes": detail.notes if detail else None,
        })
    return {"items": items, "balance": quadratura_gruppo(session, tx_id)}


@app.post("/api/investments/ledger/{ledger_id}/transaction-links", status_code=201)
def link_existing_transaction_to_ledger(ledger_id: int, payload: dict, session: Session = Depends(get_session)):
    """Specchio: collega una Transazione esistente a un record ledger esistente."""
    itx = session.get(InvestmentTransaction, ledger_id)
    if itx is None:
        raise HTTPException(status_code=404, detail=f"Operazione ledger {ledger_id} non trovata")
    tx_id = payload.get("transaction_id")
    if not isinstance(tx_id, int):
        raise HTTPException(status_code=422, detail="transaction_id obbligatorio (intero)")
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento {tx_id} non trovato")
    solo_investimenti_si_collegano(tx)
    existing = session.scalar(
        select(TransactionLedgerLink).where(
            TransactionLedgerLink.transaction_id == tx_id,
            TransactionLedgerLink.ledger_id == ledger_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Link già esistente")
    link = TransactionLedgerLink(transaction_id=tx_id, ledger_id=ledger_id)
    session.add(link)
    try:
        session.flush()
        pretendi_quadratura(session, tx_id)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto creazione link: {exc.orig}") from exc
    return {"id": link.id, "transaction_id": tx_id, "ledger_id": ledger_id}


@app.get("/api/investments/ledger/{ledger_id}/transaction-links")
def list_transaction_links_for_ledger(ledger_id: int, session: Session = Depends(get_session)):
    """Specchio: lista le Transazioni collegate a una riga ledger."""
    itx = session.get(InvestmentTransaction, ledger_id)
    if itx is None:
        raise HTTPException(status_code=404, detail=f"Operazione ledger {ledger_id} non trovata")
    rows = session.execute(
        select(TransactionLedgerLink, Transaction)
        .join(Transaction, TransactionLedgerLink.transaction_id == Transaction.id)
        .where(TransactionLedgerLink.ledger_id == ledger_id)
        .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())
    ).all()
    items = []
    for link, tx in rows:
        items.append({"linkId": link.id, "id": tx.id, "category": tx.category, "amount": num(tx.amount), "occurredOn": tx.occurred_on.isoformat()})
    return {"items": items}


@app.post("/api/investments/ledger/batch", status_code=201)
def create_investment_tx_batch(payloads: list[InvestmentTxPayload], session: Session = Depends(get_session)):
    """Import massivo: salva le righe nuove e restituisce i duplicati saltati."""
    if not payloads:
        raise HTTPException(status_code=422, detail="Lista movimenti vuota")
    created: list[dict] = []
    skipped: list[dict] = []
    try:
        for payload in payloads:
            tx = InvestmentTransaction()
            _apply_investment_tx(tx, payload, session)
            if duplicate := operazione_gia_presente(session, payload):
                skipped.append({"name": payload.name.strip(), "occurredOn": payload.occurred_on,
                                "duplicateOf": _investment_tx_to_dict(duplicate)})
                continue
            session.add(tx)
            session.flush()
            if payload.fee or payload.notes:
                detail = InvestmentTransactionDetail(
                    transaction_id=tx.id,
                    fee=_to_decimal(payload.fee, "fee", allow_negative=False),
                    notes=payload.notes,
                )
                session.add(detail)
                session.flush()
            created.append({"id": tx.id, "name": tx.name, "transactionType": tx.transaction_type})
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore import batch investimenti: {exc}") from exc
    return {"success": True, "created": created, "skipped": skipped, "count": len(created)}


@app.post("/api/investments/ledger/import/csv/columns")
async def investment_csv_columns(file: UploadFile = File(...)):
    headers, rows = _ledger_csv_rows(await read_upload(file, ".csv"))
    return {"headers": headers, "sample": rows[:5], "count": len(rows)}


@app.post("/api/investments/ledger/import/csv/preview")
async def investment_csv_preview(file: UploadFile = File(...), mapping: str = Form(...),
                                 session: Session = Depends(get_session)):
    try:
        parsed_mapping = json.loads(mapping)
    except (TypeError, json.JSONDecodeError) as error:
        raise HTTPException(422, detail="ledgerCsvInvalidMapping") from error
    if not isinstance(parsed_mapping, dict):
        raise HTTPException(422, detail="ledgerCsvInvalidMapping")
    _, rows = _ledger_csv_rows(await read_upload(file, ".csv"))
    items = _ledger_csv_preview(rows, parsed_mapping, session)
    return {"items": items, "count": len(items),
            "duplicates": sum(bool(item["duplicate"]) for item in items),
            "errors": sum(bool(item["error"]) for item in items)}


@app.get("/api/investments/ledger/{tx_id}")
def get_investment_tx(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(InvestmentTransaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento investimento {tx_id} non trovato")
    detail = session.scalar(select(InvestmentTransactionDetail).where(InvestmentTransactionDetail.transaction_id == tx_id))
    return _investment_tx_to_dict(tx, detail)


@app.patch("/api/investments/ledger/{tx_id}")
def update_investment_tx(tx_id: int, payload: InvestmentTxPayload, session: Session = Depends(get_session)):
    tx = session.get(InvestmentTransaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento investimento {tx_id} non trovato")
    _apply_investment_tx(tx, payload, session)
    detail = session.scalar(select(InvestmentTransactionDetail).where(InvestmentTransactionDetail.transaction_id == tx_id))
    if payload.fee or payload.notes:
        if detail is None:
            detail = InvestmentTransactionDetail(
                transaction_id=tx.id,
                fee=_to_decimal(payload.fee, "fee", allow_negative=False),
                notes=payload.notes,
            )
            session.add(detail)
        else:
            detail.fee = _to_decimal(payload.fee, "fee", allow_negative=False)
            detail.notes = payload.notes
    elif detail is not None:
        # se l'utente svuota fee/notes, manteniamo la riga con fee=0
        detail.fee = Decimal("0")
        detail.notes = None
    try:
        session.commit()
        session.refresh(tx)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento movimento investimento: {exc}") from exc
    return _investment_tx_to_dict(tx, detail)


@app.delete("/api/investments/ledger/{tx_id}")
def delete_investment_tx(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(InvestmentTransaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Movimento investimento {tx_id} non trovato")
    detail = session.scalar(select(InvestmentTransactionDetail).where(InvestmentTransactionDetail.transaction_id == tx_id))
    if detail is not None:
        session.delete(detail)
        session.flush()
    session.delete(tx)
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione movimento investimento: {exc}") from exc
    return {"success": True, "deleted_id": tx_id}


# --- Investment instruments --------------------------------------------------


class InstrumentPayload(BaseModel):
    name: str
    provider_symbol: str | None = None
    isin: str | None = None
    asset_class: str = "Da classificare"
    area: str = "Globale"
    sector: str = "Altro"
    currency: str = "EUR"
    target_weight: float | None = None


def _apply_instrument(inst: InvestmentInstrument, payload: InstrumentPayload) -> None:
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="name strumento obbligatorio")
    inst.name = payload.name.strip()
    inst.provider_symbol = (payload.provider_symbol or None) or None
    inst.isin = (payload.isin or None) or None
    inst.asset_class = (payload.asset_class or "Da classificare").strip()
    inst.area = (payload.area or "Globale").strip()
    inst.sector = (payload.sector or "Altro").strip()
    inst.currency = (payload.currency or "EUR").strip()
    if payload.target_weight is not None:
        inst.target_weight = _to_decimal(payload.target_weight, "target_weight", allow_negative=False)
    else:
        inst.target_weight = None



# Tipo Yahoo -> classe usata nell'app. Serve solo a dare un valore sensato agli
# strumenti ancora non classificati: una classificazione gia' scelta a mano non
# viene mai sovrascritta.
YAHOO_ASSET_CLASS = {
    "ETF": "ETF",
    "EQUITY": "Azioni",
    "MUTUALFUND": "Fondi comuni",
    "CRYPTOCURRENCY": "Cripto",
    "CURRENCY": "Valute",
    "INDEX": "Indici",
    "FUTURE": "Materie prime",
}
PLACEHOLDER_CLASSES = {"", "da classificare", "#value!"}
PLACEHOLDER_AREAS = {"", "globale", "#value!"}


def _autofill_from_source(inst) -> None:
    """Completa valuta, classe e borsa leggendo l'anagrafica dal ticker.

    La valuta viene sempre allineata alla fonte (serve alla conversione in
    euro); classe e area solo se sono ancora segnaposto, per non cancellare la
    classificazione decisa dall'utente.
    """
    if not inst.provider_symbol:
        return
    try:
        profile = fetch_instrument_profile(inst.provider_symbol)
    except MarketDataError:
        return
    if profile.currency:
        inst.currency = profile.currency.strip().upper()
    if (inst.asset_class or "").strip().lower() in PLACEHOLDER_CLASSES and profile.instrument_type:
        inst.asset_class = YAHOO_ASSET_CLASS.get(profile.instrument_type.upper(), profile.instrument_type.title())
    if (inst.area or "").strip().lower() in PLACEHOLDER_AREAS and profile.exchange:
        inst.area = profile.exchange.strip()


# ---------------------------------------------------------------------------
# CRUD Budget: i piani nascevano solo dall'import Excel, l'interfaccia li
# modificava a vuoto. Sono la base perche' l'app pianifichi da sola.
# ---------------------------------------------------------------------------

VALID_BUDGET_TYPES = {"Expenses", "Income", "Savings"}


def _budget_period(year: int, month: int) -> date:
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail=f"Mese non valido: {month}")
    return date(year, month, 1)


def _budget_to_dict(plan: BudgetPlan) -> dict:
    return {
        "id": plan.id,
        "period": plan.period.isoformat(),
        "budgetType": plan.budget_type,
        "category": plan.category,
        "categoryGroup": plan.category_group,
        "amount": float(plan.amount or 0),
    }


class BudgetCreatePayload(BaseModel):
    year: int
    month: int
    budget_type: str = "Expenses"
    category: str
    amount: float
    category_group: str | None = None


class BudgetUpdatePayload(BaseModel):
    category: str | None = None
    amount: float | None = None
    category_group: str | None = None


class BudgetCopyPayload(BaseModel):
    source_year: int
    source_month: int
    target_year: int
    target_month: int
    budget_type: str = "Expenses"


class BudgetBulkPayload(BaseModel):
    year: int
    budget_type: str = "Expenses"
    category: str
    months: list[int]
    amount: float
    category_group: str | None = None


def _rifiuta_savings(budget_type: str) -> None:
    """Il budget dei risparmi si deduce da entrate e spese: non si scrive.

    Lasciar passare una scrittura vorrebbe dire tenere per qualche secondo un
    numero che il primo ricalcolo cancella, cioe' mentire all'utente sul fatto
    che la sua modifica sia servita a qualcosa.
    """
    if budget_type == "Savings":
        raise HTTPException(status_code=422, detail="savings_derived")


def _resolve_category(category: str | None, transaction_type: str, regola: str | None = None) -> str:
    """La categoria da scrivere su un movimento appena creato.

    La regola e' semplice: se c'e' una categoria vera - scelta dall'utente, o
    letta dall'estratto conto - si usa quella; se manca, si guarda cosa ha
    deciso il motore delle regole (``regola``), che arriva solo dall'anteprima
    dell'import; se non ha deciso niente, il movimento finisce nella pila "Da
    categorizzare", dove andra' sistemato a mano.

    La categoria della riga vince sulla regola: quando il file ne porta una
    e' piu' specifica di un pattern, e comunque la regola si applica solo
    nell'anteprima, dove la si vede e la si puo' correggere.

    I trasferimenti non hanno categoria: il segnaposto "_" lo segnala, e
    nessuna regola li tocca.
    """
    if transaction_type in SPOSTAMENTI:
        return "_"
    pulita = (category or "").strip()
    if pulita and pulita.casefold() != PENDING_CATEGORY.casefold():
        return pulita
    return regola or PENDING_CATEGORY


def _check_budget_type(budget_type: str) -> str:
    if budget_type not in VALID_BUDGET_TYPES:
        raise HTTPException(status_code=422, detail=f"budget_type non valido: {budget_type}")
    return budget_type


@app.post("/api/budgets", status_code=201)
def create_budget(payload: BudgetCreatePayload, session: Session = Depends(get_session)):
    """Crea la voce di budget di una categoria per un periodo."""
    budget_type = _check_budget_type(payload.budget_type)
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=422, detail="category obbligatoria")
    # Il risparmio effettivo e' un numero solo - quello che resta delle entrate -
    # e non si puo' spalmare fra piu' categorie senza inventarselo. Una seconda
    # categoria mostrerebbe per sempre "pianificato X, effettivo zero", che e'
    # peggio di un errore: e' un numero sbagliato che non si lamenta.
    _rifiuta_savings(budget_type)
    period = _budget_period(payload.year, payload.month)
    existing = session.scalar(select(BudgetPlan).where(
        BudgetPlan.period == period, BudgetPlan.budget_type == budget_type, BudgetPlan.category == category))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Budget gia' presente per '{category}' in questo periodo")
    plan = BudgetPlan(period=period, budget_type=budget_type, category=category,
                      category_group=(payload.category_group or None),
                      amount=_to_decimal(payload.amount, "amount", allow_negative=False))
    session.add(plan)
    try:
        session.flush()
        sync_savings_plan(session, period)
        session.commit()
        session.refresh(plan)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto budget: {exc.orig}") from exc
    return _budget_to_dict(plan)


@app.patch("/api/budgets/{budget_id}")
def update_budget(budget_id: int, payload: BudgetUpdatePayload, session: Session = Depends(get_session)):
    """Aggiorna importo o categoria di una voce di budget."""
    plan = session.get(BudgetPlan, budget_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Budget {budget_id} non trovato")
    _rifiuta_savings(plan.budget_type)
    if payload.category is not None:
        category = payload.category.strip()
        if not category:
            raise HTTPException(status_code=422, detail="category non puo' essere vuota")
        plan.category = category
    if payload.amount is not None:
        plan.amount = _to_decimal(payload.amount, "amount", allow_negative=False)
    if payload.category_group is not None:
        plan.category_group = payload.category_group or None
    try:
        session.flush()
        sync_savings_plan(session, plan.period)
        session.commit()
        session.refresh(plan)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Esiste gia' un budget per questa categoria nel periodo") from exc
    return _budget_to_dict(plan)


@app.delete("/api/budgets/{budget_id}")
def delete_budget(budget_id: int, session: Session = Depends(get_session)):
    """Elimina una voce di budget."""
    plan = session.get(BudgetPlan, budget_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Budget {budget_id} non trovato")
    _rifiuta_savings(plan.budget_type)
    period = plan.period
    session.delete(plan)
    session.flush()
    sync_savings_plan(session, period)
    session.commit()
    return {"success": True, "deleted_id": budget_id}


class CategoryGroupPayload(BaseModel):
    category: str
    category_group: str | None = None


@app.put("/api/category-groups")
def set_category_group(payload: CategoryGroupPayload, session: Session = Depends(get_session)):
    """Classifica una categoria come bisogno o piacere, per tutti i periodi.

    La classificazione vive sulle righe di budget perche' e' li' che c'era gia'
    la colonna, ma non appartiene al mese: cambiarla in settembre e lasciare
    agosto com'era darebbe due risposte diverse alla stessa domanda. Si scrive
    quindi su tutte le righe di quella categoria in una volta sola.
    """
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=422, detail="category obbligatoria")
    group = (payload.category_group or "").strip() or None
    if group is not None and group not in CATEGORY_GROUPS:
        raise HTTPException(status_code=422, detail=f"Gruppo non valido: {group}")
    rows = session.scalars(select(BudgetPlan).where(
        func.lower(BudgetPlan.category) == category.lower())).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nessun budget per la categoria '{category}'")
    for row in rows:
        row.category_group = group
    session.commit()
    return {"success": True, "category": category, "categoryGroup": group, "updated": len(rows)}


@app.post("/api/budgets/copy")
def copy_budget(payload: BudgetCopyPayload, session: Session = Depends(get_session)):
    """Ricopia il budget di un periodo su un altro, sostituendo quello esistente.

    L'interfaccia lo presenta come "copia dal mese/anno precedente" e avvisa
    che il periodo di destinazione viene sostituito: qui si fa esattamente
    quello, in una sola transazione.
    """
    budget_type = _check_budget_type(payload.budget_type)
    _rifiuta_savings(budget_type)
    source = _budget_period(payload.source_year, payload.source_month)
    target = _budget_period(payload.target_year, payload.target_month)
    if source == target:
        raise HTTPException(status_code=422, detail="Periodo di origine e destinazione coincidono")
    rows = session.scalars(select(BudgetPlan).where(
        BudgetPlan.period == source, BudgetPlan.budget_type == budget_type)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Nessun budget da copiare nel periodo di origine")
    for existing in session.scalars(select(BudgetPlan).where(
            BudgetPlan.period == target, BudgetPlan.budget_type == budget_type)).all():
        session.delete(existing)
    session.flush()
    for row in rows:
        session.add(BudgetPlan(period=target, budget_type=budget_type, category=row.category,
                               category_group=row.category_group, amount=row.amount))
    session.flush()
    sync_savings_plan(session, target)
    session.commit()
    return {"success": True, "copied": len(rows), "targetPeriod": target.isoformat()}


@app.post("/api/budget-bulk")
def bulk_budget(payload: BudgetBulkPayload, session: Session = Depends(get_session)):
    """Imposta lo stesso importo su piu' mesi dello stesso anno per una categoria."""
    budget_type = _check_budget_type(payload.budget_type)
    _rifiuta_savings(budget_type)
    category = payload.category.strip()
    if not category:
        raise HTTPException(status_code=422, detail="category obbligatoria")
    months = sorted({int(month) for month in payload.months})
    if not months:
        raise HTTPException(status_code=422, detail="Nessun mese selezionato")
    amount = _to_decimal(payload.amount, "amount", allow_negative=False)
    created = updated = 0
    for month in months:
        period = _budget_period(payload.year, month)
        plan = session.scalar(select(BudgetPlan).where(
            BudgetPlan.period == period, BudgetPlan.budget_type == budget_type, BudgetPlan.category == category))
        if plan is None:
            session.add(BudgetPlan(period=period, budget_type=budget_type, category=category,
                                   category_group=(payload.category_group or None), amount=amount))
            created += 1
        else:
            plan.amount = amount
            if payload.category_group is not None:
                plan.category_group = payload.category_group or None
            updated += 1
    session.flush()
    for month in months:
        sync_savings_plan(session, _budget_period(payload.year, month))
    session.commit()
    return {"success": True, "created": created, "updated": updated, "months": months}


@app.post("/api/investments/instruments", status_code=201)
def create_instrument(payload: InstrumentPayload, session: Session = Depends(get_session)):
    existing = session.scalar(select(InvestmentInstrument).where(InvestmentInstrument.name == payload.name.strip()))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Strumento '{payload.name}' già esistente")
    inst = InvestmentInstrument()
    _apply_instrument(inst, payload)
    _autofill_from_source(inst)
    session.add(inst)
    try:
        session.flush()
        session.commit()
        session.refresh(inst)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Conflitto strumento: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione strumento: {exc}") from exc
    return _instrument_to_dict(inst)


@app.patch("/api/investments/instruments/{inst_id}")
def update_instrument(inst_id: int, payload: InstrumentPayload, session: Session = Depends(get_session)):
    inst = session.get(InvestmentInstrument, inst_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Strumento {inst_id} non trovato")
    if payload.name.strip() != inst.name:
        existing = session.scalar(select(InvestmentInstrument).where(InvestmentInstrument.name == payload.name.strip(), InvestmentInstrument.id != inst_id))
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"Strumento '{payload.name}' già esistente")
    _apply_instrument(inst, payload)
    try:
        session.commit()
        session.refresh(inst)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento strumento: {exc}") from exc
    return _instrument_to_dict(inst)


class InstrumentClassificationPayload(BaseModel):
    provider_symbol: str | None = None
    asset_class: str | None = None
    area: str | None = None
    sector: str | None = None
    currency: str | None = None
    target_weight: float | None = None


@app.patch("/api/investments/instruments/{inst_id}/classification")
def update_instrument_classification(inst_id: int, payload: InstrumentClassificationPayload, session: Session = Depends(get_session)):
    """Aggiorna solo i campi di classifica (provider_symbol, asset_class, area, sector, currency, target_weight)."""
    inst = session.get(InvestmentInstrument, inst_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Strumento {inst_id} non trovato")
    symbol_changed = False
    if payload.provider_symbol is not None:
        new_symbol = payload.provider_symbol.strip() or None
        symbol_changed = new_symbol != inst.provider_symbol
        inst.provider_symbol = new_symbol
    if payload.asset_class is not None:
        inst.asset_class = payload.asset_class.strip()
    if payload.area is not None:
        inst.area = payload.area.strip()
    if payload.sector is not None:
        inst.sector = payload.sector.strip()
    if payload.currency is not None:
        inst.currency = payload.currency.strip()
    if payload.target_weight is not None:
        inst.target_weight = _to_decimal(payload.target_weight, "target_weight", allow_negative=False)
    # La valuta la dice la fonte prezzi, non l'utente: quando si assegna un
    # ticker nuovo la si legge dalla quotazione, cosi' la conversione in euro
    # usa sempre la valuta reale dello strumento.
    if symbol_changed:
        _autofill_from_source(inst)
    try:
        session.commit()
        session.refresh(inst)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore classificazione strumento: {exc}") from exc
    return _instrument_to_dict(inst)


@app.delete("/api/investments/instruments/{inst_id}")
def delete_instrument(inst_id: int, session: Session = Depends(get_session)):
    inst = session.get(InvestmentInstrument, inst_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Strumento {inst_id} non trovato")
    session.delete(inst)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Impossibile eliminare: {exc.orig}") from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore eliminazione strumento: {exc}") from exc
    return {"success": True, "deleted_id": inst_id}


# --- Market-data cache ------------------------------------------------------


class MarketDataRefreshItem(BaseModel):
    symbol: str
    observed_on: str | None = None  # default = oggi
    force_refresh: bool = False


class MarketDataRefreshPayload(BaseModel):
    items: list[MarketDataRefreshItem]
    fail_on_error: bool = False  # se True, l'intero batch fallisce al primo errore


@app.post("/api/market-data/refresh")
def refresh_market_prices(payload: MarketDataRefreshPayload, session: Session = Depends(get_session)):
    """Scarica e mette in cache le quotazioni richieste.

    Se il simbolo+dato è già in cache, viene servito da DB (regola 5: niente
    chiamate di rete non necessarie). Yahoo viene contattato solo per le date
    non ancora osservate, o se `force_refresh=true`.
    """
    if not payload.items:
        raise HTTPException(status_code=422, detail="Lista simboli vuota")
    results: list[dict] = []
    errors: list[dict] = []
    today = datetime.now(timezone.utc).date()
    try:
        for item in payload.items:
            try:
                target = _parse_iso_date(item.observed_on, "observed_on") if item.observed_on else today
                price = get_or_fetch_price(
                    session, item.symbol, target, force_refresh=item.force_refresh,
                )
                results.append({
                    "symbol": price.symbol,
                    "observedOn": price.observed_on.isoformat(),
                    "price": float(price.price),
                    "currency": price.currency,
                    "cached": price.fetched_at is not None,
                })
            except MarketDataError as exc:
                msg = str(exc)
                errors.append({"symbol": item.symbol, "error": msg})
                if payload.fail_on_error:
                    raise HTTPException(status_code=502, detail=f"Errore Yahoo per {item.symbol}: {msg}") from exc
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Errore refresh market data: {exc}") from exc
    return {
        "success": len(errors) == 0,
        "fetched": results,
        "errors": errors,
    }


@app.get("/api/investments/instruments")
def list_instruments(session: Session = Depends(get_session)):
    """Elenco strumenti con ticker e classificazione, per la schermata di configurazione."""
    rows = session.scalars(select(InvestmentInstrument).order_by(InvestmentInstrument.name)).all()
    return {"items": [_instrument_to_dict(row) for row in rows]}


@app.get("/api/market-data/search")
def search_market_symbols(q: str):
    """Cerca il ticker a partire dal nome comune dello strumento."""
    try:
        matches = search_yahoo_symbols(q)
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [
        {"symbol": m.symbol, "name": m.name, "exchange": m.exchange, "type": m.quote_type}
        for m in matches
    ]}


@app.get("/api/market-data/verify")
def verify_market_symbol(symbol: str):
    """Controlla un ticker prima di salvarlo: ritorna prezzo, valuta e data.

    Serve alla schermata di configurazione per non salvare simboli sbagliati:
    l'errore si vede subito invece di scoprirlo al prossimo aggiornamento.
    """
    try:
        quote = fetch_yahoo_quote(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "symbol": quote.symbol, "price": quote.price,
        "currency": quote.currency, "observedOn": quote.observed_on.isoformat(),
    }


def _store_history(session: Session, symbol: str, points) -> int:
    """Scrive le chiusure storiche in cache, senza duplicare quelle gia' note."""
    existing = {
        row.observed_on
        for row in session.scalars(select(MarketPrice).where(MarketPrice.symbol == symbol)).all()
    }
    added = 0
    for observed_on, close, currency in points:
        if observed_on in existing:
            continue
        session.add(MarketPrice(symbol=symbol, observed_on=observed_on, price=Decimal(str(close)),
                                currency=currency, provider="yahoo"))
        existing.add(observed_on)
        added += 1
    return added


@app.post("/api/investments/backfill-history")
def backfill_price_history(years: int = 10, session: Session = Depends(get_session)):
    """Scarica lo storico mensile dei prezzi per i ticker configurati.

    Popola market_prices con le chiusure passate (e i cambi verso euro), che
    servono a ricostruire il valore del portafoglio mese per mese senza
    dipendere da uno storico calcolato altrove.
    """
    symbols: dict[str, str] = {}
    for row in session.scalars(select(InvestmentInstrument)).all():
        symbol = (row.provider_symbol or "").strip().upper()
        if symbol:
            symbols[symbol] = row.name

    stored, errors = [], []
    currencies: set[str] = set()
    for index, symbol in enumerate(sorted(symbols)):
        # Una pausa fra una richiesta e l'altra: un backfill scarica decine di
        # serie da dieci anni una dopo l'altra, e senza respiro la fonte
        # risponde 429 a tutto l'indirizzo, prezzi compresi.
        if index:
            time.sleep(SOURCE_REQUEST_PAUSE_SECONDS)
        try:
            points = fetch_price_history(symbol, years=years)
        except MarketDataError as exc:
            errors.append({"symbol": symbol, "code": getattr(exc, "code", "unexpected")})
            continue
        currency = next((c for _, _, c in points if c), None)
        if currency and currency.upper() != "EUR":
            currencies.add(currency.upper())
        stored.append({"symbol": symbol, "points": _store_history(session, symbol, points)})

    # Oltre alle valute delle quotazioni servono quelle in cui si legge il
    # patrimonio: senza il loro storico la conversione mese per mese non si fa.
    for currency in sorted(currencies | set(display_currencies(session))):
        last_error = "unexpected"
        for fx_symbol, _ in fx_symbols(currency):
            time.sleep(SOURCE_REQUEST_PAUSE_SECONDS)
            try:
                points = fetch_price_history(fx_symbol, years=years)
            except MarketDataError as exc:
                last_error = getattr(exc, "code", "unexpected")
                continue
            stored.append({"symbol": fx_symbol, "points": _store_history(session, fx_symbol, points)})
            break
        else:
            errors.append({"symbol": f"cambio {currency}", "code": last_error})

    session.commit()
    return {"success": not errors, "stored": stored, "errors": errors}


@app.post("/api/investments/refresh-profiles")
def refresh_instrument_profiles(session: Session = Depends(get_session)):
    """Scarica la composizione (settori, titoli) degli strumenti con ticker.

    Ogni esito viene registrato: se la fonte cambia meccanismo l'interfaccia
    deve poterlo dire, invece di mostrare un'allocazione vecchia come attuale.
    """
    # Piu' strumenti possono puntare allo stesso ticker: si scarica una volta sola.
    by_symbol: dict[str, list[str]] = defaultdict(list)
    for row in session.scalars(select(InvestmentInstrument)).all():
        symbol = (row.provider_symbol or "").strip().upper()
        if symbol:
            by_symbol[symbol].append(row.name)

    updated, errors = [], []
    now = datetime.now(timezone.utc)
    for index, (symbol, names) in enumerate(sorted(by_symbol.items())):
        if index:
            time.sleep(SOURCE_REQUEST_PAUSE_SECONDS)
        cached = session.scalar(select(InstrumentProfile).where(InstrumentProfile.symbol == symbol))
        if cached is None:
            cached = InstrumentProfile(symbol=symbol)
            session.add(cached)
            session.flush()
        try:
            profile = fetch_profile(symbol)
        except YahooRateLimited as exc:
            # Insistere peggiora il blocco: si interrompe e lo si dice una volta sola.
            cached.last_error = exc.code
            errors.append({"instruments": names, "symbol": symbol, "code": exc.code,
                           "retryMinutes": exc.retry_minutes, "detail": str(exc)})
            session.commit()
            return {"success": False, "updated": updated, "errors": errors, "blocked": True}
        except YahooProfileError as exc:
            cached.last_error = exc.code
            errors.append({"instruments": names, "symbol": symbol, "code": exc.code,
                           "retryMinutes": None, "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - fonte non contrattualizzata
            cached.last_error = "unexpected"
            errors.append({"instruments": names, "symbol": symbol, "code": "unexpected",
                           "retryMinutes": None, "detail": str(exc)})
            continue
        cached.payload = json.dumps(profile)
        cached.fetched_at = now
        cached.last_error = None
        updated.append({"instruments": names, "symbol": symbol, "sectors": len(profile["sectors"])})
    session.commit()
    return {"success": not errors, "updated": updated, "errors": errors, "blocked": False}


@app.post("/api/investments/refresh-quotes")
def refresh_instrument_quotes(force: bool = False, session: Session = Depends(get_session)):
    """Aggiorna le quotazioni di tutti gli strumenti con un ticker configurato.

    Scarica anche i cambi verso euro per le valute diverse, altrimenti i prezzi
    esteri resterebbero inutilizzabili e le posizioni continuerebbero a valere
    il prezzo dell'ultima operazione.
    """
    instruments = [
        row for row in session.scalars(select(InvestmentInstrument)).all()
        if (row.provider_symbol or "").strip()
    ]
    if not instruments:
        return {"success": True, "updated": [], "errors": [], "message": "Nessuno strumento con ticker configurato"}

    today = datetime.now(timezone.utc).date()
    updated: list[dict] = []
    errors: list[dict] = []
    currencies: set[str] = set()

    for instrument in instruments:
        symbol = instrument.provider_symbol.strip()
        try:
            price = get_or_fetch_price(session, symbol, today, force_refresh=force)
            currency = (price.currency or instrument.currency or "EUR").strip().upper()
            if currency != "EUR":
                currencies.add(currency)
            updated.append({
                "instrument": instrument.name, "symbol": price.symbol,
                "price": float(price.price), "currency": currency,
                "observedOn": price.observed_on.isoformat(),
            })
        except MarketDataError as exc:
            errors.append({"instrument": instrument.name, "symbol": symbol,
                           "code": getattr(exc, "code", "unexpected"), "detail": str(exc)})

    for currency in sorted(currencies | set(display_currencies(session))):
        last_error = None
        for fx_symbol, _ in fx_symbols(currency):
            try:
                get_or_fetch_price(session, fx_symbol, today, force_refresh=force)
                break
            except MarketDataError as exc:
                last_error = (fx_symbol, getattr(exc, "code", "unexpected"), str(exc))
        else:
            errors.append({"instrument": f"cambio {currency}", "symbol": last_error[0],
                           "code": last_error[1], "detail": last_error[2]})

    session.commit()
    return {"success": not errors, "updated": updated, "errors": errors}


@app.get("/api/market-data/cache")
def get_market_data_cache(session: Session = Depends(get_session)):
    """Elenco simboli presenti nella cache locale."""
    return {"items": list_cached_symbols(session)}


class MarketDataCacheQuery(BaseModel):
    symbol: str
    observed_on: str | None = None
    from_date: str | None = None
    to_date: str | None = None


@app.post("/api/market-data/cache/query")
def query_market_data_cache(payload: MarketDataCacheQuery, session: Session = Depends(get_session)):
    """Lettura dalla sola cache (nessuna chiamata di rete)."""
    if not payload.symbol or not payload.symbol.strip():
        raise HTTPException(status_code=422, detail="symbol obbligatorio")
    symbol = payload.symbol.strip().upper()
    stmt = select(MarketPrice).where(MarketPrice.symbol == symbol)
    if payload.observed_on:
        stmt = stmt.where(MarketPrice.observed_on == _parse_iso_date(payload.observed_on, "observed_on"))
    else:
        if payload.from_date:
            stmt = stmt.where(MarketPrice.observed_on >= _parse_iso_date(payload.from_date, "from_date"))
        if payload.to_date:
            stmt = stmt.where(MarketPrice.observed_on <= _parse_iso_date(payload.to_date, "to_date"))
    stmt = stmt.order_by(MarketPrice.observed_on.desc())
    rows = session.scalars(stmt).all()
    return {
        "symbol": symbol,
        "count": len(rows),
        "items": [
            {
                "observedOn": row.observed_on.isoformat(),
                "price": float(row.price),
                "currency": row.currency,
                "fetchedAt": row.fetched_at.isoformat() if row.fetched_at else None,
            }
            for row in rows
        ],
    }


@app.post("/api/import/csv")
async def import_csv_statement(file: UploadFile = File(...), session: Session = Depends(get_session)):
    content = await read_upload(file, '.csv')
    try:
        for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
            try:
                text_content = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        rows = await asyncio.to_thread(CSVStatementParser.extract_transactions_from_csv, text_content)
        return statement_preview(rows, session)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Parsing CSV fallito")
        raise HTTPException(422, detail="statementParseFailed") from error


# ===== RECURRING TRANSACTIONS =====

class RecurringTransactionCreate(BaseModel):
    description: str
    category: str
    categoryRaw: str = "Other"
    amount: float
    type: str = "expense"
    transactionType: str = "Expenses"
    accountName: str | None = None
    destinationName: str | None = None
    goal: str | None = None
    details: str = ""
    recurrence_rule: str  # RRULE format: FREQ=MONTHLY;BYMONTHDAY=1
    recurrence_end_date: str | None = None  # YYYY-MM-DD
    start_date: str  # YYYY-MM-DD


class RecurringTransactionResponse(BaseModel):
    id: int
    description: str
    category: str
    amount: float
    type: str
    transactionType: str
    recurrence_rule: str | None
    recurrence_end_date: str | None
    is_recurring_template: bool
    next_occurrence: str | None


def _recurring_template_to_response(template: Transaction, next_occ: date | None) -> "RecurringTransactionResponse":
    return RecurringTransactionResponse(
        id=template.id,
        description=template.details or template.category,
        category=template.category,
        amount=float(template.amount),
        type={"Income": "income", "Expenses": "expense", "Savings": "saving", "Transfers": "transfer"}.get(template.transaction_type, "expense"),
        transactionType=template.transaction_type,
        recurrence_rule=template.recurrence_rule,
        recurrence_end_date=template.recurrence_end_date.isoformat() if template.recurrence_end_date else None,
        is_recurring_template=template.is_recurring_template,
        next_occurrence=next_occ.isoformat() if next_occ else None,
    )


@app.post("/api/recurring-transactions", response_model=RecurringTransactionResponse)
async def create_recurring_transaction(
    data: RecurringTransactionCreate,
    session: Session = Depends(get_session)
):
    """Crea una transazione ricorrente (template)"""
    # Il risparmio non e' un tipo di movimento: un modello "Savings" genererebbe
    # movimenti che nessun conto, budget o report considera.
    if data.transactionType not in VALID_TRANSACTION_TYPES:
        raise HTTPException(422, detail="statementInvalidType")
    try:
        start_date = datetime.strptime(data.start_date, '%Y-%m-%d').date()
        template = Transaction(
            occurred_on=start_date,
            effective_on=compute_effective_on(session, start_date, data.transactionType),
            transaction_type=data.transactionType,
            category=data.category,
            amount=Decimal(str(abs(data.amount))),
            account_name=data.accountName,
            destination_name=data.destinationName,
            goal=data.goal,
            details=data.details or data.description,
            recurrence_rule=data.recurrence_rule,
            recurrence_end_date=datetime.strptime(data.recurrence_end_date, '%Y-%m-%d').date() if data.recurrence_end_date else None,
            is_recurring_template=True,
        )
        validate_movement(session, template)
        session.add(template)
        session.commit()
        session.refresh(template)

        next_occ = _calculate_next_occurrence(template.occurred_on, template.recurrence_rule, template.recurrence_end_date)
        return _recurring_template_to_response(template, next_occ)
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recurring-transactions", response_model=List[RecurringTransactionResponse])
async def list_recurring_transactions(
    session: Session = Depends(get_session)
):
    """Lista tutte le transazioni ricorrenti (template)"""
    templates = session.execute(
        select(Transaction).where(Transaction.is_recurring_template == True)
    ).scalars().all()

    result = []
    for t in templates:
        next_occ = _calculate_next_occurrence(t.occurred_on, t.recurrence_rule, t.recurrence_end_date)
        result.append(_recurring_template_to_response(t, next_occ))
    return result


@app.delete("/api/recurring-transactions/{template_id}")
async def delete_recurring_transaction(
    template_id: int,
    session: Session = Depends(get_session)
):
    """Elimina un template ricorrente e tutte le sue occorrenze generate"""
    template = session.get(Transaction, template_id)
    if not template or not template.is_recurring_template:
        raise HTTPException(status_code=404, detail="Template ricorrente non trovato")

    removed = 0
    for occ in session.execute(
        select(Transaction).where(Transaction.recurrence_parent_id == template_id)
    ).scalars().all():
        session.delete(occ)
        removed += 1

    session.delete(template)
    session.commit()
    return {"success": True, "message": "Template ricorrente eliminato", "occurrences_removed": removed}


@app.post("/api/recurring-transactions/generate")
async def generate_recurring_transactions(
    up_to_date: str = Query(..., description="Genera fino a questa data (YYYY-MM-DD)"),
    session: Session = Depends(get_session)
):
    """Genera le occorrenze delle transazioni ricorrenti fino a una data"""
    target_date = datetime.strptime(up_to_date, '%Y-%m-%d').date()

    templates = session.execute(
        select(Transaction).where(Transaction.is_recurring_template == True)
    ).scalars().all()

    generated = 0
    errors = []

    for template in templates:
        if not template.recurrence_rule:
            continue

        if template.recurrence_end_date and template.recurrence_end_date < target_date:
            continue

        last_occ = session.execute(
            select(Transaction)
            .where(Transaction.recurrence_parent_id == template.id)
            .order_by(Transaction.occurred_on.desc())
        ).scalars().first()

        start_from = last_occ.occurred_on if last_occ else template.occurred_on

        current = _calculate_next_occurrence(start_from, template.recurrence_rule, template.recurrence_end_date)
        while current and current <= target_date:
            existing = session.execute(
                select(Transaction).where(
                    Transaction.recurrence_parent_id == template.id,
                    Transaction.occurred_on == current
                )
            ).scalars().first()

            if not existing:
                try:
                    occ = Transaction(
                        occurred_on=current,
                        effective_on=compute_effective_on(session, current, template.transaction_type),
                        transaction_type=template.transaction_type,
                        category=template.category,
                        amount=template.amount,
                        account_name=template.account_name,
                        destination_name=template.destination_name,
                        goal=template.goal,
                        details=template.details,
                        recurrence_parent_id=template.id,
                        is_recurring_template=False,
                        counts_in_budget=template.counts_in_budget,
                    )
                    validate_movement(session, occ)
                    session.add(occ)
                    session.flush()
                    session.commit()
                    generated += 1
                except Exception as e:
                    session.rollback()
                    errors.append(f"{template.category} @ {current}: {str(e)}")

            current = _calculate_next_occurrence(current, template.recurrence_rule, template.recurrence_end_date)

    return {
        "success": True,
        "generated": generated,
        "errors": errors,
        "message": f"Generate {generated} transazioni ricorrenti"
    }


def _calculate_next_occurrence(
    current_date: date,
    rrule: str | None,
    end_date: date | None
) -> date | None:
    """Calcola la prossima occorrenza basata su RRULE semplificato"""
    if not rrule:
        return None

    # Parsing RRULE semplificato (supporta FREQ e BYMONTHDAY)
    freq = None
    bymonthday = None

    for part in rrule.split(';'):
        if part.startswith('FREQ='):
            freq = part.split('=')[1]
        elif part.startswith('BYMONTHDAY='):
            bymonthday = int(part.split('=')[1])

    if not freq:
        return None

    next_date = current_date

    if freq == 'DAILY':
        next_date = current_date + timedelta(days=1)
    elif freq == 'WEEKLY':
        next_date = current_date + timedelta(weeks=1)
    elif freq == 'MONTHLY':
        # Avanza di un mese mantenendo il giorno
        if bymonthday:
            # Usa il giorno specificato
            try:
                if current_date.month == 12:
                    next_date = date(current_date.year + 1, 1, bymonthday)
                else:
                    next_date = date(current_date.year, current_date.month + 1, bymonthday)
            except ValueError:
                # Giorno non valido per il mese (es. 31 febbraio)
                import calendar
                last_day = calendar.monthrange(current_date.year, current_date.month + 1 if current_date.month < 12 else 1)[1]
                day = min(bymonthday, last_day)
                if current_date.month == 12:
                    next_date = date(current_date.year + 1, 1, day)
                else:
                    next_date = date(current_date.year, current_date.month + 1, day)
        else:
            # Mantieni lo stesso giorno del mese
            try:
                if current_date.month == 12:
                    next_date = date(current_date.year + 1, 1, current_date.day)
                else:
                    next_date = date(current_date.year, current_date.month + 1, current_date.day)
            except ValueError:
                import calendar
                last_day = calendar.monthrange(current_date.year, current_date.month + 1 if current_date.month < 12 else 1)[1]
                day = min(current_date.day, last_day)
                if current_date.month == 12:
                    next_date = date(current_date.year + 1, 1, day)
                else:
                    next_date = date(current_date.year, current_date.month + 1, day)
    elif freq == 'YEARLY':
        try:
            next_date = date(current_date.year + 1, current_date.month, current_date.day)
        except ValueError:
            next_date = date(current_date.year + 1, current_date.month, 28)
    else:
        return None

    # Controlla data di fine
    if end_date and next_date > end_date:
        return None

    return next_date


# ===== BUDGET ALERTS =====

@app.get("/api/budget-alerts")
async def get_budget_alerts(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    warning_threshold: float = Query(1.2, ge=0, le=5),
    session: Session = Depends(get_session)
):
    """
    Calcola gli alert di budget per il mese specificato.
    Restituisce le categorie che hanno superato la soglia di warning (default 120%).
    """
    period_start = date(year, month, 1)
    if month == 12:
        period_end = date(year + 1, 1, 1)
    else:
        period_end = date(year, month + 1, 1)

    # Recupera i budget del mese
    budgets = session.execute(
        select(BudgetPlan).where(
            BudgetPlan.period == period_start,
            BudgetPlan.budget_type == "Expenses",
            BudgetPlan.amount > 0,
        )
    ).scalars().all()

    # Calcola spesa per categoria
    spending = session.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.effective_on >= period_start,
            Transaction.effective_on < period_end,
            BUDGET_MOVEMENT,
            Transaction.transaction_type == "Expenses",
        )
        .group_by(Transaction.category)
    ).all()
    # Aggregazione case-insensitive: Excel confronta le categorie ignorando
    # maiuscole/minuscole (operatore =), replichiamo lo stesso qui.
    spending_map: dict[str, float] = {}
    spending_label: dict[str, str] = {}
    for cat, total in spending:
        if cat:
            key = cat.strip().lower()
            spending_map[key] = spending_map.get(key, 0.0) + float(total)
            spending_label.setdefault(key, cat.strip())

    alerts = []
    for budget in budgets:
        spent = spending_map.get(budget.category.strip().lower(), 0.0)
        planned = float(budget.amount)
        if planned <= 0:
            continue
        usage = spent / planned
        if usage >= warning_threshold:
            alerts.append({
                "category": budget.category,
                "category_group": budget.category_group,
                "planned": planned,
                "spent": spent,
                "usage": round(usage * 100, 1),
                "remaining": round(planned - spent, 2),
            })

    # Categorie con spesa ma senza budget
    uncategorized_spending = []
    budget_categories = {b.category.strip().lower() for b in budgets}
    for cat, total in spending_map.items():
        if cat and cat not in budget_categories and total > 0:
            uncategorized_spending.append({
                "category": spending_label.get(cat, cat),
                "spent": total,
            })

    return {
        "period": f"{year}-{month:02d}",
        "warning_threshold": warning_threshold,
        "total_planned": sum(float(b.amount) for b in budgets),
        "total_spent": sum(spending_map.values()),
        "overall_usage": round(
            sum(spending_map.values()) / sum(float(b.amount) for b in budgets) * 100, 1
        ) if any(float(b.amount) > 0 for b in budgets) else 0,
        "alerts": alerts,
        "uncategorized": uncategorized_spending,
        "alert_count": len(alerts),
        "has_alert": len(alerts) > 0,
    }


@app.post("/api/budget-alerts/thresholds")
async def update_alert_thresholds(
    warning: float = Query(..., ge=0, le=5),
    session: Session = Depends(get_session)
):
    """Aggiorna la soglia di warning degli alert di budget"""
    key = "budget_alert_warning"
    existing = session.execute(
        select(AppSetting).where(AppSetting.key == key)
    ).scalars().first()
    if existing:
        existing.value = str(warning)
    else:
        session.add(AppSetting(key=key, label="Soglia warning budget", value=str(warning)))
    session.commit()
    return {"success": True, "warning": warning}


@app.get("/api/budget-alerts/thresholds")
async def get_alert_thresholds(
    session: Session = Depends(get_session)
):
    """Recupera la soglia corrente degli alert"""
    warning_setting = session.execute(
        select(AppSetting).where(AppSetting.key == "budget_alert_warning")
    ).scalars().first()
    return {
        "warning": float(warning_setting.value) if warning_setting else 1.2,
    }


_REPORT_MONTHS = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
_REPORT_MONTHS_LONG = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def _report_num(value: Any) -> float:
    return round(float(value or 0), 2)


def _report_period_total(session: Session, year: int, month: int, tx_type: str) -> float:
    return _report_num(session.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        extract("year", Transaction.effective_on) == year,
        extract("month", Transaction.effective_on) == month,
        Transaction.transaction_type == tx_type,
        BUDGET_MOVEMENT,
    )))


@app.get("/api/reports/{kind}")
def download_report(kind: str, year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12), session: Session = Depends(get_session)):
    """Esporta un report Excel o PDF con i dati del periodo selezionato,
    generato dal database dell'app (riepilogo, andamento, ultimi movimenti).
    """
    if kind not in ("excel", "pdf"):
        raise HTTPException(status_code=404, detail=f"Formato report non supportato: {kind}")

    income, expenses = (_report_period_total(session, year, month, t) for t in ("Income", "Expenses"))
    savings = round(income - expenses, 2)
    net_worth = sum(
        (-_report_num(account.current_balance) if account.source_group == "liability" else _report_num(account.current_balance))
        for account in session.scalars(select(Account)).all()
        if account.counts_in_net_worth
    )

    trend = []
    for offset in range(-6, 1):
        absolute = year * 12 + month - 1 + offset
        current_year, current_month = absolute // 12, absolute % 12 + 1
        trend.append({
            "month": _REPORT_MONTHS[current_month - 1],
            "income": _report_period_total(session, current_year, current_month, "Income"),
            "expenses": _report_period_total(session, current_year, current_month, "Expenses"),
        })

    period_start = date(year, month, 1)
    period_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    rows = session.scalars(
        select(Transaction).where(
            Transaction.effective_on >= period_start,
            Transaction.effective_on < period_end,
            BUDGET_MOVEMENT,
        ).order_by(Transaction.effective_on.desc(), Transaction.id.desc())
    ).all()
    transactions = []
    for row in rows:
        amount = _report_num(row.amount)
        signed = amount if row.transaction_type == "Income" else -amount
        transactions.append({
            "date": row.effective_on.isoformat(),
            "type": row.transaction_type,
            "category": row.category,
            "description": row.details or row.category,
            "account": row.account_name,
            "destination": row.destination_name,
            "signed_amount": signed,
        })

    report = {
        # Gli importi dell'app sono in euro e il report non converte: un'altra
        # valuta sarebbe stata solo un'etichetta sbagliata sugli stessi numeri.
        "currency": "EUR",
        "period": f"{_REPORT_MONTHS_LONG[month - 1]} {year}",
        "income": income,
        "expenses": expenses,
        "savings": savings,
        "net_worth": round(net_worth, 2),
        "trend": trend,
        "transactions": transactions,
    }

    if kind == "excel":
        stream = excel_report(report)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"Money-report-{year}-{month:02d}.xlsx"
    else:
        stream = pdf_report(report)
        media_type = "application/pdf"
        filename = f"Money-report-{year}-{month:02d}.pdf"

    return StreamingResponse(stream, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


from .core_routes import register_core_routes
from .fire_routes import register_fire_routes

register_core_routes(app)
register_fire_routes(app)
