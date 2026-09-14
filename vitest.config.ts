import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Separata da vite.config.ts: quella carica il plugin Cloudflare e l'ambiente
// di hosting, che ai test non servono e li rallenterebbero.
export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('.', import.meta.url)) } },
  test: {
    include: ['tests/unit/**/*.test.ts', 'tests/contracts/**/*.test.ts'],
    environment: 'node',
    watch: false,
  },
});
