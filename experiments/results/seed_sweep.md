# Robustez entre semillas

Semillas evaluadas: 20260916, 20260917, 20260918.

## Accuracy media

| model                   |   Total |   Pre-drift |   Transición |   Post-drift |
|:------------------------|--------:|------------:|-------------:|-------------:|
| boosting_adaptive_daily |   83.03 |       85.93 |        83.61 |        78.05 |
| boosting_static         |   82.53 |       86.07 |        83.64 |        75.87 |
| naive_day               |   76.10 |       83.69 |        67.16 |        71.82 |
| naive_week              |   78.43 |       83.79 |        80.05 |        70.25 |
| ridge_static            |   75.38 |       78.47 |        77.49 |        68.38 |

Consulta `seed_sweep_summary.csv` para desviación estándar, mínimo y máximo.
