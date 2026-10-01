import { describe, expect, it } from 'vitest';
import { previewEffectiveDate } from '@/lib/effective-date';

// La stessa regola di `effective_date` nel backend: se l'anteprima nel modulo
// dice un mese e il server ne salva un altro, il budget cambia sotto gli occhi.
describe('competenza delle entrate tardive', () => {
  it('dal giorno scelto in poi, un\'entrata conta dal primo del mese dopo', () => {
    expect(previewEffectiveDate('2026-03-20', 'Income', 'Active', 20)).toBe('2026-04-01');
    expect(previewEffectiveDate('2026-03-31', 'Income', 'Active', 20)).toBe('2026-04-01');
  });

  it('il giorno prima della soglia resta nel suo mese', () => {
    expect(previewEffectiveDate('2026-03-19', 'Income', 'Active', 20)).toBe('2026-03-19');
  });

  it('a dicembre passa all\'anno dopo', () => {
    expect(previewEffectiveDate('2026-12-28', 'Income', 'Active', 20)).toBe('2027-01-01');
  });

  it('con lo spostamento spento, o su una spesa, non cambia niente', () => {
    expect(previewEffectiveDate('2026-03-25', 'Income', 'Inactive', 20)).toBe('2026-03-25');
    expect(previewEffectiveDate('2026-03-25', 'Expenses', 'Active', 20)).toBe('2026-03-25');
  });

  it('una data non valida torna com\'e\'', () => {
    expect(previewEffectiveDate('', 'Income', 'Active', 20)).toBe('');
  });
});
