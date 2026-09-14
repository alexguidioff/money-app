/*
 * I corpi delle richieste che il frontend costruisce da un modulo. Stanno qui,
 * fuori dai componenti, perche' i test di contratto li mandano ai gestori veri
 * del backend: e' su questo confine che sono nati `birthYear` al posto di
 * `birth_year` e la stringa "null" al posto di un valore.
 */

const SPOSTAMENTI = ['Transfers', 'Investment', 'Debt'];

export function transactionPayload(form: FormData) {
  const txType = String(form.get('transaction_type') ?? 'Expenses');
  return {
    occurred_on: form.get('occurred_on'),
    transaction_type: txType,
    category: form.get('category'),
    amount: Number(form.get('amount')),
    account_name: form.get('account_name') || null,
    destination_name: form.get('destination_name') || null,
    counts_in_budget: !SPOSTAMENTI.includes(txType) && !form.get('exclude_budget'),
    refund_of_id: form.get('refund_of_id') ? Number(form.get('refund_of_id')) : null,
    goal: form.get('goal') || null,
    details: form.get('details') || null,
    ...(txType === 'Debt' ? {
      debt_principal: Number(form.get('debt_principal') || 0), debt_interest: Number(form.get('debt_interest') || 0),
    } : {}),
    // Su una spesa il campo c'e' solo per i conti di debito, e solo se
    // compilato: lasciarlo vuoto vuol dire "non e' un interesse".
    ...(txType === 'Expenses' && String(form.get('debt_interest') ?? '').trim()
      ? { debt_interest: Number(form.get('debt_interest')) } : {}),
  };
}

export type DrawdownDraft = { occurredOn: string; amount: number | string };

/** Le condizioni di un debito. `null` se il modulo non basta a costruirle. */
export function liabilityTermsPayload(form: FormData, drawdowns: readonly DrawdownDraft[]) {
  const linea = String(form.get('kind')) === 'credit_line';
  const principal = drawdowns.reduce((sum, row) => sum + Number(row.amount || 0), 0);
  // Per una linea la data d'inizio viene dal campo, non dalla prima tranche:
  // le tranche non esistono. E il "capitale" e' un valore di servizio che il
  // backend ignora, ma il payload lo vuole maggiore di zero.
  const startDate = linea ? String(form.get('start_date') ?? '')
    : [...drawdowns].sort((a, b) => a.occurredOn.localeCompare(b.occurredOn))[0]?.occurredOn;
  if (!startDate || (!linea && principal <= 0)) return null;
  return {
    kind: linea ? 'credit_line' : 'term_loan',
    credit_limit: linea && String(form.get('credit_limit') ?? '').trim() ? Number(form.get('credit_limit')) : null,
    debt_type: String(form.get('debt_type')), original_principal: linea ? 1 : principal, annual_rate: Number(form.get('annual_rate')),
    rate_type: String(form.get('rate_type')),
    // Con una linea i campi del piano non sono nel modulo, quindi il form
    // non li invia: senza questi valori di ripiego arrivavano come "null" e
    // il backend li rifiutava prima di arrivare a guardare il tipo.
    payment_frequency: String(form.get('payment_frequency') || 'monthly'),
    payment_structure: String(form.get('payment_structure') || 'amortizing'),
    grace_interest: String(form.get('grace_interest') || 'paid'),
    start_date: startDate,
    repayment_start_date: String(form.get('repayment_start_date') || '') || null,
    end_date: String(form.get('end_date')),
    planned_drawdowns: drawdowns.map((row) => ({ occurred_on: row.occurredOn, amount: Number(row.amount) })),
    status: String(form.get('status')), notes: String(form.get('notes') || '') || null,
  };
}

export type ExpenseRuleDraft = { category: string; mode: 'stay' | 'drop' | 'change'; newAmount: number | null };

export function expenseRulesPayload(rows: readonly ExpenseRuleDraft[]) {
  return { rules: rows.map((r) => ({ category: r.category, mode: r.mode, amount: r.newAmount })) };
}

export type IncomeStreamDraft = {
  name: string; kind: 'annuity' | 'capital'; amount: number; startAge: number; indexed: boolean;
  country: string | null; amountIfStoppingNow: number | null; notes: string;
};

/**
 * Solo i campi del flusso: in modifica il modulo parte dalla riga letta, che ha
 * anche `id`, e mandarlo nel corpo era un campo che il backend scartava.
 */
export function incomeStreamPayload(values: IncomeStreamDraft) {
  const { name, kind, amount, startAge, indexed, country, amountIfStoppingNow, notes } = values;
  return { name, kind, amount, startAge, indexed, country, amountIfStoppingNow, notes };
}

const testo = (form: FormData, chiave: string) => {
  const valore = form.get(chiave);
  return typeof valore === 'string' ? valore : '';
};

/** Un conto, dal modulo Patrimonio. In creazione la casella "archiviato" non c'e'. */
export function accountPayload(data: FormData, modifica: boolean) {
  return {
    name: String(data.get('name') || '').trim(),
    source_group: String(data.get('source_group') || ''),
    starting_balance: Number(data.get('starting_balance') || 0),
    notes: String(data.get('notes') || '').trim() || null,
    counts_in_net_worth: data.get('counts_in_net_worth') !== null,
    needs_manual_valuation: data.get('needs_manual_valuation') !== null,
    is_broker: data.get('is_broker') !== null,
    is_liquid: String(data.get('source_group')) === 'asset' ? data.get('is_liquid') !== null : String(data.get('source_group')) === 'bank',
    is_active: modifica ? data.get('is_archived') === null : true,
  };
}

export function goalPayload(form: FormData) {
  return {
    name: testo(form, 'name'), starting_amount: Number(testo(form, 'starting_amount')),
    target_amount: Number(testo(form, 'target_amount')), start_date: testo(form, 'start_date') || null,
    target_date: testo(form, 'target_date') || null, completed_at: testo(form, 'completed_at') || null,
    kind: testo(form, 'kind') || 'contributions', target_account: testo(form, 'target_account') || null,
  };
}

/** Un'operazione del ledger, dal modulo Investimenti. */
export function ledgerOperationPayload(form: FormData) {
  return {
    occurred_on: String(form.get('occurred_on')), name: String(form.get('name')).trim(),
    transaction_type: String(form.get('transaction_type')), amount: Number(form.get('amount')),
    units: Number(form.get('units')), price: Number(form.get('price')), currency: String(form.get('currency')).toUpperCase(),
    fee: Number(form.get('fee') || 0), notes: String(form.get('notes') || ''),
  };
}

export function notePayload(form: FormData) {
  return { section: testo(form, 'section') || 'Appunti', title: testo(form, 'title'), body: testo(form, 'body'), status: testo(form, 'status') || null };
}

export function budgetCreatePayload(year: number, month: number, budgetType: string, category: string, amount: number) {
  return { year, month, budget_type: budgetType, category, amount };
}

export function budgetUpdatePayload(category: string, amount: string | number) {
  return { category: category.trim(), amount: Number(amount) };
}

export type RecurringDraft = { description: string; amount: string; category: string; type: string; recurrence: string; startDate: string; endDate: string };

/** Una ricorrenza: il modulo tiene i campi nello stato, i conti nel FormData. */
export function recurringPayload(draft: RecurringDraft, values: FormData) {
  return {
    description: draft.description,
    amount: Number(draft.amount),
    category: draft.type === 'Transfers' ? '_' : draft.category,
    accountName: String(values.get('accountName') || ''),
    destinationName: draft.type === 'Transfers' ? String(values.get('destinationName') || '') : null,
    categoryRaw: 'Other',
    transactionType: draft.type,
    recurrence_rule: draft.recurrence,
    start_date: draft.startDate,
    recurrence_end_date: draft.endDate || null,
  };
}

/** La parte da togliere a un movimento per farne uno a se'. */
export function splitPayload(parte: { amount: string; type: string; category: string; destination: string; details: string }) {
  const spostamento = parte.type === 'Transfers';
  return {
    amount: Number(parte.amount),
    transaction_type: parte.type,
    category: spostamento ? null : parte.category || null,
    destination_name: spostamento ? parte.destination || null : null,
    details: parte.details,
  };
}
