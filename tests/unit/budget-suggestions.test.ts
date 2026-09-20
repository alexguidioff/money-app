import { describe, expect, it } from 'vitest';
import { suggerimentiSenzaRiga } from '@/lib/budget-suggestions';

// Numeri tondi e inventati: il repository e' pubblico e non ci finiscono
// importi veri di nessuno.
const voce = (category: string, median: number) => ({ category, median });

describe('suggerimentiSenzaRiga', () => {
  it('tiene solo le categorie che non hanno una riga', () => {
    const esito = suggerimentiSenzaRiga(
      [voce('Affitto', 1000), voce('Spesa', 300)],
      [{ category: 'Affitto' }],
    );
    expect(esito.map((v) => v.category)).toEqual(['Spesa']);
  });

  it('non distingue maiuscole ne\' spazi ai bordi: la stessa categoria e\' una sola', () => {
    const esito = suggerimentiSenzaRiga(
      [voce('  affitto ', 1000), voce('Affitto', 800)],
      [{ category: 'AFFITTO' }],
    );
    expect(esito).toEqual([]);
  });

  it('lascia fuori la mediana a zero, che non e\' un suggerimento', () => {
    const esito = suggerimentiSenzaRiga([voce('Palestra', 0), voce('Spesa', 300)], []);
    expect(esito.map((v) => v.category)).toEqual(['Spesa']);
  });

  it('mette prima le piu\' grosse', () => {
    const esito = suggerimentiSenzaRiga([voce('Piccola', 10), voce('Grande', 900), voce('Media', 300)], []);
    expect(esito.map((v) => v.category)).toEqual(['Grande', 'Media', 'Piccola']);
  });

  it('senza suggerimenti o senza righe non esplode', () => {
    expect(suggerimentiSenzaRiga([], [])).toEqual([]);
    expect(suggerimentiSenzaRiga([voce('Spesa', 300)], [])).toHaveLength(1);
  });
});
