# Pulso TransMi

Plataforma central del primer proyecto de MLOps de Orbital Academy. Publicará
observaciones sintéticas de demanda en estaciones de TransMilenio, recibirá
pronósticos y construirá un leaderboard temporal.

## Estado

La versión `0.1.0` provisiona la infraestructura base:

- PostgreSQL 17 con separación entre catálogo, simulación privada, competencia y operación.
- API FastAPI con endpoints de salud y metadatos.
- Scheduler en modo heartbeat; todavía no avanza el reloj ni libera observaciones.
- Docker Compose con límites de CPU, RAM y logs.
- Esquema inicial, roles de mínimo privilegio y documentación operativa.

## Inicio local

```bash
cp .env.example .env
# Reemplazar todas las contraseñas de ejemplo.
docker compose up -d --build
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/ready
```

La base de datos no publica ningún puerto al host. La API escucha exclusivamente
en `127.0.0.1:8010`; la exposición HTTPS deberá hacerse mediante Caddy.

## Estructura

```text
app/                 API y scheduler
config/              ejemplos versionados de escenarios
database/init/       inicialización canónica de PostgreSQL
database/migrations/ cambios incrementales para bases existentes
docs/                arquitectura, modelo, generador y runbook
tests/               pruebas automatizadas
docker-compose.yml   stack del VPS
```

## Comandos operativos

```bash
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=100 scheduler
docker compose exec postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose down
```

No ejecutar `docker compose down -v` en un entorno con datos: elimina el volumen
de PostgreSQL.

## Documentación

- [Arquitectura](docs/architecture.md)
- [Modelo de datos](docs/data-model.md)
- [Generador de patrones](docs/pattern-generator.md)
- [Contrato inicial de API](docs/api-contract.md)
- [Runbook del VPS](docs/runbook.md)
