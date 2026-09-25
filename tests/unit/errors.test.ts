import { describe, expect, it } from 'vitest';
import { responseError } from '@/lib/download';
import { messaggioErroreFire } from '@/lib/fire-errors';
import { messaggioErroreRegola } from '@/lib/rule-errors';
import { messaggioImportScalable, motivoRifiuto } from '@/lib/scalable-errors';
import { translations, type TranslationKey } from '@/lib/translations';

// Una `t` che restituisce il testo italiano: il test controlla quale chiave si
// sceglie, con la frase vera, non una chiave qualunque.
const t = (chiave: TranslationKey) => String(translations.it[chiave]);
// Una `t` che riempie i segnaposto: i messaggi che nominano la riga si provano
// con la frase vera, non col `{{row}}` letterale.
const conParametri = (chiave: TranslationKey, params?: Record<string, string>) =>
  String(translations.it[chiave]).replace(/\{\{(\w+)\}\}/g, (_, nome: string) => params?.[nome] ?? '');
const risposta = (status: number, corpo: unknown) =>
  new Response(JSON.stringify(corpo), { status, headers: { 'Content-Type': 'application/json' } });

describe('messaggi d\'errore di import ed export', () => {
  it('un codice noto del server diventa la sua frase', async () => {
    expect(await responseError(risposta(400, { detail: 'uploadEmpty' }), t)).toBe(t('uploadEmpty'));
    expect(await responseError(risposta(400, { detail: { code: 'importInvalid', reason: 'x' } }), t)).toBe(t('importInvalid'));
  });

  it('un movimento incompleto dice quali campi mancano, con i loro nomi', async () => {
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

describe('messaggi dell\'import di Scalable', () => {
  it('un motivo di scarto diventa la sua frase', () => {
    // Il codice del lettore e' corto e non e' una chiave: la frase si compone.
    expect(motivoRifiuto('coppiaInterna', t)).toBe(t('scalableReasonCoppiaInterna'));
    expect(motivoRifiuto('rowUnbalanced', t)).toBe(t('scalableReasonRowUnbalanced'));
    // Un motivo che non conosciamo non arriva a schermo cosi' com'e'.
    expect(motivoRifiuto('', t)).toBe(t('scalableReasonRigaSconosciuta'));
    expect(motivoRifiuto('qualcosaDiNuovo', t)).toBe(t('scalableReasonRigaSconosciuta'));
  });

  it('un fallimento riga per riga dice quale riga, e perche\'', async () => {
    const corpo = { detail: { code: 'scalableRowFailed', row: 42, cause: 'rigaSenzaQuote' } };
    expect(await messaggioImportScalable(risposta(422, corpo), conParametri))
      .toBe(conParametri('scalableRowFailed', { row: '42', cause: t('scalableReasonRigaSenzaQuote') }));
    // La quadratura si controlla dopo le righe, e la sua risposta porta il
    // numero ma non un motivo: anche quella si legge.
    expect(await messaggioImportScalable(risposta(422, { detail: { code: 'scalableRowUnbalanced', row: 7 } }), conParametri))
      .toBe(conParametri('scalableRowFailed', { row: '7', cause: t('scalableReasonRowUnbalanced') }));
  });

  it('un elenco gia\' importato e un utente diverso hanno la loro frase', async () => {
    // Sono due `detail` che portano gia' la chiave intera: `responseError` li sa
    // leggere, e senza di lui si vedrebbe un "non riuscito" generico.
    expect(await messaggioImportScalable(risposta(409, { detail: 'scalableAlreadyImported' }), t))
      .toBe(t('scalableAlreadyImported'));
    expect(await messaggioImportScalable(risposta(422, { detail: 'scalableUserMismatch' }), t))
      .toBe(t('scalableUserMismatch'));
    expect(await messaggioImportScalable(new Response('<html>', { status: 502 }), t)).toBe(t('scalableFailed'));
  });
});
