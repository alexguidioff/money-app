import { describe, expect, it } from 'vitest';
import { etichettaPeriodo } from '@/lib/period-label';

const INGLESE = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Il server manda "Mag 25" e "T1 25": con l'app in inglese finivano cosi' sui grafici.
describe('etichette di periodo', () => {
  it('i mesi del server diventano quelli della lingua', () => {
    expect(etichettaPeriodo('Mag 25', INGLESE, 'en')).toBe('May 25');
    expect(etichettaPeriodo('Gen', INGLESE, 'en')).toBe('Jan');
    expect(etichettaPeriodo('Dic 26', INGLESE, 'en')).toBe('Dec 26');
  });

  it('i trimestri usano la lettera della lingua', () => {
    expect(etichettaPeriodo('T3 25', INGLESE, 'en')).toBe('Q3 25');
    expect(etichettaPeriodo('T3 25', INGLESE, 'fr')).toBe('T3 25');
  });

  it('tutto il resto passa invariato', () => {
    // Date numeriche dei debiti, anni, e parole che somigliano a un mese.
    for (const valore of ['03/2026', '15/03/2026', '2026', 'Mayo', 'Set di pentole', 'Mar 2026']) {
      expect(etichettaPeriodo(valore, INGLESE, 'en')).toBe(valore);
    }
    expect(etichettaPeriodo(undefined, INGLESE, 'en')).toBe('');
  });
});
