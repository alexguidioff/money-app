export type RigaScala = { anno: number; history?: number; central?: number; target?: number };

/**
 * Tacche degli assi del grafico FIRE.
 *
 * La scala segue la parte del grafico che conta - fino a cinque anni dopo
 * l'ultimo evento del piano - non la coda: con un prelievo basso la curva
 * ottimistica arriva a decine di milioni e schiacciava storia e obiettivo
 * contro lo zero. Quattro intervalli di un passo "tondo": un massimo grezzo
 * finiva come ultima tacca (€4.793.111). Gli anni vanno a decenni.
 */
export function scalaGrafico(rows: readonly RigaScala[], annoRitiro: number, annoPrimoFlusso: number | null,
                             annoIncrocio: number | null): { yTicks?: number[]; xTicks: number[] } {
  const annoFuoco = Math.max(annoRitiro, annoPrimoFlusso ?? annoRitiro, annoIncrocio ?? annoRitiro) + 5;
  const massimo = Math.max(0, ...rows.filter((row) => row.anno <= annoFuoco)
    .flatMap((row) => [row.history, row.central, row.target])
    .filter((value): value is number => value != null && Number.isFinite(value)));
  const potenza = massimo > 0 ? 10 ** Math.floor(Math.log10(massimo * 1.1 / 4)) : 1;
  const passo = potenza * ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((k) => k * potenza >= massimo * 1.1 / 4) ?? 10);
  const yTicks = massimo > 0 ? [0, 1, 2, 3, 4].map((i) => i * passo) : undefined;
  const primo = rows[0]?.anno;
  const ultimo = rows.at(-1)?.anno;
  const xTicks = primo != null && ultimo != null
    ? Array.from({ length: Math.max(0, Math.floor(ultimo / 10) - Math.ceil(primo / 10) + 1) },
                 (_, i) => (Math.ceil(primo / 10) + i) * 10)
    : [];
  return { yTicks, xTicks };
}
