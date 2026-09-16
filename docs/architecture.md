# Arquitectura

## Principio de seguridad

El futuro se almacena en `sim.generated_truth`, mientras que la API pública solo
puede consultar `competition.observations`. El scheduler copia una observación
cuando el reloj virtual alcanza su timestamp. El rol `academy_api` no tiene
permiso de lectura sobre el ground truth, las semillas ni la definición del drift.

## Componentes

1. **PostgreSQL:** fuente de verdad, datos generados, predicciones y scoring.
2. **API:** lectura de datos liberados y recepción futura de submissions.
3. **Scheduler:** reloj, liberación, resolución y snapshots. En `0.1.0` solo emite heartbeat.
4. **Caddy:** terminación TLS. Se configura cuando exista un dominio aprobado.

## Flujo previsto

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

## Límites iniciales del VPS

| Servicio | Memoria | CPU |
|---|---:|---:|
| API | 384 MiB | 0.35 |
| Scheduler | 192 MiB | 0.20 |
| PostgreSQL | 640 MiB | 0.40 |

No se utiliza Redis, Celery ni un broker. La coordinación del scheduler usará
advisory locks de PostgreSQL.
