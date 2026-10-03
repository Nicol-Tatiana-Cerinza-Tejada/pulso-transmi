# Informe técnico — Pulso TransMi

## 1. Hipótesis del EDA

1. La demanda presenta estacionalidad intradía, con diferencias entre horas
   valle y horas punta.
2. El mismo slot de la semana anterior es una señal fuerte para estaciones con
   comportamiento semanal estable.
3. Las estaciones con mayor demanda tienen mayor variabilidad absoluta, por lo
   que conviene comparar errores relativos.
4. Los outliers se concentran en estaciones o franjas específicas y no deben
   eliminarse automáticamente porque pueden representar cambios de régimen.
5. Combinar último valor, estacionalidad semanal y medias móviles debe superar a
   cada baseline aislado en horizontes de 15 a 60 minutos.

## 2. Qué funcionó

- El histórico se cargó con upsert por `(station_id, ts)` y timestamps UTC.
- El collector conserva el cursor solo después del upsert, por lo que repetir
  una página no duplica observaciones.
- La validación temporal evita mezclar futuro con entrenamiento.
- LightGBM con lags `1, 2, 4, 96, 672`, medias móviles, calendario y estación
  superó a los tres baselines.
- El artefacto se guardó con nombre versionado en Supabase Storage sin
  sobrescritura.
- El modelo quedó registrado y promovido como `champion`.
- La inferencia respeta los targets exactos del ciclo y utiliza idempotencia.
- Los workflows de GitHub Actions tienen concurrencia, timeout, caché y
  secretos fuera del código.

## 3. Qué no funcionó o queda pendiente

- Al inicio fue necesario aplicar manualmente el esquema y la migración de
  `submission_receipts` en Supabase.
- El primer intento de entrenamiento falló porque el bucket de artefactos no
  existía; el cliente ahora puede crearlo como privado.
- La evaluación todavía no tiene `actuals` porque el reloj aparece en estado
  `waiting`; por eso aún no hay ciclos evaluados ni scores reales del sistema.
- El data drift usa `observations.value` como proxy de feature. Aún no se
  guardan distribuciones de las features derivadas dentro del artefacto.
- La plataforma externa y Supabase se comunican mediante varias operaciones
  HTTP; la consistencia se protege con idempotencia y checkpoints, no con una
  transacción distribuida única.

## 3.1 Adaptación a la fase de drift

Durante la fase de drift, los rezagos diarios y semanales dejaron de ser
confiables. La inferencia ahora ejecuta un selector en memoria con modelos
Ridge de ventana corta: `cross_ar` usa los últimos ocho valores de las doce
estaciones, `own_ar` usa los ocho valores de la estación y `pooled_ar` normaliza
las estaciones antes de ajustar un modelo común. Se prueban ventanas de 12 y
24 horas, además de persistencia y el champion.

La elección se hace por estación con seis orígenes sombra anteriores al corte.
Cada origen usa únicamente observaciones disponibles hasta ese momento y se
elige el candidato con menor WAPE. Si un modelo reciente falla, se conserva el
champion y se registra la elección en el log. La inferencia mantiene las 48
predicciones, el corte y la idempotencia originales.

También se verifica la frescura: si una estación no tiene datos dentro de los
30 minutos anteriores al corte, se intenta una ingesta adicional y se registra
la advertencia sin inventar observaciones futuras.

### 3.2 Cambio periódico del 18-sep virtual y correcciones

Desde el 18-sep virtual (30-sep real) la demanda dejó de seguir el patrón
diario y semanal: la forma intradía dejó de correlacionar con la referencia
(de ~0,95 a ~0) y apareció una onda de 4 horas (16 intervalos) con grupos de
estaciones desfasados. El champion LightGBM, que depende de rezagos de 1 día y
1 semana, cayó a ~41 %, prácticamente igual que la persistencia.

El selector incorpora candidatos estacionales (`seasonal_4h`, promedios de 2 y
3 periodos de 4 h, `seasonal_1d`, `seasonal_1w`) que compiten con los AR de
ventana corta en los mismos seis orígenes sombra. En un backtest de los
últimos ciclos el selector obtuvo 91,5 %. Un candidato solo compite si tiene
predicción en todos los orígenes sombra. La elección por estación y sus WAPE
quedan guardados en `pipeline_events.details.selector`, en
`predictions.model_version` (`selector:<candidato>`) y en la versión enviada
(`selector-<hash>`).

Correcciones del pipeline:

- La prueba de significancia fallaba siempre (`duplicate labels`) porque
  quedaban dos columnas `prediction`; por eso ningún candidato se promovía.
  El error de evaluación del champion ahora se guarda en `training_metadata`.
- Las lecturas de `metrics`, `predictions` y `actuals` quedaban cortadas en
  1000 filas por PostgREST; ahora se pagina con orden por clave primaria.
- Los ciclos se ordenan por su instante virtual y no por `calculated_at`, que
  `evaluate.py` reescribe en cada ejecución.
- El monitor parsea timestamps ISO-8601 con o sin microsegundos y marca como
  `resolved` las señales cuya condición ya no se cumple.
- Un timeout del collector ya no bloquea la inferencia ni el reentrenamiento.

## 4. Decisión de promoción

Se comparó el promedio de accuracy de los cuatro horizontes contra el mejor
baseline agregado:

- Mejor baseline: `seasonal_naive`, `83.7934`.
- LightGBM: `86.4581`.
- Diferencia: `+2.6647` puntos porcentuales.

La promoción se permitió únicamente porque LightGBM superó al mejor baseline y
las cuatro inferencias de prueba produjeron valores finitos y no negativos.
El modelo quedó registrado como:

```text
lgbm-20260920T165954Z-28d05e5ff125-af5852ea
```

## 5. Decisión de reentrenamiento

El workflow de entrenamiento se programa cada dos horas, con una histéresis de
seis horas entre promociones. Cada ejecución crea una nueva versión y nunca
sobrescribe artefactos. Una versión permanece como `candidate` si no supera al
mejor baseline; solo se convierte en `champion` si también mejora las
submissions aceptadas de las últimas 24 horas, conserva una regresión máxima de
tres puntos por estación y pasa la inferencia de prueba. El umbral de 85 % queda
como objetivo informativo, no como bloqueo durante el drift. El champion
anterior se marca como `retired` al promover el nuevo.

El reentrenamiento también debe considerarse antes si:

- performance drift permanece bajo 80 % durante tres ciclos consecutivos;
- PSI de la demanda reciente alcanza `0.20` o más;
- hay fallas repetidas del collector o submissions pendientes.

## 6. Evidencia reproducible

El workflow manual `Diagnose recent models` ejecuta
`scripts/diagnose_recent.py` en modo solo lectura. Calcula durante los últimos
36 orígenes horarios el accuracy oficial de champion, persistencia, modelos de
ventana corta y selector; también imprime el promedio de los últimos seis
ciclos. Todas las ventanas se cortan por el origen evaluado para evitar fuga de
información futura.

## 7. Qué haríamos después

1. Esperar la activación del reloj y ejecutar el ciclo completo con observaciones
   reales reveladas.
2. Confirmar que `evaluate.py` llena `actuals`, `metrics` y consulta ambas
   ventanas del leaderboard.
3. Validar señales de monitorización con fallas controladas y recuperar señales
   resueltas para evitar alertas repetidas.
4. Guardar estadísticas de distribución de las features usadas por el champion
   para reemplazar el proxy de demanda del PSI.
5. Añadir pruebas de integración contra una base Supabase de prueba y pruebas
   de contrato para targets, idempotencia y límites de tres intentos.
6. Comparar LightGBM con modelos directos adicionales y calibrar los
   hiperparámetros usando únicamente ventanas temporales pasadas.
