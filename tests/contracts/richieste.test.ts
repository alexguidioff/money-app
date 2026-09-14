import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { expect, it } from 'vitest';
import type { RetirementProfile } from '@/components/settings/retirement-profile-form';
import { accountPayload, budgetCreatePayload, budgetUpdatePayload, expenseRulesPayload, goalPayload, incomeStreamPayload,
  ledgerOperationPayload, liabilityTermsPayload, notePayload, recurringPayload, splitPayload, transactionPayload } from '@/lib/payloads';

/*
 * I corpi che il frontend manda, costruiti dalle stesse funzioni che usano i
 * moduli. Il file lo legge `python -m tests.contratti richieste`, che li passa
 * ai gestori veri del backend su un database sqlite: un campo col nome
 * sbagliato (`birthYear` al posto di `birth_year`) o un valore che il backend
 * rifiuta (la stringa "null") fa fallire il gate.
 *
 * I conti nominati qui (Banca, Broker, Fido, Prestito) sono quelli che il
 * backend semina in `tests/contratti.py`.
 */

const USCITA = resolve(__dirname, '../fixtures/contracts/richieste.json');

function modulo(campi: Record<string, string>): FormData {
  const form = new FormData();
  for (const [nome, valore] of Object.entries(campi)) form.set(nome, valore);
  return form;
}

const oggi = new Date().toISOString().slice(0, 10);
const annoProssimo = `${new Date().getFullYear() + 5}-01-01`;

// Lo stato del modulo profilo cosi' com'e' al salvataggio: e' quello che parte.
const profilo: RetirementProfile = {
  birthYear: 1998, country: 'CH', targetRetirementAge: 60, realReturn: 4, withdrawalRate: 3.5,
  withdrawalTaxRate: 8, expenseBasis: 'custom', customAnnualExpenses: 40000, leanAnnualExpenses: 30000,
  inflation: 1.5, notes: '',
};

const richieste = [
  { endpoint: 'profile', case: 'profilo completo', body: profilo },
  { endpoint: 'profile', case: 'senza spese personalizzate', body: { ...profilo, expenseBasis: 'median', customAnnualExpenses: null, leanAnnualExpenses: null } },
  { endpoint: 'stream', case: 'rendita non indicizzata', body: incomeStreamPayload({
    name: 'LPP nuova', kind: 'annuity', amount: 15000, startAge: 65, indexed: false, country: 'CH', amountIfStoppingNow: 8000, notes: '' }) },
  { endpoint: 'stream', case: 'modifica: la riga letta ha anche id', body: incomeStreamPayload({
    ...{ id: 7 }, name: 'Pilastro 3a', kind: 'capital', amount: 90000, startAge: 64, indexed: true, country: 'CH', amountIfStoppingNow: null, notes: 'conto' } as never) },
  { endpoint: 'expenseRules', case: 'resta, sparisce, cambia', body: expenseRulesPayload([
    { category: 'Housing', mode: 'change', newAmount: 7000 }, { category: 'Groceries', mode: 'drop', newAmount: null },
    { category: 'Salary', mode: 'stay', newAmount: null }]) },
  { endpoint: 'transaction', case: 'spesa', body: transactionPayload(modulo({
    occurred_on: oggi, transaction_type: 'Expenses', category: 'Groceries', amount: '42.30', account_name: 'Banca', details: 'Spesa' })) },
  { endpoint: 'transaction', case: 'entrata fuori budget', body: transactionPayload(modulo({
    occurred_on: oggi, transaction_type: 'Income', category: 'Salary', amount: '100', account_name: 'Banca', exclude_budget: 'on' })) },
  { endpoint: 'transaction', case: 'rata con quota interessi', body: transactionPayload(modulo({
    occurred_on: oggi, transaction_type: 'Debt', amount: '450', account_name: 'Banca', destination_name: 'Prestito',
    debt_principal: '420', debt_interest: '30' })) },
  { endpoint: 'transaction', case: 'interessi addebitati sul fido', body: transactionPayload(modulo({
    occurred_on: oggi, transaction_type: 'Expenses', category: 'Commissions', amount: '12', account_name: 'Fido', debt_interest: '12' })) },
  { endpoint: 'transaction', case: 'versamento al broker', body: transactionPayload(modulo({
    occurred_on: oggi, transaction_type: 'Investment', amount: '1000', account_name: 'Banca', destination_name: 'Broker' })) },
  { endpoint: 'creditLineTerms', case: 'linea senza i campi del piano', body: liabilityTermsPayload(modulo({
    kind: 'credit_line', start_date: '2024-01-01', credit_limit: '30000', debt_type: 'revolving', annual_rate: '1.5',
    rate_type: 'variable', end_date: annoProssimo, status: 'active' }), []) },
  { endpoint: 'termLoanTerms', case: 'prestito a tranche', body: liabilityTermsPayload(modulo({
    kind: 'term_loan', debt_type: 'personal', annual_rate: '2', rate_type: 'fixed', payment_frequency: 'monthly',
    payment_structure: 'amortizing', grace_interest: 'paid', repayment_start_date: '2024-07-01', end_date: annoProssimo,
    status: 'active', notes: '' }),
    [{ occurredOn: '2024-01-15', amount: 10000 }, { occurredOn: '2024-06-15', amount: '5000' }]) },
  { endpoint: 'setting', case: 'colore', body: { value: 'Yellow' } },
  { endpoint: 'account', case: 'conto broker', body: accountPayload(modulo({
    name: 'Nuovo broker', source_group: 'asset', starting_balance: '0', notes: '', is_broker: 'on', counts_in_net_worth: 'on' }), false) },
  { endpoint: 'account', case: 'banca', body: accountPayload(modulo({ name: 'Conto nuovo', source_group: 'bank', starting_balance: '150.5', counts_in_net_worth: 'on' }), false) },
  { endpoint: 'goal', case: 'accumulo verso un conto', body: goalPayload(modulo({
    name: 'Viaggio', starting_amount: '100', target_amount: '3000', start_date: oggi, target_date: annoProssimo,
    kind: 'contributions', target_account: 'Banca' })) },
  { endpoint: 'goal', case: 'patrimonio netto senza date', body: goalPayload(modulo({ name: 'Libertà', starting_amount: '', target_amount: '500000', kind: 'net_worth' })) },
  { endpoint: 'ledgerOperation', case: 'acquisto', body: ledgerOperationPayload(modulo({
    occurred_on: oggi, name: 'ETF Mondo', transaction_type: 'Buy', amount: '1000', units: '10', price: '100', currency: 'eur', fee: '1', notes: '' })) },
  { endpoint: 'note', case: 'appunto', body: notePayload(modulo({ section: '', title: 'Ricordare', body: 'Rinnovare il fido', status: '' })) },
  { endpoint: 'budgetCreate', case: 'nuova categoria', body: budgetCreatePayload(new Date().getFullYear(), 3, 'Expenses', 'Viaggi', 200) },
  { endpoint: 'budgetUpdate', case: 'modifica importo', body: budgetUpdatePayload(' Groceries ', '275') },
  { endpoint: 'recurring', case: 'affitto mensile', body: recurringPayload({
    description: 'Affitto', amount: '900', category: 'Housing', type: 'Expenses', recurrence: 'FREQ=MONTHLY;BYMONTHDAY=1',
    startDate: oggi, endDate: '' }, modulo({ accountName: 'Banca' })) },
  { endpoint: 'recurring', case: 'trasferimento mensile', body: recurringPayload({
    description: 'Accantonamento', amount: '200', category: '', type: 'Transfers', recurrence: 'FREQ=MONTHLY;BYMONTHDAY=15',
    startDate: oggi, endDate: annoProssimo }, modulo({ accountName: 'Banca', destinationName: 'Casa' })) },
  { endpoint: 'split', case: 'meta\' affitto da farsi restituire', body: splitPayload({
    amount: '450', type: 'Transfers', category: 'Housing', destination: 'Casa', details: 'Affitto, parte di Mary' }) },
  { endpoint: 'split', case: 'parte con un\'altra categoria', body: splitPayload({
    amount: '100', type: 'Expenses', category: 'Groceries', destination: 'Casa', details: '' }) },
];

it('i corpi delle richieste sono costruiti e scritti per il backend', () => {
  expect(richieste.every((r) => r.body !== null)).toBe(true);
  mkdirSync(dirname(USCITA), { recursive: true });
  writeFileSync(USCITA, `${JSON.stringify(richieste, null, 1)}\n`);
});
