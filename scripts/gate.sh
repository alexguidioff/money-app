#!/bin/bash
# Gate di pubblicazione. Niente va in esecuzione finche' tutti i passi sono
# verdi: API e web nuovi si pubblicano insieme, alla fine.
#
#   scripts/gate.sh
#
# Serve Docker, pnpm (con `pnpm install` gia' fatto) e ruff. I test end-to-end
# girano su uno stack separato (porta 3011, dati in memoria): mai sui dati veri.
set -o pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
PORTA="${MONEY_WEB_PORT:-3010}"
mkdir -p tests/fixtures/contracts
# Un container usa-e-getta dall'immagine nuova, con variabili e mount del
# servizio (il workbook per i test di parita'), senza porte e senza toccare
# l'API in esecuzione.
CANDIDATA=(docker compose run --rm --no-deps -T -w /app -e PYTHONPATH=/app api)

echo "=== TYPECHECK ==="; npx tsc --noEmit || { echo "STOP typecheck"; exit 1; }; echo ok
echo "=== CONTROLLO STATICO BACKEND (nomi non definiti, variabili e import inutili) ==="
ruff check --no-cache --select F821,F822,F823,F811,F841,F401 --output-format concise backend/app || { echo "STOP controllo statico backend"; exit 1; }
echo "=== IMMAGINE API CANDIDATA (non ancora pubblicata) ==="
docker compose build api > /tmp/apib.log 2>&1 || { echo "STOP build api"; tail -12 /tmp/apib.log; exit 1; }
echo ok
echo "=== TEST FRONTEND ==="
pnpm vitest run tests/unit tests/contracts/richieste.test.ts > /tmp/vitest.log 2>&1 || { echo "STOP test frontend"; tail -30 /tmp/vitest.log; exit 1; }; grep -E "Tests +[0-9]" /tmp/vitest.log
echo "=== CONTRATTI: richieste del frontend ai gestori veri ==="
"${CANDIDATA[@]}" python -m tests.contratti richieste < tests/fixtures/contracts/richieste.json 2>/dev/null || { echo "STOP contratti richieste"; exit 1; }
echo "=== CONTRATTI: risposte vere contro i tipi del frontend ==="
"${CANDIDATA[@]}" python -m tests.contratti risposte > tests/fixtures/contracts/risposte.json 2>/tmp/risposte.err || { echo "STOP generazione risposte"; tail -5 /tmp/risposte.err; exit 1; }
pnpm vitest run tests/contracts/risposte.test.ts > /tmp/vitest-contratti.log 2>&1 || { echo "STOP contratti risposte"; tail -40 /tmp/vitest-contratti.log; exit 1; }; grep -E "Tests +[0-9]" /tmp/vitest-contratti.log
echo "=== SUITE BE COMPLETA (nessuna esclusione) ==="
"${CANDIDATA[@]}" python -m pytest tests/ -q > /tmp/pytest.log 2>&1
ESITO=$?
tail -1 /tmp/pytest.log
[ $ESITO -ne 0 ] && { echo "STOP: test rossi, non pubblico"; grep -E "^FAILED|^ERROR" /tmp/pytest.log | head -10; exit 1; }
# Chi ha un vecchio Money.xlsx montato vuole i test di parita' eseguiti davvero:
# se venissero saltati la suite sembrerebbe verde con otto controlli in meno.
if [ -f "${MONEY_XLSX_HOST_DIR:-./imports}/Money.xlsx" ]; then
  SALTATI=$(grep -oE "[0-9]+ skipped" /tmp/pytest.log | grep -oE "[0-9]+")
  [ "${SALTATI:-0}" -gt 1 ] && { echo "STOP: $SALTATI test saltati (atteso 1): il workbook c'e' ma i test di parita' non girano"; exit 1; }
fi
echo "=== BUILD WEB CANDIDATO ==="
pnpm build > /tmp/b.log 2>&1 || { echo "STOP build"; tail -20 /tmp/b.log; exit 1; }
grep -cE "built in" /tmp/b.log | sed 's/^/ fasi: /'
docker build -f Dockerfile.runtime-patch -t money-app-web:latest . > /tmp/webb.log 2>&1 || { echo "STOP build web"; tail -5 /tmp/webb.log; exit 1; }
echo "=== END-TO-END (stack di test su 3011, mai sui dati veri) ==="
tests/e2e/esegui.sh > /tmp/e2e.log 2>&1 || { echo "STOP end-to-end: non pubblico"; grep -E "✘|Error:|console:|pagina:|waiting for" /tmp/e2e.log | head -20; exit 1; }
grep -E "[0-9]+ passed" /tmp/e2e.log
echo "=== PUBBLICAZIONE: API e web insieme ==="
# Ricreati sempre: con i servizi costruiti in locale `up -d` puo' lasciare in
# esecuzione il container vecchio anche se l'immagine e' cambiata.
docker compose up -d --force-recreate api web > /tmp/up.log 2>&1 || { echo "STOP pubblicazione"; tail -8 /tmp/up.log; exit 1; }
for servizio in api web; do
  attesa=$(docker image inspect -f '{{.Id}}' "money-app-$servizio:latest")
  in_uso=$(docker inspect -f '{{.Image}}' "money-app-$servizio-1")
  [ "$attesa" != "$in_uso" ] && { echo "STOP: $servizio non gira sull'immagine appena provata"; exit 1; }
done
for i in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$PORTA/)" = 200 ] \
    && [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$PORTA/health)" = 200 ] && break
  sleep 1
done
curl -s -o /dev/null -w " $PORTA: %{http_code}\n" --max-time 15 http://127.0.0.1:$PORTA/
curl -s -o /dev/null -w " API: %{http_code}\n" --max-time 15 http://127.0.0.1:$PORTA/health
echo " immagini in uso = immagini provate"
echo "=== FATTO ==="
