import { translations, type TranslationKey } from './translations';

type Traduci = (key: TranslationKey, params?: Record<string, string>) => string;

// I nomi con cui il server dice quale campo manca a un movimento.
const CAMPI_MOVIMENTO: Record<string, TranslationKey> = {
  date: 'fieldDate', amount: 'fieldAmount', account: 'fieldAccount', destination: 'fieldDestinationAccount',
  category: 'fieldCategory', type: 'fieldType',
};

/** "Mancano dei campi obbligatori: Conto destinazione", dai nomi del server. */
export function campiMancanti(fields: string[] | undefined, t: Traduci): string {
  return t('movementIncomplete', { fields: (fields ?? []).map((campo) => t(CAMPI_MOVIMENTO[campo] ?? 'fieldType')).join(', ') });
}

/**
 * Il motivo per cui il server ha rifiutato una richiesta, tradotto.
 *
 * `detail` ha tre forme: una stringa, l'elenco degli errori di validazione di
 * FastAPI, o un oggetto con un codice nostro. Solo un codice che ha il suo
 * testo si mostra; una frase scritta dal server (ne scrive ancora qualcuna, in
 * italiano) o un codice che non conosciamo diventano `predefinito`. Prima
 * queste stringhe finivano a schermo cosi' com'erano, e chi usa l'app in
 * tedesco leggeva un codice o una frase italiana.
 */
export async function responseError(response: Response, t: Traduci,
                                    predefinito: TranslationKey = 'importExportFailed'): Promise<string> {
  if (response.status === 401) return t('downloadLoginRequired');
  if (response.status === 413) return t('uploadTooLarge');
  const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
  const detail = payload?.detail;
  const oggetto = detail && typeof detail === 'object' && !Array.isArray(detail)
    ? detail as { code?: string; fields?: string[] } : null;
  if (oggetto?.code === 'movementIncomplete') return campiMancanti(oggetto.fields, t);
  const code = typeof detail === 'string' ? detail : oggetto?.code;
  return t(code && Object.hasOwn(translations.it, code) ? code as TranslationKey : predefinito);
}

/**
 * Un'eccezione che porta una frase gia' tradotta, pronta da mostrare.
 *
 * Serve un tipo apposta perche' `catch (e) { setError(e.message) }` mostra
 * anche le eccezioni che non abbiamo scritto noi: quando la richiesta non parte
 * affatto, `fetch` fallisce con un TypeError il cui testo e' in inglese e nomina
 * `fetch`. `messaggioDaErrore` riconosce questa e tace le altre.
 */
export class MessaggioUtente extends Error {}

/** Il testo da mostrare per un'eccezione: il suo, se e' uno dei nostri. */
export function messaggioDaErrore(error: unknown, predefinito: TranslationKey, t: Traduci): string {
  return error instanceof MessaggioUtente ? error.message : t(predefinito);
}

export async function downloadFile(url: string, filename: string, t: (key: TranslationKey) => string) {
  const response = await fetch(url);
  if (!response.ok) throw new MessaggioUtente(await responseError(response, t));
  const blobUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}
