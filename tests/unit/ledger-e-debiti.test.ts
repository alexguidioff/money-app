import { describe, expect, it } from 'vitest';
import { nettoOperazioni } from '@/lib/ledger-preview';
import { liabilityTermsPayload } from '@/lib/payloads';

describe('anteprima delle operazioni collegate', () => {
  it('un acquisto costa prezzo piu\' commissione', () => {
    // Prima ogni riga era trattata come una vendita: 1000 + 2 risultava 998.
    expect(nettoOperazioni([{ transactionType: 'Buy', units: '10', price: '100', fee: '2' }])).toBe(1002);
  });

  it('una vendita incassa prezzo meno commissione', () => {
    expect(nettoOperazioni([{ transactionType: 'Sell', units: '10', price: '100', fee: '2' }])).toBe(998);
  });

  it('acquisti e vendite nello stesso bonifico si compensano, campi vuoti contano zero', () => {
    expect(nettoOperazioni([
      { transactionType: 'Buy', units: '5', price: '100', fee: '1' },
      { transactionType: 'Sell', units: '2', price: '100', fee: '1' },
      { transactionType: 'Buy', units: '', price: '', fee: '' },
    ])).toBe(302);
  });
});

function modulo(campi: Record<string, string>): FormData {
  const form = new FormData();
  for (const [nome, valore] of Object.entries(campi)) form.set(nome, valore);
  return form;
}

describe('condizioni di un debito', () => {
  it('una linea di credito senza i campi del piano non manda "null"', () => {
    // Il modulo di una linea non ha frequenza, struttura e preammortamento:
    // arrivavano come la stringa "null" e il salvataggio veniva rifiutato.
    const payload = liabilityTermsPayload(modulo({
      kind: 'credit_line', start_date: '2023-01-12', credit_limit: '40000', debt_type: 'revolving',
      annual_rate: '1.5', rate_type: 'variable', end_date: '2030-01-01', status: 'active',
    }), []);
    expect(payload).toMatchObject({
      kind: 'credit_line', payment_frequency: 'monthly', payment_structure: 'amortizing',
      grace_interest: 'paid', credit_limit: 40000, original_principal: 1, planned_drawdowns: [],
    });
    expect(JSON.stringify(payload)).not.toContain('"null"');
  });

  it('un prestito prende capitale e data d\'inizio dalle tranche', () => {
    const payload = liabilityTermsPayload(modulo({
      kind: 'term_loan', debt_type: 'personal', annual_rate: '1.6', rate_type: 'fixed', payment_frequency: 'monthly',
      payment_structure: 'amortizing', grace_interest: 'paid', end_date: '2035-01-01', status: 'active',
    }), [{ occurredOn: '2023-08-02', amount: '10000' }, { occurredOn: '2023-01-12', amount: 10000 }]);
    expect(payload).toMatchObject({ start_date: '2023-01-12', original_principal: 20000, credit_limit: null });
  });

  it('un prestito senza tranche, o una linea senza data, non si puo\' salvare', () => {
    expect(liabilityTermsPayload(modulo({ kind: 'term_loan' }), [])).toBeNull();
    expect(liabilityTermsPayload(modulo({ kind: 'credit_line' }), [])).toBeNull();
  });
});
