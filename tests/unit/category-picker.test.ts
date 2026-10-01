import { describe, expect, it } from 'vitest';
import { posizioneNelRamo, ramiDelleCategorie, valoreDellaMadre, type CategoryNode } from '@/lib/category-picker';

// L'albero come lo manda il server: una radice con figli, una radice senza, e
// una figlia che compare anche come radice nel vocabolario piatto.
const ALBERO: CategoryNode[] = [
  { name: 'Housing', children: ['Rent/Mortgage', 'Utilities'] },
  { name: 'Transport', children: [] },
  { name: 'Salary', children: [] },
];

const SPESE = ['Housing', 'Rent/Mortgage', 'Utilities', 'Transport', 'Altra spesa'];
const ENTRATE = ['Salary', 'Rimborsi'];

describe('ramiDelleCategorie', () => {
  it('mostra le madri del verso, non le sessanta voci piatte', () => {
    const rami = ramiDelleCategorie(SPESE, ALBERO);
    // Le tre dell'albero piu' la categoria che l'albero non conosce.
    expect(rami.map((ramo) => ramo.madre)).toEqual(['Housing', 'Transport', 'Altra spesa']);
  });

  it('una figlia non compare anche come madre', () => {
    const rami = ramiDelleCategorie(SPESE, ALBERO);
    expect(rami.some((ramo) => ramo.madre === 'Utilities')).toBe(false);
  });

  it('una categoria che l\'albero non conosce resta sceglibile da sola', () => {
    const rami = ramiDelleCategorie(SPESE, ALBERO);
    const altra = rami.find((ramo) => ramo.madre === 'Altra spesa');
    expect(altra).toEqual({ madre: 'Altra spesa', sceglibile: true, figlie: [] });
  });

  it('la madre dell\'altro verso non compare', () => {
    const rami = ramiDelleCategorie(ENTRATE, ALBERO);
    expect(rami.map((ramo) => ramo.madre)).toEqual(['Salary', 'Rimborsi']);
  });

  it('una radice di cui il verso non accetta niente non compare', () => {
    const rami = ramiDelleCategorie(['Salary'], ALBERO);
    expect(rami).toEqual([{ madre: 'Salary', sceglibile: true, figlie: [] }]);
  });

  it('una radice spenta con le figlie accese resta, ma non sceglibile', () => {
    const rami = ramiDelleCategorie(['Rent/Mortgage', 'Utilities'], ALBERO);
    expect(rami).toEqual([{ madre: 'Housing', sceglibile: false, figlie: ['Rent/Mortgage', 'Utilities'] }]);
  });
});

describe('posizioneNelRamo', () => {
  const rami = ramiDelleCategorie(SPESE, ALBERO);

  it('una figlia ritrova la madre', () => {
    expect(posizioneNelRamo(rami, 'Rent/Mortgage')).toEqual({ madre: 'Housing', figlia: 'Rent/Mortgage' });
  });

  it('una madre sta da sola, senza figlia', () => {
    expect(posizioneNelRamo(rami, 'Housing')).toEqual({ madre: 'Housing', figlia: '' });
  });

  it('niente scelto: niente madre', () => {
    expect(posizioneNelRamo(rami, '')).toEqual({ madre: '', figlia: '' });
  });

  it('un nome fuori dall\'albero non fa sparire la scelta', () => {
    expect(posizioneNelRamo(rami, 'Sconosciuta')).toEqual({ madre: 'Sconosciuta', figlia: '' });
  });
});

describe('valoreDellaMadre', () => {
  it('una madre sceglibile si sceglie da sola', () => {
    const rami = ramiDelleCategorie(SPESE, ALBERO);
    expect(valoreDellaMadre(rami, 'Transport')).toBe('Transport');
  });

  it('una madre spenta entra dalla sua prima figlia', () => {
    const rami = ramiDelleCategorie(['Rent/Mortgage', 'Utilities'], ALBERO);
    expect(valoreDellaMadre(rami, 'Housing')).toBe('Rent/Mortgage');
  });

  it('tornare alla voce vuota vuol dire non aver scelto', () => {
    const rami = ramiDelleCategorie(SPESE, ALBERO);
    expect(valoreDellaMadre(rami, '')).toBe('');
  });
});
