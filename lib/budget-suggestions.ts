/**
 * Le categorie che hanno speso ma non hanno ancora una riga di budget.
 *
 * Sono quelle per cui il suggerimento serve di piu': la riga, quando c'e' gia',
 * il numero ce l'ha e il suggerimento e' solo un'alternativa. Il confronto e'
 * quello del resto dell'app (senza spazi ai bordi, senza maiuscole), perche' la
 * categoria scritta a mano e quella dell'albero sono la stessa cosa scritta due
 * volte, e una differenza di spazi non deve far comparire due volte la stessa
 * voce.
 *
 * Prima le piu' grosse: se le categorie senza riga sono tante, quelle che
 * contano si vedono subito. La mediana a zero resta fuori: vuol dire che nella
 * maggior parte dei mesi li' dentro non hai speso niente.
 */
export function suggerimentiSenzaRiga<T extends { category: string; median: number }>(
  suggestions: readonly T[],
  righe: readonly { category: string }[],
): T[] {
  const conRiga = new Set(righe.map((riga) => riga.category.trim().toLowerCase()));
  return suggestions
    .filter((voce) => voce.median > 0 && !conRiga.has(voce.category.trim().toLowerCase()))
    .sort((a, b) => b.median - a.median);
}
