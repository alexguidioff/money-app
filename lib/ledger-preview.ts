export type LedgerRigaTipo = 'Buy' | 'Sell' | 'Dividend' | 'Fee' | 'Deposit' | 'Withdrawal';
export type RigaOperazione = { transactionType: LedgerRigaTipo; units: string; price: string; fee: string; amount?: string };

// Dividendi, commissioni, versamenti e prelievi non hanno quote: il denaro che
// muovono e' il loro importo, non quote per prezzo. Tenerli qui dentro evita
// che una riga di solo contante risulti da zero euro e faccia sembrare
// l'operazione "non corrispondente" al movimento.
export const LEDGER_SENZA_QUOTE = ['Dividend', 'Fee', 'Deposit', 'Withdrawal'] as const;
const SOLO_CONTANTE: readonly string[] = LEDGER_SENZA_QUOTE;
const IN_ENTRATA: readonly LedgerRigaTipo[] = ['Sell', 'Dividend', 'Deposit'];

/**
 * Il denaro che le operazioni del ledger muovono, da confrontare con l'importo
 * del movimento: un acquisto costa prezzo piu' commissione, una vendita incassa
 * prezzo meno commissione. Prima ogni riga era trattata come una vendita, e un
 * acquisto con commissioni risultava sempre "non corrispondente".
 */
export function nettoOperazioni(rows: readonly RigaOperazione[]): number {
  return Math.abs(rows.reduce((acc, r) => {
    const fee = Number(r.fee || 0);
    if (SOLO_CONTANTE.includes(r.transactionType)) {
      const importo = Number(r.amount || 0);
      return acc + (IN_ENTRATA.includes(r.transactionType) ? importo : -importo);
    }
    const lordo = Number(r.units || 0) * Number(r.price || 0);
    return acc + (r.transactionType === 'Sell' ? -(lordo - fee) : lordo + fee);
  }, 0));
}
