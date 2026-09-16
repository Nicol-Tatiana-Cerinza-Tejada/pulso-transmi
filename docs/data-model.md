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

La accuracy oficial es el promedio de las accuracies por estación. Una
predicción ausente se resuelve como predicción cero y queda marcada con
`was_missing = true`. Para entrar al ranking se exigirá una cobertura mínima del
95 %.
