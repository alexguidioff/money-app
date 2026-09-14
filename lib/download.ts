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

export async function responseError(response: Response, t: Traduci): Promise<string> {
  if (response.status === 401) return t('downloadLoginRequired');
  if (response.status === 413) return t('uploadTooLarge');
  const payload = await response.json().catch(() => null) as { detail?: string | { code?: string; fields?: string[] } } | null;
  const code = typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.code;
  if (code === 'movementIncomplete' && typeof payload?.detail === 'object') return campiMancanti(payload.detail.fields, t);
  return t(code && Object.hasOwn(translations.it, code) ? code as TranslationKey : 'importExportFailed');
}

export async function downloadFile(url: string, filename: string, t: (key: TranslationKey) => string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await responseError(response, t));
  const blobUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}
