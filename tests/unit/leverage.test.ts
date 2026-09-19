import { describe, expect, it } from 'vitest';
import { colonneFerme } from '@/lib/fire-leverage';

/*
 * Cinque righe della stessa leva: quali colonne non si muovono.
 *
 * E' la decisione che il componente prende per non disegnare una tabella di
 * numeri identici, e si prova qui perche' e' una decisione e non un disegno:
 * il caso vero e' l'eta' di ritiro, che gli anni non li muove mai.
 */

const riga = (yearsLeft: number | null, capitalNeeded: number) => ({ yearsLeft, capitalNeeded });

describe('colonne ferme di una leva', () => {
  it('cinque righe identiche: non si muove niente', () => {
    // Il caso segnalato: eta' di ritiro senza flussi che partono dopo il ritiro.
    const righe = [48, 49, 50, 51, 52].map(() => riga(24, 1_365_430));
    expect(colonneFerme(righe)).toEqual({ anni: true, capitale: true });
  });

  it('gli anni fermi e il capitale che si muove: si toglie la colonna degli anni', () => {
    // E' il caso con una pensione che parte dopo il ritiro: il ponte si accorcia
    // e il capitale scende, ma la data dell'indipendenza non si sposta.
    const righe = [riga(9, 479_621), riga(9, 447_213), riga(9, 500_000)];
    expect(colonneFerme(righe)).toEqual({ anni: true, capitale: false });
  });

  it('chi non arriva mai lo dice in tutte le righe: la colonna e\' ferma lo stesso', () => {
    const righe = [riga(null, 900_000), riga(null, 900_000), riga(null, 1_200_000)];
    expect(colonneFerme(righe).anni).toBe(true);
  });

  it('una riga che arriva e una che non arriva sono due valori diversi', () => {
    // `null` non e' "nessun valore da confrontare": e' la risposta "mai", ed e'
    // diversa da un numero.
    const righe = [riga(12, 900_000), riga(null, 900_000)];
    expect(colonneFerme(righe).anni).toBe(false);
  });

  it('quando si muove tutto non c\'e\' niente da togliere', () => {
    const righe = [riga(24, 1_500_000), riga(20, 1_400_000), riga(15, 1_200_000)];
    expect(colonneFerme(righe)).toEqual({ anni: false, capitale: false });
  });
});
