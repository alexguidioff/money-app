import { describe, expect, it } from 'vitest';
import { previewEffectiveDate } from '@/lib/effective-date';

// La stessa regola di `effective_date` nel backend: se l'anteprima nel modulo
// dice un mese e il server ne salva un altro, il budget cambia sotto gli occhi.
describe('competenza dei movimenti di fine mese', () => {
  it('dal giorno scelto in poi, un movimento conta dal primo del mese dopo', () => {
    expect(previewEffectiveDate('2026-03-20', 'Income', 'Active', 20)).toBe('2026-04-01');
    expect(previewEffectiveDate('2026-03-31', 'Income', 'Active', 20)).toBe('2026-04-01');
  });

  it('vale anche per le uscite: la spesa del 30 e\' del mese dopo', () => {
    expect(previewEffectiveDate('2026-03-30', 'Expenses', 'Active', 20)).toBe('2026-04-01');
    expect(previewEffectiveDate('2026-03-30', 'expenses', 'Active', 25)).toBe('2026-04-01');
  });

  it('il giorno prima della soglia resta nel suo mese', () => {
    expect(previewEffectiveDate('2026-03-19', 'Income', 'Active', 20)).toBe('2026-03-19');
    expect(previewEffectiveDate('2026-03-24', 'Expenses', 'Active', 25)).toBe('2026-03-24');
  });

  it('a dicembre passa all\'anno dopo', () => {
    expect(previewEffectiveDate('2026-12-28', 'Income', 'Active', 20)).toBe('2027-01-01');
    expect(previewEffectiveDate('2026-12-30', 'Expenses', 'Active', 20)).toBe('2027-01-01');
  });

  it('giroconti e investimenti restano dove sono: non sono ne\' spese ne\' incassi', () => {
    expect(previewEffectiveDate('2026-03-25', 'Transfers', 'Active', 20)).toBe('2026-03-25');
    expect(previewEffectiveDate('2026-03-25', 'Investment', 'Active', 20)).toBe('2026-03-25');
    expect(previewEffectiveDate('2026-03-25', 'Debt', 'Active', 20)).toBe('2026-03-25');
  });

  it('con lo spostamento spento non cambia niente', () => {
    expect(previewEffectiveDate('2026-03-25', 'Income', 'Inactive', 20)).toBe('2026-03-25');
    expect(previewEffectiveDate('2026-03-25', 'Expenses', 'Inactive', 20)).toBe('2026-03-25');
  });

  it('una data non valida torna com\'e\'', () => {
    expect(previewEffectiveDate('', 'Income', 'Active', 20)).toBe('');
  });
});
