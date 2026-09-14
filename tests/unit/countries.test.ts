import { describe, expect, it } from 'vitest';
import { COUNTRIES, TAX_NOTES } from '@/lib/data/countries';

const LINGUE = ['it', 'en', 'de', 'es', 'fr'] as const;

describe('paesi e note fiscali', () => {
  it('ogni paese ha una nota fiscale in tutte e cinque le lingue', () => {
    // Le note esistevano solo in italiano: in ogni altra lingua compariva il
    // testo italiano, e l'utente l'ha trovato prima dei test.
    const mancanti = COUNTRIES.flatMap((paese) => LINGUE.flatMap((lingua) =>
      TAX_NOTES.some((nota) => nota.country === paese.code && nota.language === lingua && nota.body.trim())
        ? [] : [`${paese.code}/${lingua}`]));
    expect(mancanti).toEqual([]);
  });

  it('nessuna nota per un paese che non esiste o doppia', () => {
    const codici = new Set(COUNTRIES.map((paese) => paese.code));
    const coppie = TAX_NOTES.map((nota) => `${nota.country}/${nota.language}`);
    expect(TAX_NOTES.filter((nota) => !codici.has(nota.country))).toEqual([]);
    expect(coppie.length).toBe(new Set(coppie).size);
  });

  it('ogni paese ha eta\' pensionabile e simulatore ufficiale', () => {
    for (const paese of COUNTRIES) {
      expect(paese.retirementAge, paese.code).toBeGreaterThanOrEqual(55);
      expect(paese.retirementAge, paese.code).toBeLessThanOrEqual(75);
      expect(paese.simulatorUrl, paese.code).toMatch(/^https:\/\//);
    }
  });
});
