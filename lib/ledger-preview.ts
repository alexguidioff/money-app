export type RigaOperazione = { transactionType: 'Buy' | 'Sell'; units: string; price: string; fee: string };

/**
 * Il denaro che le operazioni del ledger muovono, da confrontare con l'importo
 * del movimento: un acquisto costa prezzo piu' commissione, una vendita incassa
 * prezzo meno commissione. Prima ogni riga era trattata come una vendita, e un
 * acquisto con commissioni risultava sempre "non corrispondente".
 */
export function nettoOperazioni(rows: readonly RigaOperazione[]): number {
  return Math.abs(rows.reduce((acc, r) => {
    const lordo = Number(r.units || 0) * Number(r.price || 0);
    const fee = Number(r.fee || 0);
    return acc + (r.transactionType === 'Sell' ? -(lordo - fee) : lordo + fee);
  }, 0));
}
