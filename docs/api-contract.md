# Contrato de API `0.4.2`

Este documento es el contrato técnico de la plataforma central. Los clientes
deben descubrir el ciclo vigente en la API y nunca inferirlo a partir de la hora
local de una máquina o de un cron.

Base pública: `https://pulso-transmi.72-60-245-2.sslip.io`

## Convenciones

- Fechas y horas: ISO 8601 con zona horaria.
- IDs de estación: texto de cinco dígitos; no convertir a entero.
- Demanda: números finitos, no negativos y menores o iguales a `100000`.
- Respuestas dinámicas: `Cache-Control: no-store`.
- Cada respuesta incluye `X-Request-ID`; debe guardarse al diagnosticar errores.
- La API key va en `Authorization: Bearer $PULSO_API_KEY`, nunca en el JSON.

## Dataset inicial

`GET /v1/stations`, `GET /v1/observations`, `GET /v1/context` y
`GET /v1/downloads/{filename}` sirven el corte estático de entrenamiento. Este
corte no cambia y no contiene futuro de competencia. La paginación usa un
`next_cursor` opaco que solo debe copiarse a la petición siguiente.

## Stream incremental

### `GET /v1/stream/observations`

Entrega las observaciones liberadas por el reloj de competencia, en orden de
liberación. Acepta `cursor` y `limit` (1–5000).

```json
{
  "data": [{
    "station_id": "02300",
    "observed_at": "2026-09-16T10:15:00-05:00",
    "demand": 341,
    "released_at": "2026-09-16T10:30:03-05:00"
  }],
  "count": 1,
  "next_cursor": null,
  "server_time": "2026-09-16T10:30:04-05:00"
}
```

El collector debe hacer `upsert` por `(station_id, observed_at)` y guardar el
cursor solo después de confirmar la transacción en Supabase. Así, repetir una
página no duplica datos.

## Reloj y ciclo

### `GET /v1/clock`

Devuelve `virtual_now`, número de tick y estado. Sin escenario activo responde
`200` con `state: "waiting"`.

### `GET /v1/forecast-cycles/current`

Devuelve el ciclo abierto, su `data_cutoff`, cierre y el conjunto exacto de
targets. Sin ciclo abierto devuelve `404 no_open_cycle`. Esta respuesta es la
única fuente válida para construir una entrega.

```json
{
  "cycle_id": "cyc_p1_20260916T150000Z",
  "state": "open",
  "origin_at": "2026-09-16T10:00:00-05:00",
  "data_cutoff": "2026-09-16T10:00:00-05:00",
  "opens_at": "2026-09-16T10:00:02-05:00",
  "closes_at": "2026-09-16T10:25:02-05:00",
  "expected_predictions": 48,
  "targets": [{
    "station_id":"02300",
    "target_at":"2026-09-16T10:15:00-05:00",
    "horizon_minutes":15
  }]
}
```

## Identidad

El portal usa una sesión web temporal. Los scripts y GitHub Actions usan una API
key independiente. La identidad nunca se toma del JSON de una predicción.

### `POST /v1/portal/login`

Recibe `name`, `email` y `student_code`. Solo `email` y `student_code` se
normalizan y comparan mediante firmas criptográficas con la matrícula importada.
`name` es un nombre preferido de presentación: se conserva en la sesión temporal,
no autentica y no reemplaza el nombre oficial del leaderboard. En éxito crea una
cookie `HttpOnly`, `Secure` y `SameSite=Strict`; no devuelve la cédula ni sus
firmas. Los errores son genéricos y los intentos están limitados.

### `POST /v1/portal/api-key`

Requiere sesión web. Emite una sola credencial activa por estudiante y muestra
el secreto únicamente en la respuesta de creación. Si ya existe devuelve
`409 api_key_already_issued` sin revelar el secreto anterior.

### `POST /v1/portal/api-key/rotate`

Requiere sesión web y el body exacto `{"confirm_revoke": true}`. Revoca todas
las credenciales activas del estudiante y emite una nueva dentro de la misma
transacción. La llave anterior deja de autenticar inmediatamente y el secreto
nuevo solo aparece en esta respuesta. Se permiten como máximo cuatro emisiones
por estudiante en una hora, incluida la creación inicial.

### `GET /v1/portal/dashboard` y `GET /v1/portal/leaderboard`

Requieren sesión. El primero entrega identidad, prefijo de credencial, ronda y
recibos propios. El segundo muestra únicamente nombre, grupo, estado de
activación, entrega y métricas de la cohorte; nunca correo ni documento. Cada
participante incluye un `avatar_index` estable dentro de la cohorte y la respuesta
incluye `timeline`, con hasta 96 snapshots acumulados por participante
(`calculated_at`, `accuracy`, `coverage` y `rank`). Antes del primer score,
`timeline` es una lista vacía: el cliente debe presentar el estado de warmup sin
fabricar resultados.

### `GET /v1/me`

Requiere API key y devuelve la identidad resuelta en el servidor. El estudiante
no envía nombre, correo o equipo en una submission: esto evita suplantación y
mantiene un único identificador oficial.

## Enviar predicciones

### `POST /v1/submissions`

Headers obligatorios:

```http
Authorization: Bearer ptm_live_...secret...
Content-Type: application/json
Idempotency-Key: gha-123456789-1
```

Payload `1.0`:

```json
{
  "schema_version": "1.0",
  "cycle_id": "cyc_p1_20260916T150000Z",
  "client_run_id": "github-123456789-1",
  "data_cutoff": "2026-09-16T10:00:00-05:00",
  "model": {
    "version": "xgb-20260916.2",
    "trained_at": "2026-09-16T09:54:00-05:00",
    "training_data_end": "2026-09-16T10:00:00-05:00",
    "git_commit": "abcdef1234567"
  },
  "predictions": [{
    "station_id":"02300",
    "target_at":"2026-09-16T10:15:00-05:00",
    "value":321.5
  }]
}
```

Reglas de aceptación:

1. el ciclo existe, está abierto y no llegó a `closes_at`;
2. el participante está activo en ese escenario;
3. `data_cutoff` es idéntico al del ciclo;
4. `training_data_end` no supera el corte;
5. llegan todos los targets y solamente esos targets;
6. no hay extras, duplicados, `NaN`, infinitos ni negativos;
7. el body máximo es 64 KB y hay máximo tres intentos por ciclo;
8. el último intento válido reemplaza al anterior como entrega oficial.

Una entrega nueva devuelve `201`. Repetirla con igual `Idempotency-Key` devuelve
el mismo recibo con `200`, sin duplicarla. Reutilizar la llave con otro contenido
devuelve `409`.

```json
{
  "submission_id": "sub_5f...",
  "status": "accepted",
  "attempt": 2,
  "received_at": "2026-09-16T10:08:11-05:00",
  "closes_at": "2026-09-16T10:25:02-05:00",
  "predictions_received": 48,
  "expected_predictions": 48,
  "is_official": true,
  "payload_hash": "sha256:...",
  "replaced_submission_id": "sub_1a..."
}
```

### `GET /v1/submissions/{submission_id}`

Requiere la misma API key. Devuelve el recibo y si continúa siendo oficial;
nunca permite consultar entregas de otro participante.

## Leaderboard

### `GET /v1/leaderboard?window=cumulative`

Requiere API key. `window` acepta `cumulative` o `rolling_24h`. Publica nombre, tipo,
elegibilidad, accuracy, WAPE crudo, accuracy@20, cobertura, posición y fecha.
No publica API keys, parámetros de modelos ni predicciones individuales.

La métrica oficial calcula WAPE por estación, transforma cada resultado a
`100 × max(0, 1 − WAPE)` y promedia estaciones. Un target faltante se evalúa
como cero; la cobertura muestra la confiabilidad operacional.

## Errores relevantes

| HTTP | Código | Significado |
|---|---|---|
| 400 | `invalid_cursor` | Cursor alterado o ilegible |
| 401 | `invalid_api_key` | Falta, formato incorrecto, revocada o inválida |
| 403 | `participant_inactive` | Sin acceso activo al escenario |
| 404 | `cycle_not_found` / `no_open_cycle` | Ciclo inválido o sin ventana activa |
| 409 | `cycle_closed` | La ventana ya cerró |
| 409 | `idempotency_conflict` | Misma llave, payload diferente |
| 409 | `attempt_limit_reached` | Ya se consumieron tres intentos |
| 409 | `api_key_already_issued` | La credencial personal ya fue generada |
| 409 | `api_key_missing` | Se intentó rotar sin una credencial activa |
| 413 | `payload_too_large` | Body mayor a 64 KB |
| 415 | `unsupported_media_type` | No se envió JSON |
| 422 | `invalid_target_set` | Faltan targets, sobran o están repetidos |
| 429 | `rate_limited` | Más de diez intentos por minuto y API key |
| 429 | `api_key_rotation_rate_limited` | Se alcanzó el límite horario de emisiones |

Los errores de negocio se entregan bajo `detail.code`; los de esquema usan la
validación estándar de FastAPI. El cliente debe registrar status, body y
`X-Request-ID`, pero nunca imprimir la API key.
