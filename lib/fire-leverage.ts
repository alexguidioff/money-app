/*
 * Quali colonne della tabella delle leve non si muovono affatto.
 *
 * Non tutte le leve spostano tutte le colonne, e una colonna ferma non e' una
 * risposta: e' lo stesso numero cinque volte sotto un'intestazione che promette
 * una sensibilità. L'eta' di ritiro e' il caso vero: non sposta mai gli anni -
 * la data dell'indipendenza non dipende da quando si smette - e non sposta il
 * capitale finche' non c'e' un flusso che parte *dopo* il ritiro, perche' il
 * capitale necessario e' la riserva perpetua (spese diviso prelievo) e l'eta'
 * di ritiro la tocca solo attraverso il ponte verso la pensione.
 *
 * Sta qui e non nel componente perche' e' una decisione, non un disegno: cosi'
 * si prova senza montare niente.
 */

type Riga = { yearsLeft: number | null; capitalNeeded: number };

export function colonneFerme(righe: readonly Riga[]): { anni: boolean; capitale: boolean } {
  // Cinque `null` sono una colonna ferma, non cinque valori diversi: chi non
  // arriva nell'orizzonte lo dice allo stesso modo in tutte le righe.
  const ferma = (valori: readonly (number | null)[]) => new Set(valori).size <= 1;
  return {
    anni: ferma(righe.map((riga) => riga.yearsLeft)),
    capitale: ferma(righe.map((riga) => riga.capitalNeeded)),
  };
}
