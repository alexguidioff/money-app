import { describe, expect, it } from 'vitest';
import { valuteDellaPanoramica, valuteDelConto } from '@/lib/currency-options';

// L'elenco maestro sta in Patrimonio > Valute. Il conto e la Panoramica lo
// leggono tutti e due, ma non allo stesso modo: il primo non deve perdere la
// valuta che il conto ha gia', il secondo non deve offrire una valuta di cui
// non si conosce il cambio.
describe('le valute del conto', () => {
  it('sono quelle dell\'elenco maestro', () => {
    expect(valuteDelConto(['EUR', 'CHF'], undefined)).toEqual(['EUR', 'CHF']);
  });

  it('tengono la valuta che il conto ha gia\'', () => {
    // Il conto e' in yen e lo yen non e' piu' nell'elenco: la voce deve esserci
    // lo stesso, o salvare il conto gliela cambia.
    expect(valuteDelConto(['EUR', 'CHF'], 'JPY')).toEqual(['EUR', 'CHF', 'JPY']);
  });

  it('non ripetono una valuta che c\'e\' gia\'', () => {
    expect(valuteDelConto(['EUR', 'CHF'], 'CHF')).toEqual(['EUR', 'CHF']);
  });

  it('senza impostazioni restano sull\'euro', () => {
    expect(valuteDelConto(undefined, undefined)).toEqual(['EUR']);
  });
});

describe('le valute della Panoramica', () => {
  it('l\'euro c\'e\' sempre e sta primo', () => {
    expect(valuteDellaPanoramica(['CHF', 'EUR'], ['CHF'])).toEqual(['EUR', 'CHF']);
    expect(valuteDellaPanoramica(undefined, undefined)).toEqual(['EUR']);
  });

  it('offrono solo le valute di cui si conosce il cambio', () => {
    // Il franco c'e' nell'elenco ma lo storico dei cambi non e' ancora arrivato:
    // chiederlo darebbe un errore, quindi la pillola non compare.
    expect(valuteDellaPanoramica(['USD', 'CHF'], ['CHF'])).toEqual(['EUR', 'CHF']);
  });

  it('senza il patrimonio caricato le offrono tutte', () => {
    expect(valuteDellaPanoramica(['USD', 'CHF'], undefined)).toEqual(['EUR', 'USD', 'CHF']);
  });
});
