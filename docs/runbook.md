# Runbook del VPS

## Ubicación

```text
/opt/pulso-transmi
```

La API publica únicamente `127.0.0.1:8010`. PostgreSQL no publica puertos.

La configuración candidata de Caddy vive en
`deploy/caddy/pulso-transmi.caddy`. No debe instalarse hasta recibir aprobación
explícita para publicar. Externamente solo expone `/health`, `/docs`,
`/openapi.json` y `/v1/*`; `/ready` permanece interno.

## Despliegue

```bash
cd /opt/pulso-transmi
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
curl --fail http://127.0.0.1:8010/ready
```

## Diagnóstico

```bash
sudo docker compose logs --tail=100 api
sudo docker compose logs --tail=100 scheduler
sudo docker compose logs --tail=100 postgres
sudo docker compose exec postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## Backup

Antes de activar la competencia debe programarse un `pg_dump` diario y una copia
fuera del VPS. Tener un volumen Docker no equivale a tener backup.

## Actualización

1. Crear backup.
2. Validar migraciones en una base temporal.
3. Construir la imagen.
4. Ejecutar las migraciones pendientes como `pulso_admin` y registrar su versión
   en `ops.schema_migrations`.
5. Reiniciar API y scheduler.
6. Verificar `/ready` y heartbeat.

## Recuperación

No usar `docker compose down -v`. Ante una falla de API, PostgreSQL puede seguir
operando y el reloj permanece pausado. El avance del reloj debe usar advisory
locks y transacciones cortas.
