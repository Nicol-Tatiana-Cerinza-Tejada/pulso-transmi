# Contrato de API `0.2.0`

La versión `0.2.0` sirve un corte estático de entrenamiento. Todas las rutas de
esta sección son públicas y de solo lectura. La API dinámica conservará las
formas de paginación, pero podrá añadir campos compatibles.

## Operación

### `GET /health`

Liveness del proceso; no consulta PostgreSQL.

### `GET /ready`

Comprueba PostgreSQL y reporta la versión del dataset cargado.

## Datos públicos

### `GET /v1/meta`

Devuelve versión de API, modo, manifiesto completo y enlaces de descubrimiento.
El manifiesto declara explícitamente `future_included: false`.

### `GET /v1/stations`

Devuelve las 12 estaciones con ID oficial tratado como texto, nombre, corredor,
latitud y longitud.

### `GET /v1/observations`

Parámetros opcionales:

| Parámetro | Tipo | Regla |
|---|---|---|
| `station_id` | texto | ID exacto de cinco caracteres |
| `start` | ISO 8601 con zona | Inclusive |
| `end` | ISO 8601 con zona | Inclusive |
| `cursor` | texto opaco | Cursor devuelto por la página anterior |
| `limit` | entero | 1–5.000; por defecto 1.000 |

Respuesta:

```json
{
  "data": [
    {
      "observed_at": "2026-07-26T00:00:00-05:00",
      "station_id": "02300",
      "demand": 313
    }
  ],
  "count": 1,
  "next_cursor": "WyIyMDI2..."
}
```

El cliente debe tratar `next_cursor` como opaco. Cuando sea `null`, terminó el
recorrido. No debe construir ni modificar cursores.

### `GET /v1/context`

Usa `start`, `end`, `cursor` y `limit` con las mismas reglas. Cada timestamp
contiene lluvia y temperatura observadas/pronosticadas e intensidad de evento.

### `GET /v1/downloads/{filename}`

Archivos permitidos:

- `stations.csv`
- `observations.csv`
- `context.csv`
- `metadata.json`

La respuesta incluye un `ETag` basado en SHA-256. Cualquier otro nombre devuelve
404 y no permite acceso arbitrario al filesystem.

## Errores

- `400`: cursor inválido.
- `404`: descarga inexistente.
- `422`: parámetros inválidos, rango invertido o límite fuera de rango.
- `503`: PostgreSQL no disponible en `/ready`.

## Planificado

```text
GET  /v1/clock
GET  /v1/forecast-cycles/current
POST /v1/submissions
GET  /v1/submissions/{public_id}
GET  /v1/leaderboard
GET  /v1/baselines
```

Las submissions requerirán API key, `cycle_id`, `model_version`, `data_cutoff`
y una predicción no negativa y finita para cada target requerido.
