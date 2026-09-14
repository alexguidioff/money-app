# Money

[Italiano](README.it.md) · [English](README.md) · [Deutsch](README.de.md) · **Español** · [Français](README.fr.md)

Una app de finanzas personales que funciona **en tu casa**: tus movimientos,
tus cuentas y tus inversiones se quedan en tu propia base de datos, en una
máquina que controlas tú. Ningún servicio externo ve tus números.

Nació para sustituir una hoja de cálculo que había ido creciendo durante años, y
conserva su obsesión: **los números tienen que cuadrar**. Cada total se calcula
a partir de los movimientos, nunca se copia de otro sitio, y los saldos
calculados se pueden comparar con los declarados para darte cuenta enseguida
de que algo no cuadra.

## Qué hace

- **Movimientos** — ingresos, gastos, transferencias entre cuentas,
  recurrencias. Importación de extractos bancarios en PDF o CSV, con vista previa
  antes de guardar: el PDF se lee aunque el banco no dibuje una tabla y solo
  alinee el texto en columnas.
- **Cuentas** — como un balance, divididas en bancos, activos, pasivos e
  inversiones, con el saldo calculado junto al esperado.
- **Presupuesto** — planificación por categoría y mes, comparación con lo
  gastado, evolución anual.
- **Objetivos** — cuánto falta, a qué ritmo, para cuándo.
- **Deudas** — préstamos y líneas de crédito con el cuadro de amortización
  teórico junto a lo que realmente se ha dispuesto, cargado y devuelto.
- **Patrimonio** — la serie mensual del patrimonio neto, también en otras
  divisas al tipo de cambio de ese mes, no al de hoy.
- **Inversiones** — cartera valorada a precios de mercado descargados desde los
  tickers, reparto por sector y por valor subyacente, evolución de cada
  instrumento con tus propias compras y ventas encima.
- **Jubilación y FIRE** — cuánto capital necesitas, en qué año llegas y qué lo
  mueve: tasa de ahorro, rentas y pensiones futuras, gastos que cambian en la
  jubilación, con notas fiscales de diez países europeos.
- **Notas** — notas libres para recordar por qué decidiste algo.
- **Juntos** — quien quiera comparte sus *totales* con las demás cuentas de la
  misma instalación. Nunca los movimientos, ni las categorías, ni las cuentas.
- **Avisos** — presupuestos superados, cotizaciones paradas, copias de seguridad
  que no se hacen. Se cierran y no vuelven.

Arriba en cada página hay un **"?"** que explica para qué sirve la página y qué
necesita para llenarse de números.

La interfaz está disponible en italiano, inglés, alemán, español y francés.

## Cómo se pone en marcha

Solo necesitas Docker.

```bash
git clone https://github.com/alexguidioff/money-app.git money
cd money
./setup.sh                    # crea el .env con dos contraseñas aleatorias
docker compose up -d --build
```

Después abre <http://localhost:3010> y **crea la primera cuenta** en la
pantalla que aparece. La app empieza vacía: no hay datos de nadie más.

Para añadir más personas, desde la misma pantalla de acceso: cada una tendrá
sus propios movimientos, separados. La contraseña es opcional: mientras nadie
ponga una, la app se abre sin pedir nada.

## Las dos contraseñas de la base de datos

No son las que usas para entrar en la app: son las claves con las que la app
habla con la base de datos, y **nadie tiene que escribirlas nunca**. `setup.sh`
genera dos al azar y las escribe en el archivo `.env`, que Docker lee al
arrancar.

Una persona solo las necesita para abrir la base de datos a mano o para
restaurar una copia de seguridad fuera de la app: en ambos casos se leen de
`.env`.

Si se pierde el `.env` con los datos todavía dentro, no se pierde nada: dentro
del contenedor la base de datos acepta la conexión local sin contraseña.

```bash
./setup.sh                                  # un .env nuevo, con contraseñas nuevas
docker compose exec db psql -U money -d money \
  -c "alter role money password '<la POSTGRES_PASSWORD del nuevo .env>'"
docker compose up -d                        # la otra la reescribe la app sola
```

## Eliminar a una persona

Desde **Ajustes → Cuenta**, y **solo la propia cuenta**: no existe un
administrador que pueda borrar los números de otra persona. Hay que escribir el
propio nombre para confirmar, y antes de eliminar, la app descarga los datos en
el formato de intercambio y hace una copia de seguridad de la base de datos; la
copia se queda también junto a las copias de seguridad, en el servidor.

## Cómo se separan los datos entre personas

No por la disciplina de las consultas, sino **por la base de datos**. Cada
tabla con datos personales tiene una política de fila de PostgreSQL ligada al
usuario de la petición, y la app se conecta con un rol sin privilegios: una
consulta que olvide el filtro no ve los datos ajenos, simplemente no encuentra
nada.

Es una decisión deliberada. El riesgo de una app como esta no es romperse de
forma visible: es mezclar en silencio los números de dos personas.

## Dónde están los datos

En un volumen de Docker con PostgreSQL. No salen de ahí:

- **Copias de seguridad automáticas** cada día, más una antes de cada
  importación que sustituye los datos. Se restauran desde la interfaz, en
  Informes.
- **Exportación completa** en el "formato de intercambio": una hoja por entidad,
  fechas ISO, sin fórmulas. Sirve para llevártelo todo y cargarlo en otra
  instalación; hay un test que comprueba que el ciclo exportar-importar devuelve
  exactamente lo que tomó.

Lo único que sale a internet son las **cotizaciones** de los instrumentos que
has configurado con un ticker y los **tipos de cambio** de las divisas que
elijas.

## Instalarla en un NAS

La app está pensada para acabar en una máquina siempre encendida, accesible
desde los dispositivos de casa.

1. `MONEY_BIND_ADDRESS=0.0.0.0` en el `.env`
2. Pon una red privada delante: [Tailscale](https://tailscale.com) es la vía más
   sencilla: tráfico cifrado, ningún puerto abierto en el router y, con
   `tailscale serve`, también un certificado HTTPS de verdad
3. `MONEY_APP_ORIGIN=https://...` con la dirección desde la que abrirás la app.
   Sirve dos veces: la cookie de sesión detecta sola que está detrás de HTTPS, y
   la API solo acepta peticiones de `localhost`, `127.0.0.1` y de esta
   dirección; otro sitio web abierto en el mismo navegador no puede leer ni
   modificar tus datos
4. Construye las imágenes **en el NAS** (`docker compose up -d --build`), no las
   copies: la arquitectura puede ser distinta de la de tu ordenador

Sin una red privada delante, no la expongas: contraseñas y movimientos viajarían
sin cifrar por la red local. Pon una contraseña a cada cuenta: tras diez
intentos fallidos en un cuarto de hora, el acceso con ese nombre se bloquea
durante un rato.

## Cómo está hecha

- **API** — FastAPI y SQLAlchemy sobre PostgreSQL 17
- **Interfaz** — React (con vinext sobre Vite), en cinco idiomas
- **Gateway** — nginx delante de ambos, para que el navegador hable con un único origen

```bash
pnpm install
docker compose exec api python -m pytest tests/ -q   # tests del backend
pnpm test                                            # tests unitarios y contratos del frontend
pnpm test:e2e                                        # end-to-end con Playwright en un stack desechable
scripts/gate.sh                                      # todo seguido, y publica solo si todo está en verde
```

Los tests cubren el motor de cálculo, la lectura de los extractos, el ciclo
exportar-importar y los totales que en el pasado se rompieron en silencio: una
plantilla recurrente contada como gasto real, el capital invertido calculado
olvidando las posiciones vendidas. Los **contratos** comparan las respuestas
reales de la API con los tipos que lee la interfaz, y los cuerpos que envía la
interfaz con los que acepta la API: un campo renombrado en un solo lado detiene
el gate.

Los end-to-end (`pnpm test:e2e`, antes `pnpm exec playwright install chromium`)
se ejecutan en un proyecto de Compose separado, puerto 3011, base de datos en
memoria: nunca tocan la instalación que usas. Necesitan las imágenes
`money-app-api` y `money-app-web`, que crea `docker compose build`.

## Licencia

MIT.
