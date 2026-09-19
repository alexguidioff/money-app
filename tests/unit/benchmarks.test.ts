import { describe, expect, it } from 'vitest';
import { BENCHMARKS } from '@/lib/data/benchmarks';

// La stessa regola di `SYMBOL_PATTERN` in backend/app/market_data.py: quello che
// non le somiglia viene rifiutato prima di uscire in rete. Scritta qui a mano
// apposta, cosi' i due posti si controllano a vicenda invece di fidarsi.
const SIMBOLO_VALIDO = /^[A-Za-z0-9.^=-]{1,80}$/;

describe('indici di confronto', () => {
  it('ogni indice ha un simbolo che la fonte accettera\'', () => {
    // Un simbolo fuori da questa forma non da' errore a schermo: la quotazione
    // non arriva, il confronto resta vuoto, e sembra che l'indice non abbia
    // storia invece che essere un simbolo mai esistito.
    const sbagliati = BENCHMARKS.filter((indice) => !SIMBOLO_VALIDO.test(indice.symbol));
    expect(sbagliati).toEqual([]);
  });

  it('nessun simbolo doppio e nessun nome vuoto', () => {
    const simboli = BENCHMARKS.map((indice) => indice.symbol);
    expect(simboli.length).toBe(new Set(simboli).size);
    expect(BENCHMARKS.filter((indice) => !indice.name.trim())).toEqual([]);
  });
});
