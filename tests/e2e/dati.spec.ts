import { expect, test, type Page } from '@playwright/test';

/*
 * I flussi che spostano dati in blocco: import di un estratto conto, copia di
 * sicurezza e ripristino, export e reimport completo. Sono quelli che fanno
 * piu' danni se si rompono, e che nessun test di contratto vede, perche'
 * passano da file, download e dialoghi del browser.
 *
 * In fila e dopo app.spec.ts: il ripristino e il reimport sostituiscono i dati.
 */
test.describe.configure({ mode: 'serial' });

async function apri(page: Page, voce: string) {
  await page.locator('aside').getByRole('button', { name: voce, exact: true }).click();
}

async function avvia(page: Page) {
  await page.goto('/');
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
}

async function movimenti(page: Page): Promise<string[]> {
  const risposta = await page.request.get('/api/transactions?limit=500');
  expect(risposta.ok()).toBe(true);
  return ((await risposta.json()) as { items: Array<{ details: string | null }> }).items.map((t) => t.details ?? '');
}

test('import di un estratto conto CSV: anteprima, conto per tutte le righe, conferma', async ({ page }) => {
  await avvia(page);
  await apri(page, 'Movimenti');
  const scelta = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Importa da CSV' }).click();
  await (await scelta).setFiles({
    name: 'estratto.csv', mimeType: 'text/csv',
    buffer: Buffer.from('data,descrizione,importo\n02/03/2026,Supermercato e2e,-45.20\n05/03/2026,Rimborso e2e,12.00\n06/03/2026,Bar e2e,-27.00\n'),
  });
  const anteprima = page.getByRole('dialog');
  await expect(anteprima.getByText('Supermercato e2e')).toBeVisible();
  await anteprima.getByLabel('Conto per tutte le righe').selectOption('Banca');
  // Stessi tipi del modulo "Nuovo movimento", senza il risparmio che non e' un movimento.
  const tipo = anteprima.getByLabel('Tipo', { exact: true }).first();
  await expect(tipo.locator('option')).toHaveText(['Spesa', 'Entrata', 'Trasferimento', 'Investimento', 'Debito']);
  // Le categorie si scelgono da un elenco che segue il tipo.
  const categoria = anteprima.getByLabel('Categoria', { exact: true }).first();
  await expect(categoria.locator('option', { hasText: 'Groceries' })).toHaveCount(1);
  await categoria.selectOption('Groceries');
  await tipo.selectOption('Income');
  await expect(categoria).toHaveValue('');
  await expect(categoria.locator('option', { hasText: 'Salary' })).toHaveCount(1);
  await expect(categoria.locator('option', { hasText: 'Groceries' })).toHaveCount(0);
  await tipo.selectOption('Expenses');
  await categoria.selectOption('Groceries');
  // Un importo letto male si corregge prima di salvare.
  const importo = anteprima.getByLabel('Importo', { exact: true }).first();
  await expect(importo).toHaveValue('45.2');
  await importo.fill('54.20');
  // Pagata per intero, ma solo in parte propria: la riga si divide in due, e le
  // due parti restano pari al totale mentre se ne corregge una.
  await anteprima.getByRole('button', { name: 'Dividi la riga 1' }).click();
  const importi = anteprima.getByLabel('Importo', { exact: true });
  await expect(importi).toHaveCount(4);
  await expect(importi.nth(0)).toHaveValue('27.1');
  await expect(importi.nth(1)).toHaveValue('27.1');
  await importi.nth(1).fill('20');
  await expect(importi.nth(0)).toHaveValue('34.2');
  await anteprima.getByLabel('Tipo', { exact: true }).nth(1).selectOption('Transfers');
  await anteprima.getByLabel('Conto destinazione', { exact: true }).first().selectOption('Broker');
  // Una divisione si puo' anche annullare.
  await anteprima.getByRole('button', { name: 'Dividi la riga 3' }).click();
  await expect(importi).toHaveCount(5);
  await anteprima.getByRole('button', { name: 'Annulla la divisione della riga 3' }).click();
  await expect(importi).toHaveCount(4);
  await expect(importi.nth(2)).toHaveValue('12');
  await anteprima.getByRole('button', { name: /Conferma e aggiungi \(4\)/ }).click();
  await expect(anteprima).toBeHidden();
  const salvati = await movimenti(page);
  expect(salvati.filter((d) => d === 'Supermercato e2e')).toHaveLength(2);
  expect(salvati).toContain('Rimborso e2e');
  await expect(page.getByText('34,20', { exact: false }).first()).toBeVisible();

  // Una spesa gia' salvata si divide dalla lista: meta' resta spesa, meta'
  // diventa un trasferimento. La lista mostra le uscite col segno meno, e la
  // divisione non deve confondersi.
  await page.getByRole('button', { name: 'Dividi Bar e2e' }).click();
  const divisione = page.getByRole('dialog');
  await expect(divisione.getByLabel('Importo della nuova parte')).toHaveValue('13.5');
  await divisione.getByLabel('Importo della nuova parte').fill('10');
  await expect(divisione.getByText(/Resta sull’originale: 17,00/)).toBeVisible();
  await divisione.getByLabel('Tipo').selectOption('Transfers');
  await divisione.getByLabel('Conto destinazione').selectOption('Broker');
  await divisione.getByRole('button', { name: 'Dividi', exact: true }).click();
  await expect(divisione).toBeHidden();
  const risposta = await page.request.get('/api/transactions?limit=500');
  const bar = ((await risposta.json()) as { items: Array<{ details: string | null; amount: number; transactionType: string }> }).items
    .filter((t) => t.details === 'Bar e2e').map((t) => [t.transactionType, Math.abs(t.amount)]).sort();
  expect(bar).toEqual([['Expenses', 17], ['Transfers', 10]]);
});

// Un elenco di Scalable scritto come lo incolla l'interfaccia: le colonne di un
// movimento sono separate da U+2028, non da spazi. Numeri tondi e nomi
// inventati.
const ELENCO_SCALABLE = [
  'lunedì, 3 febbraio 2020',
  'Piano di accumulo Fondo Alfa UCITS ETF 1C 10,00 az. €-100,00',
  'martedì, 4 febbraio 2020',
  'Piano di accumulo Fondo Alfa UCITS ETF Acc 5,00 az. €-60,00',
  'mercoledì, 5 febbraio 2020',
  'Deposito €300,00',
  'giovedì, 6 febbraio 2020',
  'Interesse €12,34',
  'venerdì, 7 febbraio 2020',
  // Un tipo che l'interfaccia non usa per i movimenti: si scarta e si dice.
  'Commissione €1,00',
].join('\n');

test('import di un elenco Scalable: anteprima coi rifiuti, conferma, conti creati', async ({ page }) => {
  await avvia(page);
  await apri(page, 'Investimenti');
  await page.getByRole('button', { name: 'Registro', exact: true }).click();
  await page.getByRole('button', { name: 'Importa da Scalable' }).click();
  const riquadro = page.getByRole('dialog');
  await riquadro.getByRole('textbox').fill(ELENCO_SCALABLE);
  await riquadro.getByRole('button', { name: 'Anteprima importazione' }).click();

  await expect(riquadro.getByText('5 righe lette · 4 da importare · 1 rifiutate')).toBeVisible();
  // Su chi si sta scrivendo: senza, un import fatto guardando lo schermo di
  // un'altra persona finirebbe su quella persona.
  await expect(riquadro.getByText(/^Stai importando su: /)).toBeVisible();
  // La riga che il lettore non riconosce si dice col suo motivo: uno scarto
  // silenzioso sarebbe denaro che sparisce senza che nessuno lo sappia.
  await expect(riquadro.getByText('Tipo di movimento non riconosciuto')).toBeVisible();
  // Due grafie dello stesso fondo restano una riga sola da confermare, ed e'
  // quello che tiene insieme la posizione invece di sdoppiarla.
  await expect(riquadro.getByLabel('Strumento')).toHaveCount(1);

  await riquadro.getByRole('button', { name: /Conferma e aggiungi \(4\)/ }).click();
  await expect(riquadro).toBeHidden();
  await expect(page.getByText(/^Importati 4 movimenti, 1 righe rifiutate\.$/)).toBeVisible({ timeout: 20_000 });

  // I tre conti dell'import: il contante del broker, i titoli, e quello dove
  // finisce il denaro che in banca non e' mai arrivato.
  const conti = (await (await page.request.get('/api/accounts')).json()) as { items: Array<{ name: string }> };
  expect(conti.items.map((c) => c.name)).toEqual(expect.arrayContaining(['Scalable', 'Scalable investimenti', 'Versamenti non tracciati']));
});

test('Quotazioni: il bottone per scaricare lo storico dei prezzi c\'e\'', async ({ page }) => {
  // Il rendimento (TWR) resta vuoto finche' manca una quotazione di fine mese,
  // e i mesi passati li scrive solo questa rotta: l'aggiornamento salva la
  // quotazione di adesso e basta. Senza un bottone, chi legge "manca una
  // quotazione di fine mese" non ha dove chiedere il pezzo che manca.
  // Non si preme: chiamerebbe la fonte per davvero, un minuto di rete dentro
  // il gate. Che la richiesta parta lo dice il codice, non questa prova.
  await avvia(page);
  await apri(page, 'Investimenti');
  await page.getByRole('button', { name: 'Quotazioni', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Scarica lo storico prezzi' })).toBeVisible();
});

test('export e reimport completo: i dati tornano uguali', async ({ page }, info) => {
  await avvia(page);
  await apri(page, 'Report');
  const prima = (await movimenti(page)).sort();
  const scaricamento = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Esporta dati' }).click();
  const file = info.outputPath('export.xlsx');
  await (await scaricamento).saveAs(file);

  await page.locator('input[type="file"][accept=".xlsx"]').setInputFiles(file);
  page.once('dialog', (dialogo) => void dialogo.accept());
  await page.getByRole('button', { name: 'Sostituisci i dati' }).click();
  await expect(page.getByText(/^Importate \d+ righe\.$/)).toBeVisible({ timeout: 20_000 });
  expect((await movimenti(page)).sort()).toEqual(prima);
});

test('copia di sicurezza e ripristino: quello scritto dopo la copia sparisce', async ({ page }) => {
  await avvia(page);
  await apri(page, 'Report');
  await page.getByRole('button', { name: 'Crea una copia ora' }).click();
  await expect(page.getByText('Copia creata.')).toBeVisible({ timeout: 20_000 });

  const nota = await page.request.post('/api/notes', { data: { section: 'Appunti', title: 'Nota da perdere', body: '' } });
  expect(nota.ok()).toBe(true);

  // Le copie sono in ordine dalla piu' recente: la prima e' quella appena creata.
  await page.getByRole('button', { name: 'Ripristina', exact: true }).first().click();
  await page.getByRole('button', { name: 'Confermi il ripristino?' }).click();
  await expect(page.getByText(/Dati ripristinati dalla copia/)).toBeVisible({ timeout: 30_000 });

  const appunti = (await (await page.request.get('/api/notes')).json()) as { items: Array<{ title: string }> };
  expect(appunti.items.map((a) => a.title)).not.toContain('Nota da perdere');
});
