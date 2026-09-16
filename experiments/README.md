# Experimento transitorio de calibración

Este directorio existe únicamente en la rama `codex/experiment-calibration`.
Su propósito es comprobar si el escenario sintético resulta demasiado fácil,
demasiado aleatorio o adecuado para estudiantes de pregrado antes de implementar
el generador de producción.

## Ejecutar

```bash
uv venv /tmp/pulso-transmi-experiment
uv pip install --python /tmp/pulso-transmi-experiment/bin/python -r experiments/requirements.txt
/tmp/pulso-transmi-experiment/bin/python experiments/calibrate.py
/tmp/pulso-transmi-experiment/bin/python experiments/sweep.py
```

Los resultados se escriben en `experiments/results/`:

- `metrics.csv`: accuracy y MAE por modelo, régimen y horizonte;
- `metrics_by_station.csv`: dificultad y estabilidad entre estaciones;
- `metrics_by_day.csv`: evolución diaria antes y después del drift;
- `competition_predictions.csv`: predicciones auditables del corte;
- `report.md`: lectura compacta y veredicto automático;
- `accuracy_by_regime.png`: comparación visual del efecto del drift;
- `patterns_and_drift.png`: series de tres estaciones representativas;
- `hourly_sample.csv`: muestra agregada para inspección;
- `run.json`: semilla y tamaños del experimento.
- `seed_sweep.csv` y `seed_sweep_summary.csv`: robustez entre tres semillas;
- `seed_sweep.md`: resumen legible de la prueba de robustez.
- `analysis.md`: interpretación pedagógica y recomendación de diseño.

## Baselines

- último valor;
- mismo intervalo del día anterior;
- mismo intervalo de la semana anterior;
- regresión Ridge estática;
- gradient boosting entrenado una sola vez;
- el mismo boosting reentrenado cada día con una ventana móvil de 28 días.

Todas las features usan únicamente información disponible en el origen del
pronóstico. El modelo adaptativo solo incorpora labels cuyo `target_at` ya pasó.

## Criterios preliminares

El escenario se considera candidato si:

1. la regresión estática no supera 88 % en toda la competencia;
2. el boosting es mejor que los baselines simples sin acercarse a 100 %;
3. el boosting estático pierde al menos 7 puntos tras los cambios de régimen;
4. el reentrenamiento recupera al menos 2 puntos post-drift;
5. ningún horizonte o estación domina por completo la métrica agregada.

Estos umbrales son de diseño, no reglas definitivas del leaderboard.
