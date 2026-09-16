# Runbook del VPS

## Ubicación

```text
/opt/pulso-transmi
```

La API publica únicamente `127.0.0.1:8010`. PostgreSQL no publica puertos.

La configuración activa de Caddy está versionada en
`deploy/caddy/pulso-transmi.caddy` e instalada como
`/etc/caddy/pulso-transmi.caddy`. Externamente solo expone `/health`, `/docs`,
`/openapi.json` y `/v1/*`; `/ready` permanece interno.

## Despliegue

```bash
cd /opt/pulso-transmi
git pull --ff-only origin main
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
curl --fail http://127.0.0.1:8010/ready
```

Instalar primero la configuración versionada de Caddy, validarla y luego recargar:

```bash
sudo cp deploy/caddy/pulso-transmi.caddy /etc/caddy/pulso-transmi.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## Diagnóstico

```bash
sudo docker compose logs --tail=100 api
sudo docker compose logs --tail=100 scheduler
sudo docker compose logs --tail=100 postgres
sudo docker compose exec postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## Backup

Antes de una migración o activación:

```bash
mkdir -p backups
sudo docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "backups/predeploy-$(date +%Y%m%d-%H%M%S).dump"
```

Debe programarse además un `pg_dump` diario y una copia fuera del VPS. Tener un
volumen Docker no equivale a tener backup. Los dumps contienen información de
competencia y no se publican en Git.

## Migraciones

Los scripts de `database/init/` solo actúan sobre un volumen nuevo. En una base
existente, aplicar cada migración pendiente explícitamente:

```bash
sudo docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < database/migrations/003_submission_protocol.sql
```

Comprobar después `ops.schema_migrations`. La migración `003` agrega trazabilidad
e idempotencia y revoca al rol API cualquier lectura de configuración privada.

## Participantes y API keys

Crear participantes desde el contenedor `scheduler`, cuyo rol puede escribir el
registro administrativo:

```bash
sudo docker compose exec scheduler python -m app.admin create-participant \
  --name "Nombre visible" --slug "equipo-01" --scenario "p1-2026"
```

La llave completa se muestra una sola vez. Entregarla por un canal privado; no
guardarla en logs, hojas públicas, issues ni commits. El estudiante la almacena
como GitHub Actions Secret `PULSO_API_KEY`. Para revocar, establecer
`competition.api_keys.revoked_at` y registrar la intervención.

## Activar un escenario

La competencia solo corre cuando tanto `sim.scenarios.state` como
`competition.scenario_clock.state` están en `running`. Activar después de validar
el escenario, crear participantes, probar backup y ejecutar un ensayo integral.
Para pausar sin perder datos, cambiar únicamente el reloj a `paused`; el API
seguirá sirviendo historia y recibos, pero no avanzará el tiempo virtual.

## Actualización

1. Crear backup.
2. Validar migraciones en una base temporal.
3. Construir la imagen.
4. Ejecutar las migraciones pendientes como `pulso_admin` y registrar su versión
   en `ops.schema_migrations`.
5. Reiniciar API y scheduler.
6. Verificar `/ready` y heartbeat.

Smoke test sin secretos:

```bash
curl --fail https://pulso-transmi.72-60-245-2.sslip.io/health
curl --fail https://pulso-transmi.72-60-245-2.sslip.io/v1/clock
curl --fail 'https://pulso-transmi.72-60-245-2.sslip.io/v1/leaderboard?window=cumulative'
curl -i https://pulso-transmi.72-60-245-2.sslip.io/v1/me  # debe ser 401
```

## Incidentes de submission

Solicitar al estudiante `X-Request-ID`, `submission_id`, `cycle_id`, status HTTP
y hora del run. Nunca solicitar que publique su API key. Consultar primero
`ops.audit_events`, luego `competition.submissions` y `ops.job_runs`. Una entrega
`superseded` es válida pero ya no es la oficial del ciclo; una entrega ausente
tras el cierre no se inserta retroactivamente.

## Recuperación

No usar `docker compose down -v`. Ante una falla de API, PostgreSQL puede seguir
operando y el reloj permanece pausado. El avance del reloj debe usar advisory
locks y transacciones cortas.
