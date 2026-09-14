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
    buffer: Buffer.from('data,descrizione,importo\n02/03/2026,Supermercato e2e,-45.20\n05/03/2026,Rimborso e2e,12.00\n'),
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
  await anteprima.getByRole('button', { name: /Conferma e aggiungi \(2\)/ }).click();
  await expect(anteprima).toBeHidden();
  expect(await movimenti(page)).toEqual(expect.arrayContaining(['Supermercato e2e', 'Rimborso e2e']));
  await expect(page.getByText('54,20', { exact: false }).first()).toBeVisible();
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
