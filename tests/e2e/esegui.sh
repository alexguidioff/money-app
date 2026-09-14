#!/bin/bash
# Test end-to-end su uno stack pulito: lo crea, esegue i test, lo distrugge.
# Mai sull'app vera: progetto Compose `money-test`, porta 3011, dati in memoria.
set -o pipefail
cd "$(dirname "$0")/../.."
STACK=(docker compose -p money-test -f compose.test.yaml)
"${STACK[@]}" down -v > /dev/null 2>&1
"${STACK[@]}" up -d --wait > /tmp/e2e-stack.log 2>&1 || { echo "STOP: stack di test non partito"; tail -20 /tmp/e2e-stack.log; "${STACK[@]}" down -v > /dev/null 2>&1; exit 1; }
pnpm exec playwright test "$@"
ESITO=$?
"${STACK[@]}" down -v > /dev/null 2>&1
exit $ESITO
