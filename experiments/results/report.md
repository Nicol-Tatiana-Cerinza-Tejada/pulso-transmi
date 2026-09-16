# Resultado de calibración experimental

Escenario determinista con 45 días de historia, 7 de competencia, 12 estaciones y cuatro horizontes.
La accuracy es `100 × max(0, 1 - WAPE)` promediada por estación.

## Accuracy por régimen

| model                   |   Total |   Pre-drift |   Transición |   Post-drift |
|:------------------------|--------:|------------:|-------------:|-------------:|
| boosting_adaptive_daily |   83.02 |       86.04 |        83.38 |        77.90 |
| boosting_static         |   82.58 |       86.20 |        83.50 |        75.71 |
| naive_week              |   78.77 |       83.91 |        80.27 |        70.78 |
| naive_day               |   75.86 |       83.86 |        65.79 |        71.48 |
| ridge_static            |   75.18 |       78.67 |        76.91 |        67.98 |
| naive_last              |   74.40 |       74.57 |        74.26 |        74.10 |

## Lectura automática

- Regresión lineal estática: **75.18**. No resuelve por sí sola el reto.
- Caída del boosting estático tras drift: **10.49 puntos**.
- Recuperación por reentrenamiento diario post-drift: **2.20 puntos**.
- Veredicto automático: **RETO VIABLE**.

La decisión final también debe revisar estabilidad por estación y horizonte; no se debe optimizar solo el promedio.
