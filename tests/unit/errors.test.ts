import { describe, expect, it } from 'vitest';
import { responseError } from '@/lib/download';
import { messaggioErroreFire } from '@/lib/fire-errors';
import { messaggioErroreRegola } from '@/lib/rule-errors';
import { translations, type TranslationKey } from '@/lib/translations';

// Una `t` che restituisce il testo italiano: il test controlla quale chiave si
// sceglie, con la frase vera, non una chiave qualunque.
const t = (chiave: TranslationKey) => String(translations.it[chiave]);
const risposta = (status: number, corpo: unknown) =>
  new Response(JSON.stringify(corpo), { status, headers: { 'Content-Type': 'application/json' } });

describe('messaggi d\'errore di import ed export', () => {
  it('un codice noto del server diventa la sua frase', async () => {
    expect(await responseError(risposta(400, { detail: 'uploadEmpty' }), t)).toBe(t('uploadEmpty'));
    expect(await responseError(risposta(400, { detail: { code: 'importInvalid', reason: 'x' } }), t)).toBe(t('importInvalid'));
  });

  it('un movimento incompleto dice quali campi mancano, con i loro nomi', async () => {
    const conParametri = (chiave: TranslationKey, params?: Record<string, string>) =>
      String(translations.it[chiave]).replace(/\{\{(\w+)\}\}/g, (_, nome: string) => params?.[nome] ?? '');
    const corpo = { detail: { code: 'movementIncomplete', fields: ['destination', 'category'] } };
    expect(await responseError(risposta(422, corpo), conParametri)).toBe('Mancano dei campi obbligatori: Conto destinazione, Categoria.');
  });

  it('login mancante e file troppo grande hanno la loro frase anche senza corpo', async () => {
    expect(await responseError(new Response('', { status: 401 }), t)).toBe(t('downloadLoginRequired'));
    expect(await responseError(new Response('', { status: 413 }), t)).toBe(t('uploadTooLarge'));
  });

  it('un codice sconosciuto, un testo libero o un corpo non JSON non arrivano a video', async () => {
    // "Errore salvataggio: ..." e' una frase italiana del server, non una chiave.
    expect(await responseError(risposta(500, { detail: 'Errore salvataggio: boom' }), t)).toBe(t('importExportFailed'));
    expect(await responseError(risposta(400, { detail: 'toString' }), t)).toBe(t('importExportFailed'));
    expect(await responseError(new Response('<html>', { status: 502 }), t)).toBe(t('importExportFailed'));
  });
});

describe('messaggi d\'errore FIRE', () => {
  it('i rifiuti del piano diventano frasi che dicono cosa correggere', async () => {
    expect(await messaggioErroreFire(risposta(409, { detail: 'fireStreamDuplicateName' }), t, 'fireStreamsSaveError'))
      .toBe(t('fireErrorDuplicateName'));
    expect(await messaggioErroreFire(risposta(422, { detail: 'fireEngine_stima_solo_per_rendita' }), t, 'fireStreamsSaveError'))
      .toBe(t('fireErrorEarlyOnlyAnnuity'));
  });

  it('un errore di validazione generico ripiega sul messaggio del modulo', async () => {
    // FastAPI manda un elenco per i campi non validi: non e' un codice nostro.
    expect(await messaggioErroreFire(risposta(422, { detail: [{ msg: 'field required' }] }), t, 'fireProfileSaveError'))
      .toBe(t('fireProfileSaveError'));
  });
});

describe('messaggi d\'errore delle regole di categorizzazione', () => {
  it('ogni rifiuto del server ha la sua frase, non un "non riuscito"', async () => {
    // Le sei risposte che le due rotte delle regole possono dare: se una non
    // fosse mappata, chi scrive una regola leggerebbe solo che non ha
    // funzionato, senza sapere cosa correggere.
    const codici = ['rulePatternRequired', 'ruleRegexInvalid', 'ruleCategoryUnknown',
      'ruleAmountRange', 'ruleLimitReached', 'ruleDuplicate'] as const;
    for (const codice of codici) {
      expect(await messaggioErroreRegola(risposta(422, { detail: codice }), t)).toBe(t(codice));
    }
  });

  it('un corpo che non e\' un codice nostro ripiega sul messaggio del modulo', async () => {
    expect(await messaggioErroreRegola(new Response('<html>', { status: 502 }), t)).toBe(t('ruleSaveError'));
    expect(await messaggioErroreRegola(risposta(422, { detail: [{ msg: 'field required' }] }), t))
      .toBe(t('ruleSaveError'));
  });
});
