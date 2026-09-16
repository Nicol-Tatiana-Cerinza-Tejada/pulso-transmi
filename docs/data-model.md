# Modelo de datos

## Esquemas

### `catalog`

Contiene únicamente identidad y geolocalización de estaciones. `station_id` es
texto para preservar ceros iniciales.

### `sim`

Zona privada con versiones del generador, configuración, parámetros ocultos,
eventos, drift y futuro precalculado. Nunca se consulta directamente desde un
endpoint público.

### `competition`

Contiene reloj, participantes, datos liberados, ciclos, submissions,
predicciones y resultados. Las entregas son inmutables; `cycle_entries` señala
cuál intento es oficial antes del cierre.

Relaciones principales:

```text
participants ─┬─ api_keys
              └─ participant_scenarios ── scenarios

forecast_cycles ─┬─ cycle_targets
                 ├─ submissions ── predictions
                 ├─ cycle_entries (puntero al intento oficial)
                 └─ score_components ── score_snapshots
```

`submissions` conserva cada intento aceptado y su hash. Reemplazar una entrega
solo cambia `cycle_entries.official_submission_id` y marca el intento anterior
como `superseded`; no borra la evidencia original.

### `ops`

Heartbeats, ejecuciones del scheduler y auditoría operacional.

## Volumen esperado

Con 12 estaciones, 52 días y frecuencia de 15 minutos se generan cerca de
60.000 observaciones. Con 30 estudiantes y cuatro horizontes por hora se esperan
aproximadamente 242.000 predicciones durante siete días. No se requiere
particionamiento en la primera versión.

## Índices

Los índices principales siguen los patrones reales de consulta:

- Observaciones por escenario y cursor de liberación.
- Ciclos por escenario y estado.
- Submissions por participante, ciclo y fecha.
- Predicciones por target y estación.
- Componentes de score por participante y tiempo.
- Snapshots por escenario, ventana y fecha descendente.

Todas las columnas que actúan como foreign key y aparecen en joins frecuentes
tienen un índice apropiado.

## Accuracy

Para cada estación y ventana:

```text
WAPE = sum(abs(real - predicción)) / sum(real)
Accuracy = 100 * max(0, 1 - WAPE)
```

La accuracy oficial es el promedio no ponderado de las accuracies por estación. Una
predicción ausente se resuelve como predicción cero y queda marcada con
`was_missing = true`. `coverage` es la fracción de targets con predicción y se
publica junto al score. La política académica puede usar el umbral de 95 % para
elegibilidad sin cambiar el cálculo reproducible.

## Ventanas de leaderboard

- `cumulative`: desde el inicio de competencia hasta el último tick resuelto.
- `rolling_24h`: últimas 24 horas virtuales, útil para observar recuperación ante drift.
- `current_cycle`: reservado en el esquema para diagnósticos posteriores.

Los snapshots son append-only. La vista `leaderboard_latest` selecciona el más
reciente por participante y ventana, lo que permite auditar la evolución sin
recalcular el pasado para cada consulta.
