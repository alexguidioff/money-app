/**
 * Il mese di competenza di un movimento, come lo calcola il backend
 * (`effective_date` in `calculation_engine.py`): con lo spostamento attivo, un
 * movimento dal giorno scelto in poi conta dal primo del mese successivo. Vale
 * sia per le entrate sia per le uscite: sono i due versi che il mese li chiude.
 * Il modulo lo mostra in anteprima prima di salvare.
 *
 * Si lavora sul testo della data e non su `Date`: passare da un orario locale
 * a `toISOString` sposta il giorno nei fusi lontani da UTC.
 */
export function previewEffectiveDate(occurredOn: string, transactionType: string, shift: string, day: number): string {
  if (!['income', 'expenses'].includes(transactionType.toLowerCase()) || shift.toLowerCase() !== 'active') return occurredOn;
  const parti = /^(\d{4})-(\d{2})-(\d{2})$/.exec(occurredOn);
  if (!parti || Number(parti[3]) < day) return occurredOn;
  const anno = Number(parti[1]);
  const mese = Number(parti[2]);
  return mese === 12 ? `${anno + 1}-01-01` : `${anno}-${String(mese + 1).padStart(2, '0')}-01`;
}
