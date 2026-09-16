# Arquitectura

## Principio de seguridad

El futuro se almacena en `sim.generated_truth`, mientras que la API pública solo
puede consultar `competition.observations`. El scheduler copia una observación
cuando el reloj virtual alcanza su timestamp. El rol `academy_api` no tiene
permiso de lectura sobre el ground truth, las semillas ni la definición del drift.

## Componentes

1. **PostgreSQL:** fuente de verdad, datos generados, predicciones y scoring.
2. **API:** dataset inicial, stream incremental, ciclos, autenticación,
   submissions y leaderboard.
3. **Scheduler:** avanza el reloj cada 30 minutos, libera dos observaciones de
   15 minutos por estación, abre ciclos horarios, resuelve targets y toma snapshots.
4. **Caddy:** TLS, superficie pública y límite de 64 KB para submissions.

## Flujo operativo

```text
configuración privada
  -> compilador de escenario
  -> sim.generated_truth
  -> scheduler / reloj virtual
  -> competition.observations
  -> API
  -> predicciones
  -> score_components
  -> score_snapshots
  -> leaderboard
```

## Cadencia

```text
Cada 15 min virtuales     existe un target por estación
Cada 30 min reales       el scheduler libera 2 timestamps por estación
Cada 60 min virtuales    se abre un ciclo de pronóstico
Durante 25 min reales    se aceptan hasta 3 intentos por participante
Horizontes del ciclo     +15, +30, +45 y +60 min × 12 estaciones = 48 valores
Después del cierre       el último intento válido es el oficial
Al revelarse el target   se calcula error y se actualiza el leaderboard
```

La hora de GitHub Actions solo sirve para despertar el pipeline. El pipeline
siempre consulta `/v1/clock` y `/v1/forecast-cycles/current`; esto tolera retrasos
del cron gratuito y evita fabricar IDs o cutoffs.

## Camino de una submission

```text
Bearer API key ─► hash scrypt ─► participante y escenario
       │
       ▼
JSON estricto ─► ciclo abierto ─► target set exacto ─► transacción PostgreSQL
                                                        ├─ submission inmutable
                                                        ├─ 48 predicciones
                                                        ├─ puntero oficial
                                                        └─ evento de auditoría
```

La llave de idempotencia hace seguros los reintentos de GitHub Actions. Un mismo
contenido no se duplica; una llave reutilizada con contenido diferente se rechaza.

## Guardrails por capa

| Capa | Control |
|---|---|
| Caddy | TLS, solo rutas aprobadas, body máximo de 64 KB |
| FastAPI | JSON obligatorio, esquema sin campos extra, fechas con zona, rate limit |
| Autenticación | secreto mostrado una vez y almacenado como hash scrypt |
| Negocio | participante activo, ciclo abierto, cutoff exacto, máximo 3 intentos |
| Integridad | target set completo, constraints, unicidad e idempotencia |
| PostgreSQL | transacción atómica, intentos inmutables y auditoría |
| Privilegios | la API no puede leer `sim`, semillas, drift ni ground truth |

## Fallos y reintentos

- El collector usa `upsert` y persiste el cursor al final de su transacción.
- El envío usa una `Idempotency-Key` estable por run y ciclo.
- El scheduler usa un advisory lock por escenario y escrituras `ON CONFLICT`.
- Si API o scheduler reinician, PostgreSQL conserva reloj, ciclo, entrega oficial
  y resultados; no existe estado crítico solo en memoria.
- El rate limit en memoria protege errores accidentales. Los límites definitivos
  siguen siendo el tamaño en Caddy, los tres intentos y las constraints de BD.

## Límites iniciales del VPS

| Servicio | Memoria | CPU |
|---|---:|---:|
| API | 384 MiB | 0.35 |
| Scheduler | 192 MiB | 0.20 |
| PostgreSQL | 640 MiB | 0.40 |

No se utiliza Redis, Celery ni un broker. PostgreSQL coordina el scheduler con
advisory locks y es la única fuente de verdad.
