# Evidencia de la fase de adaptación a cambios de demanda

Este documento explica cómo el pipeline cumple los seis puntos de evidencia
solicitados para la fase de drift. La estrategia usa validación temporal,
modelos independientes por estación y horizonte, y promoción controlada.

## 1. Continuidad de ingesta y submissions

`src/collector.py` persiste el cursor solamente después de guardar las
observaciones y registra cada ejecución en `collector_runs`. Los workflows
reintentan fallos temporales y comparten un lock para no competir por el
cursor.

`src/infer.py` consulta el ciclo vigente, respeta `data_cutoff`, conserva la
clave idempotente y los recibos de cada intento. `src/monitor.py` registra una
señal operacional cuando encuentra ejecuciones fallidas, collector atrasado,
submissions pendientes o ciclos sin una entrega aceptada.

Una submission aceptada no se considera error por no tener todavía ground
truth: la evaluación puede quedar pendiente hasta que la API revele los
targets.

## 2. Operación frente a cambio de demanda

`operational_failure` observa collector y submissions. No se mezcla con las
señales estadísticas.

`performance_drift` compara la accuracy por estación en tres ciclos revelados
consecutivos y alerta cuando la estación queda por debajo de 80 %.

`data_drift` calcula PSI por estación comparando las últimas 24 horas con los
28 días anteriores; el umbral usado es `PSI >= 0.20`.

La separación permite distinguir una falla de disponibilidad de un cambio real
en la distribución o en la relación temporal de la demanda.

## 3. Disparador y datos del entrenamiento

El workflow `snapshot-retrain.yml` revisa el estado cada dos horas (cron de
GitHub, que en la práctica puede atrasarse varias horas). Se entrena un
candidato cuando existe evidencia nueva de caída localizada o una señal de
drift abierta posterior al último modelo.

Antes de entrenar, `src/snapshot.py` crea un snapshot inmutable del contenido
de `observations`, lo guarda en Supabase Storage, calcula SHA-256 y registra
filas, estaciones y rango temporal en `dataset_snapshots`.

El candidato usa únicamente ese snapshot. Los modelos reciben más peso en las
observaciones recientes mediante una vida media de 14 días, sin descartar
completamente la historia. En inferencia se aplica además un ajuste de nivel
limitado entre 0.75 y 1.25: compara las últimas 2 horas disponibles con los
mismos slots de 7 días antes y nunca usa datos posteriores al cutoff.

## 4. Comparación temporal sin información futura

La validación usa ciclos horarios históricos. Para cada origen, las features y
rezagos se construyen con datos hasta el origen; el target futuro solo se usa
para medir después.

La selección compara LightGBM independiente por estación/horizonte contra
baselines naive, promedio móvil y patrón semanal. La inferencia real vuelve a
usar únicamente observaciones hasta el `data_cutoff` del ciclo abierto.

## 5. Evidencia para mantener, promover o retirar

Cada candidato queda registrado en `model_versions` con versión, commit,
snapshot asociado, métricas oficiales, rutas por estación y horizonte,
significancia y mínimo de accuracy por estación.

La promoción exige superar la referencia, mejorar el champion con margen,
pasar la prueba de significancia y producir predicciones válidas. El 85 % se
usa como objetivo y alerta, pero no bloquea una mejora comprobada: durante un
drift puede ser correcto promover un modelo inferior al 85 % si supera al
champion vigente. Los cambios se auditan en
`model_promotion_events`; las versiones anteriores quedan retiradas, no se
eliminan.

## 6. Antes, durante y después del cambio

Antes del cambio quedan los snapshots, métricas y leaderboard acumulados.
Durante el cambio queda la señal en `drift_signals`, con estación, score,
ventana, referencia y valor actual.

Después quedan el nuevo snapshot, el candidato, la decisión de promoción y
las métricas de los ciclos posteriores. El dashboard expone el historial de
snapshots, decisiones de retraining, señales abiertas, accuracy por estación y
salud del pipeline.

## Política elegida

El drift no reemplaza automáticamente el champion. Dispara una evaluación;
únicamente una mejora respaldada por validación y significancia se promueve.
Esto evita que una ventana ruidosa o un modelo sobreajustado reduzca la
accuracy de la competencia.
