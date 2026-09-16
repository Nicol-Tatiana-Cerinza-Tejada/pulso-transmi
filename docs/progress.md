---
title: Progreso de Pulso TransMi
description: Estado verificable, decisiones vigentes, pendientes y criterios de salida del proyecto.
updated_at: 2026-09-16
---

# Progreso del proyecto

Esta bitácora separa trabajo terminado, trabajo en curso y diseño planificado. Se
actualiza cuando cambia el estado operativo; una idea documentada no equivale a
una funcionalidad disponible.

## Resumen del corte

**Fecha:** 16 de septiembre de 2026  
**Versión:** `0.1.0`  
**Fase:** infraestructura base  
**Estado global:** plataforma interna saludable; competencia aún no iniciada  
**Repositorio:** `uexternadojz/pulso-transmi`  
**VPS:** `/opt/pulso-transmi`

## Entregado y verificado

| Área | Resultado | Evidencia |
|---|---|---|
| Repositorio | Repo privado creado y rama `main` publicada | GitHub y commit `e440358` |
| Contenedores | API, scheduler y PostgreSQL definidos con límites de recursos y logs | `docker-compose.yml` |
| PostgreSQL | PostgreSQL 17, volumen persistente y healthcheck | `docker-compose.yml` |
| Separación de datos | Esquemas `catalog`, `sim`, `competition` y `ops` | `database/init/01-schema.sql` |
| Privilegios | Roles independientes para API y scheduler; futuro privado fuera del rol API | `database/init/02-grants.sql` |
| Integridad | Foreign keys compuestas, checks de predicción finita e índices operativos | migración `002` |
| API | `/health`, `/ready` y `/v1/meta` | `app/main.py` |
| Scheduler | Proceso persistente con heartbeat e identidad de instancia | `app/scheduler.py` |
| Despliegue | Stack levantado en el VPS; API enlazada únicamente a `127.0.0.1:8010` | verificación operativa del corte |
| Pruebas | Prueba automática de liveness | `tests/test_health.py` |
| Gestión | Proyecto creado en la vertical Academy del Supabase operativo | ID `1dde4b7d-7ab4-4df8-8298-34c25d662750` |

## Implementado parcialmente

| Área | Disponible | Falta para cerrar |
|---|---|---|
| API | Infraestructura, pool y tres endpoints iniciales | rutas públicas de competencia y autenticación |
| Scheduler | Heartbeat cada intervalo configurable | reloj virtual, locks, liberación, cierre y scoring |
| Base de datos | Modelo completo inicial | datos semilla, pruebas integrales y rutina de migración automatizada |
| Escenarios | Contrato YAML de ejemplo | compilador, cifrado/gestión de semilla, generación y validación |
| Métricas | Tablas y definición de WAPE/accuracy | cálculo transaccional, snapshots y pruebas de casos límite |
| Observabilidad | Healthchecks y logs Docker rotados | métricas, alertas y dashboard operativo |

## No disponible todavía

- catálogo cargado de estaciones y sus coordenadas;
- serie histórica sintética;
- escenario activo o reloj virtual en ejecución;
- endpoints de estaciones, observaciones y ciclos;
- registro de participantes y entrega segura de API keys;
- recepción y validación de submissions;
- resolución de targets, scoring y leaderboard;
- dominio público y TLS mediante Caddy;
- backup diario externo al VPS;
- starter kit para estudiantes;
- pipeline de referencia en GitHub Actions;
- prueba end-to-end desde un repositorio estudiantil.

## Decisiones vigentes

1. La plataforma central corre en Docker sobre el VPS y usa PostgreSQL propio.
2. Cada estudiante puede usar GitHub Actions y Supabase en sus planes gratuitos.
3. Vercel se reserva para el dashboard opcional y no es requisito del score.
4. Las observaciones aparecen cada 15 minutos y cada ciclo exige cuatro
   horizontes futuros.
5. El ground truth completo se precalcula, pero permanece en `sim` y nunca se
   expone al rol de la API.
6. Los cambios de régimen actúan sobre parámetros causales para exigir monitoreo
   y reentrenamiento, no como ruido arbitrario aplicado al resultado.
7. La métrica principal es accuracy derivada de WAPE por estación, con cobertura
   mínima prevista del 95 %.
8. La competencia no se activa hasta completar calibración, seguridad, backup y
   una prueba integral externa.

## Plan de ejecución

### Hito 1 — Datos y escenario reproducible

- [ ] seleccionar y cargar las 12 estaciones;
- [ ] conservar fuente y fecha de la metadata geográfica;
- [ ] implementar arquetipos, estacionalidad, clima, eventos, relaciones
  espaciales y distribución binomial negativa;
- [ ] hacer determinista cada muestra a partir de semilla, estación, tiempo y
  componente;
- [ ] materializar historia y competencia en `sim.generated_truth`;
- [ ] registrar versión del generador, commit y hash de configuración.

**Criterio de salida:** el mismo commit, configuración y semilla producen hashes
idénticos; ningún rol público puede consultar el futuro.

### Hito 2 — Calibración y drift

- [ ] ejecutar último valor, naive diario, naive semanal, regresión y boosting;
- [ ] validar las bandas de accuracy del escenario;
- [ ] comprobar una caída mínima de 10 puntos para un modelo sin reentrenar;
- [ ] revisar que un pipeline adaptativo pueda recuperar desempeño;
- [ ] congelar el escenario antes de la clase.

**Criterio de salida:** el problema es difícil pero aprendible, y el drift es
observable sin volver aleatorio el ranking.

### Hito 3 — Protocolo de competencia

- [ ] implementar reloj y ticks idempotentes con advisory lock;
- [ ] liberar observaciones y contexto sin filtrar futuro;
- [ ] abrir y cerrar ciclos cada hora;
- [ ] autenticar participantes con secretos almacenados como hash;
- [ ] validar cutoff, cobertura, targets, valores finitos y duplicados;
- [ ] resolver ciclos y generar snapshots cumulative y rolling 24h.

**Criterio de salida:** reintentos no duplican datos, una entrega tardía no entra
al score y cada resultado puede reconstruirse desde registros inmutables.

### Hito 4 — Publicación y experiencia estudiantil

- [ ] configurar dominio, Caddy y HTTPS;
- [ ] aplicar rate limiting y límites de payload;
- [ ] programar backup y probar restauración;
- [ ] publicar OpenAPI y ejemplos válidos de requests/responses;
- [ ] crear starter kit con GitHub Actions y manejo de secrets;
- [ ] ejecutar el flujo completo desde una cuenta de prueba.

**Criterio de salida:** un estudiante nuevo puede descargar datos, entrenar y
enviar una predicción siguiendo solo documentación pública.

## Riesgos abiertos

| Riesgo | Impacto | Mitigación prevista |
|---|---|---|
| Fuga del futuro | Invalida la competencia | roles separados, grants mínimos y prueba negativa |
| Drift demasiado obvio o imposible | Ranking poco útil | calibración contra cinco baselines |
| Reloj duplicado tras reinicio | Observaciones/ciclos inconsistentes | advisory lock, transacciones e idempotencia |
| Abuso o error en submissions | Saturación o scores corruptos | autenticación, validación estricta y rate limit |
| Pérdida de la base | Pérdida total de resultados | `pg_dump` diario externo y simulacro de restore |
| Dependencia del plan gratuito | Ejecuciones pausadas o cuotas | cargas pequeñas, observabilidad y procedimiento manual de contingencia |

## Cómo actualizar esta bitácora

En cada cambio material:

1. mover elementos entre “no disponible”, “parcial” y “entregado” solo con
   evidencia verificable;
2. actualizar `updated_at`, versión y corte;
3. enlazar código, prueba, comando o runbook que demuestre el resultado;
4. actualizar el README si cambia arquitectura, setup, despliegue, API o flujo
   estudiantil;
5. reflejar el estado resumido en el proyecto operativo de Academy.

No incluir secretos, valores de `.env`, semillas privadas ni parámetros ocultos
del escenario.
