'use client';

import { ErrorFallback } from '@/components/error-fallback';

/**
 * Il confine d'errore delle pagine: `reset` rimonta il pezzo che si e' rotto
 * senza ricaricare tutto, che e' quello che serve quando il guasto e' di un
 * disegno solo.
 */
export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorFallback onRetry={reset} />;
}
