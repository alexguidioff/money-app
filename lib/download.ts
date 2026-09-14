import { translations, type TranslationKey } from './translations';

export async function responseError(response: Response, t: (key: TranslationKey) => string): Promise<string> {
  if (response.status === 401) return t('downloadLoginRequired');
  if (response.status === 413) return t('uploadTooLarge');
  const payload = await response.json().catch(() => null) as { detail?: string | { code?: string } } | null;
  const code = typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.code;
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
