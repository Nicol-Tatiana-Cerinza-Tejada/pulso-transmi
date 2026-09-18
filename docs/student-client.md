# Cliente de referencia: responsabilidades y secuencia

Este documento define el comportamiento mínimo de una solución estudiantil. No
impone librería, algoritmo ni estructura de repositorio.

## Estado que debe persistir cada estudiante

En Supabase:

- observaciones con llave única `(station_id, observed_at)`;
- último cursor confirmado del stream;
- ciclos vistos y estado de cada inferencia;
- `submission_id`, `client_run_id`, versión de modelo y commit;
- métricas propias, alertas de drift y decisiones de reentrenamiento.

En GitHub Actions Secrets:

- `PULSO_API_KEY`;
- credenciales privadas de Supabase.

La API key central no debe almacenarse en tablas, artifacts, logs, variables
`NEXT_PUBLIC_*` ni bundles de Vercel.

## Loop cada 30 minutos

1. Leer el cursor confirmado en Supabase.
2. Recorrer `/v1/stream/observations` hasta `next_cursor = null`.
3. Validar tipos, zona horaria, estaciones conocidas y valores no negativos.
4. Hacer `upsert` de las observaciones y confirmar el nuevo cursor.
5. Consultar `/v1/forecast-cycles/current`.
6. Si responde 404, terminar exitosamente: puede no ser hora de inferencia.
7. Si el ciclo ya tiene un recibo aceptado, no repetirlo salvo corrección explícita.
8. Cargar el modelo promovido y construir exactamente los targets solicitados.
9. Validar localmente que no falten pares `(station_id, target_at)`.
10. Enviar con una `Idempotency-Key` estable, por ejemplo
    `${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${cycle_id}`.
11. Guardar el recibo antes de terminar el job.

## Qué debe pasar si algo falla

| Falla | Conducta del pipeline |
|---|---|
| API temporalmente indisponible | reintentar con backoff y la misma llave |
| 404 sin ciclo abierto | terminar correctamente, sin submission |
| 401 | fallar y avisar; revisar secret sin imprimirlo |
| 409 ciclo cerrado | registrar como entrega perdida; no alterar timestamps |
| 409 idempotency conflict | fallar: el cliente reutilizó mal la llave |
| 422 target set | fallar antes de reintentar; corregir construcción del payload |
| 429 | respetar backoff; no crear nuevas llaves para evadir el límite |

## Entrenamiento y promoción

Entrenar y predecir pueden ser workflows diferentes. El entrenamiento produce un
artefacto identificable y solo promueve un modelo que supera la validación
temporal definida por el estudiante. La inferencia consume el modelo promovido; no
debe entrenar desde cero en cada ciclo salvo que esa sea una decisión medida y
justificada.

MLflow es opcional y suma valor como trazabilidad. Una alternativa gratuita es
guardar metadata y métricas en Supabase y el artefacto pequeño como GitHub
Release. No se deben confirmar binarios grandes repetidamente en Git.

## Dashboard opcional en Vercel

El bono puede mostrar:

- datos nuevos y retraso del collector;
- versión de modelo promovida y último entrenamiento;
- error por estación y horizonte;
- indicadores de drift y decisión de reentrenamiento;
- cobertura, accuracy y posición desde el leaderboard autenticado.

La consulta desde Vercel debe ocurrir en una función de servidor con
`PULSO_API_KEY`, nunca desde el navegador ni una variable `NEXT_PUBLIC_*`.
El dashboard es observabilidad, no el lugar donde corre el entrenamiento ni la
inferencia programada. Esas tareas permanecen en GitHub Actions.
