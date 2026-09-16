# Contrato inicial de API

## Disponible en `0.1.0`

### `GET /health`

Liveness del proceso. No consulta PostgreSQL.

### `GET /ready`

Comprueba la conexión con PostgreSQL.

### `GET /v1/meta`

Devuelve nombre del proyecto, número de estaciones cargadas y escenario activo,
sin exponer configuración privada.

## Contrato planificado

```text
GET  /v1/clock
GET  /v1/stations
GET  /v1/observations?cursor=...
GET  /v1/forecast-cycles/current
POST /v1/submissions
GET  /v1/submissions/{public_id}
GET  /v1/leaderboard
GET  /v1/baselines
```

Las observaciones usarán paginación por cursor. Las submissions requerirán API
key, `cycle_id`, `model_version`, `data_cutoff` y una predicción para cada target
requerido.
