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

El workflow de entrenamiento se programa diariamente. Cada ejecución crea una
nueva versión y nunca sobrescribe artefactos. Una versión permanece como
`candidate` si no supera al mejor baseline; solo se convierte en `champion` si
también pasa la inferencia de prueba. El champion anterior se marca como
`retired` al promover el nuevo.

El reentrenamiento también debe considerarse antes si:

- performance drift permanece bajo 80 % durante tres ciclos consecutivos;
- PSI de la demanda reciente alcanza `0.20` o más;
- hay fallas repetidas del collector o submissions pendientes.

## 6. Qué haríamos después

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
