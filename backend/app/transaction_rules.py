"""Shared movement rules: budget inclusion is separate from actual cash flow."""
from sqlalchemy import and_, func, or_
from .models import Transaction
from .models import Account
from fastapi import HTTPException
from sqlalchemy import select

REAL_MOVEMENT = Transaction.is_recurring_template.is_(False)


def blank(column):
    return func.trim(func.coalesce(column, "")) == ""


# Un Investment sposta denaro come un giroconto - due conti, nessuna categoria -
# ma un lato dev'essere un conto broker, ed e' l'unico tipo che il ledger
# investimenti accetta. Il tipo `Savings` non esiste piu': il risparmio si
# deriva da entrate meno uscite, non si registra.
SPOSTAMENTI = ["Transfers", "Investment", "Debt"]
TIPI_MOVIMENTO = ["Income", "Expenses", "Transfers", "Investment", "Debt"]
BUDGET_MOVEMENT = and_(REAL_MOVEMENT, Transaction.counts_in_budget.is_(True),
                       Transaction.transaction_type.not_in(SPOSTAMENTI))

MISSING = {
    "date": Transaction.occurred_on.is_(None),
    "amount": or_(Transaction.amount.is_(None), Transaction.amount <= 0),
    "account": blank(Transaction.account_name),
    "category": and_(Transaction.transaction_type.not_in(SPOSTAMENTI), blank(Transaction.category)),
    "destination": or_(and_(Transaction.transaction_type.in_(SPOSTAMENTI),
                       or_(blank(Transaction.destination_name), Transaction.destination_name == Transaction.account_name)),
                       and_(Transaction.transaction_type.in_(["Income", "Expenses"]), ~blank(Transaction.destination_name))),
    "type": Transaction.transaction_type.not_in(TIPI_MOVIMENTO),
}
# Un movimento accettato com'e' resta incompleto, ma smette di chiedere
# attenzione: `missing_fields` continua a dire cosa manca, e `validate_movement`
# continua a rifiutare chi ne crea uno nuovo. Cambia solo chi viene contato.
INCOMPLETE_MOVEMENT = and_(or_(*MISSING.values()), Transaction.incomplete_accepted.is_(False))


def missing_fields(tx):
    fields = []
    if not tx.occurred_on: fields.append("date")
    if tx.amount is None or tx.amount <= 0: fields.append("amount")
    if not (tx.account_name or "").strip(): fields.append("account")
    if tx.transaction_type in SPOSTAMENTI:
        if not (tx.destination_name or "").strip() or tx.destination_name == tx.account_name:
            fields.append("destination")
    elif not (tx.category or "").strip():
        fields.append("category")
    if tx.transaction_type in {"Income", "Expenses"} and (tx.destination_name or "").strip():
        fields.append("destination")
    if tx.transaction_type not in TIPI_MOVIMENTO:
        fields.append("type")
    return fields


def validate_movement(session, tx, previous_accounts=()):
    fields = missing_fields(tx)
    if fields:
        raise HTTPException(422, detail={"code": "movementIncomplete", "fields": fields})
    names = [tx.account_name]
    if tx.transaction_type in SPOSTAMENTI:
        names.append(tx.destination_name)
    conti = {}
    for name in names:
        account = session.scalar(select(Account).where(Account.name == name))
        if account is None or (account.is_active is False and name not in previous_accounts):
            raise HTTPException(422, detail={"code": "accountInactive", "account": name})
        conti[name] = account
    if tx.transaction_type == "Investment" and not any(c.is_broker for c in conti.values()):
        # Un Investment senza broker non e' un investimento: e' un giroconto
        # scritto male, e permetterlo vorrebbe dire lasciar collegare al ledger
        # movimenti che con i titoli non c'entrano niente.
        raise HTTPException(422, detail={"code": "investmentNeedsBroker"})
    if tx.transaction_type == "Debt" and sum(c.source_group == "liability" for c in conti.values()) != 1:
        raise HTTPException(422, detail={"code": "debtNeedsLiability"})


def verso_debito(tx, conti_passivi) -> str:
    """Il verso contabile si legge dai conti, non si chiede all'utente."""
    passivi = {str(nome).strip().casefold() for nome in conti_passivi}
    origine = str(tx.account_name or "").strip().casefold() in passivi
    destinazione = str(tx.destination_name or "").strip().casefold() in passivi
    if origine == destinazione:
        raise ValueError("debtNeedsLiability")
    return "drawdown" if origine else "repayment"


def set_refund(session, tx, original_id):
    if original_id == tx.refund_of_id:
        return
    original = None
    if original_id is not None:
        original = session.scalar(select(Transaction).where(Transaction.id == original_id).with_for_update())
        if (original is None or original.id == tx.id or original.is_recurring_template
                or {original.transaction_type, tx.transaction_type} != {"Income", "Expenses"}):
            raise HTTPException(422, detail="refundInvalid")
    if tx.refund_of_id:
        tx.counts_in_budget = True
    tx.refund_of_id = original_id
    if original is not None:
        tx.counts_in_budget = False


def validate_refund(session, tx):
    if tx.refund_of_id:
        original = session.get(Transaction, tx.refund_of_id)
        if (original is None or {original.transaction_type, tx.transaction_type} != {"Income", "Expenses"}
                or tx.counts_in_budget):
            raise HTTPException(422, detail="refundUnlinkFirst")
        already_refunded = session.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.refund_of_id == original.id, Transaction.id != (tx.id or -1)))
        if (already_refunded or 0) + tx.amount > original.amount:
            raise HTTPException(422, detail="refundExceedsOriginal")
    refunds = session.scalars(select(Transaction).where(Transaction.refund_of_id == (tx.id or -1))).all()
    if refunds:
        if tx.transaction_type not in {"Income", "Expenses"} or any(
                {tx.transaction_type, refund.transaction_type} != {"Income", "Expenses"} for refund in refunds):
            raise HTTPException(422, detail="refundUnlinkFirst")
        if sum(refund.amount for refund in refunds) > tx.amount:
            raise HTTPException(422, detail="refundExceedsOriginal")
