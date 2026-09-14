import type { Lang } from '@/lib/translations';

// Il server scrive i periodi in un formato fisso e italiano - "Gen 25",
// "Mag", "T1 25" - che finiva tale e quale sugli assi dei grafici anche con
// l'app in inglese o in tedesco. Qui diventano i mesi della lingua scelta.
const MESI_SERVER = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic'];
const TRIMESTRE: Record<Lang, string> = { it: 'T', en: 'Q', de: 'Q', es: 'T', fr: 'T' };

export function etichettaPeriodo(valore: unknown, mesiBrevi: readonly string[], lang: Lang): string {
  const testo = String(valore ?? '');
  const mese = /^([A-Z][a-z]{2})( \d{2})?$/.exec(testo);
  if (mese && MESI_SERVER.includes(mese[1])) return `${mesiBrevi[MESI_SERVER.indexOf(mese[1])]}${mese[2] ?? ''}`;
  const trimestre = /^T([1-4]) (\d{2})$/.exec(testo);
  if (trimestre) return `${TRIMESTRE[lang]}${trimestre[1]} ${trimestre[2]}`;
  return testo;
}
