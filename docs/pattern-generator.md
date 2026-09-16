# Generador de patrones

## Modelo

Cada conteo se obtiene de una media positiva y una distribución binomial
negativa:

```text
log(mu_s,t) =
    nivel_s
  + estacionalidad_s(t)
  + calendario_s(t)
  + tendencia_s(t)
  + clima_s(t)
  + eventos_s(t)
  + espacio_s(t-1)
  + factor_ciudad(t)
  + residual_s(t)
  + drift_s(t)

y_s,t ~ NegativeBinomial(mu_s,t, dispersion_s)
```

## Personalidades

Cada escenario distribuye las estaciones entre cinco arquetipos privados:
residencial, laboral, intercambio, universitaria y ocio. Los parámetros se
guardan en `sim.scenario_stations.parameters` y no se exponen.

## Reproducibilidad

El escenario completo se genera antes de iniciar. Cada muestra aleatoria se
deriva de la semilla privada, estación, timestamp y componente. El resultado no
depende del tamaño del batch ni del orden de ejecución.

## Drift

Los drifts modifican parámetros causales en lugar de multiplicar directamente
el resultado final:

- `level_shift`: intercepto.
- `peak_shift`: centro de un pico diario.
- `trend_change`: pendiente temporal.
- `weather_change`: sensibilidad al clima.
- `closure`: nivel y redistribución espacial.
- `variance_shift`: dispersión.

Una transición gradual se implementa con una función sigmoide entre el inicio y
el final configurado.

## Calibración obligatoria

Antes de validar un escenario se ejecutan, como mínimo:

1. Último valor.
2. Mismo intervalo del día anterior.
3. Mismo intervalo de la semana anterior.
4. Regresión con calendario y lags.
5. Modelo de boosting con las mismas variables públicas.

El escenario se rechaza si los modelos no quedan en las bandas declaradas en
`config/scenario.example.yaml` o si el drift no produce una caída observable.
