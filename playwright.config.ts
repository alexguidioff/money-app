import { defineConfig, devices } from '@playwright/test';

// Solo lo stack di test (compose.test.yaml, porta 3011): mai l'app vera su 3010.
const BASE_URL = 'http://127.0.0.1:3011';

export default defineConfig({
  testDir: 'tests/e2e',
  globalSetup: './tests/e2e/prepara.ts',
  // I test scrivono sullo stesso utente: in fila, cosi' uno non trova i dati
  // dell'altro a meta'.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [['list']],
  outputDir: 'test-results/e2e',
  use: {
    baseURL: BASE_URL,
    storageState: 'test-results/e2e-stato.json',
    locale: 'it-IT',
    timezoneId: 'Europe/Rome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
});
