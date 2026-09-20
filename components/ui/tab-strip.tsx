'use client';

/**
 * La striscia che sceglie una cosa fra poche: le schede di una sezione, o le
 * pillole di un intervallo.
 *
 * Erano dieci controlli diversi per la stessa scelta - quattro contenitori,
 * tre modi di segnare la voce accesa - e in tre non si capiva nemmeno quale
 * fosse accesa: il colore lo diceva a chi guarda, non a chi legge lo schermo.
 * Qui la forma e' una sola per famiglia, e la voce accesa e' detta due volte:
 * `aria-pressed` per chi legge, il colore per chi guarda.
 *
 * `label` non e' facoltativo: un gruppo di pulsanti senza nome e' un gruppo di
 * pulsanti che non si sa cosa sceglie.
 *
 * Le due famiglie sono due perche' le due scelte sono diverse: le schede
 * cambiano quello che si vede sotto (e stanno da sole, larghe), le pillole
 * scelgono un parametro dentro una card (e stanno strette accanto a un'altra
 * scritta). Non sono una striscia sola con cinque misure.
 */
export function TabStrip<T extends string | number>({ id, label, value, options, onChange, variant = 'schede' }: {
  id?: string;
  label: string;
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
  variant?: 'schede' | 'pillole';
}) {
  const schede = variant === 'schede';
  return (
    <div id={id} role="group" aria-label={label}
      className={schede
        ? 'flex flex-wrap gap-2 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm'
        : 'flex gap-1 rounded-lg border border-black/6 bg-[#f4f5f1] p-1 text-xs'}>
      {options.map(([voce, etichetta]) => (
        <button key={voce} type="button" aria-pressed={voce === value} onClick={() => onChange(voce)}
          className={schede
            ? `rounded-lg px-3.5 py-2 text-sm font-medium transition ${voce === value ? 'bg-[var(--money-deep)] text-white' : 'text-[#5e6c68] hover:bg-[#f0f2ee]'}`
            : `rounded-md px-2.5 py-1 font-medium transition ${voce === value ? 'bg-white text-[#173b33] shadow-sm' : 'text-[#5e6c68] hover:text-[#173b33]'}`}>
          {etichetta}
        </button>
      ))}
    </div>
  );
}
