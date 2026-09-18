---
title: Progreso de Pulso TransMi
description: Estado verificable, decisiones vigentes, pendientes y criterios de salida del proyecto.
updated_at: 2026-09-18
---

# Progreso del proyecto

Esta bitácora separa trabajo terminado, trabajo en curso y diseño planificado. Se
actualiza cuando cambia el estado operativo; una idea documentada no equivale a
una funcionalidad disponible.

## Resumen del corte

**Fecha:** 18 de septiembre de 2026

**Versión:** `0.4.2`

**Fase:** portal estudiantil y ronda de integración operativos

**Estado global:** primera predicción habilitada; escenario dinámico aún no iniciado

**Repositorio:** `uexternadojz/pulso-transmi`
**VPS:** `/opt/pulso-transmi`

## Entregado y verificado

| Área | Resultado | Evidencia |
|---|---|---|
| Repositorio | Repo público y rama `main` publicada | GitHub |
| Contenedores | API, scheduler y PostgreSQL definidos con límites de recursos y logs | `docker-compose.yml` |
| PostgreSQL | PostgreSQL 17, volumen persistente y healthcheck | `docker-compose.yml` |
| Separación de datos | Esquemas `catalog`, `sim`, `competition` y `ops` | `database/init/01-schema.sql` |
| Privilegios | API sin acceso a escenarios privados, parámetros ni ground truth | grants + migración `003` |
| Integridad | Foreign keys compuestas, checks de predicción finita e índices operativos | migración `002` |
| API de datos | Dataset inicial, stream incremental, reloj y ciclos | `app/main.py` |
| Portal estudiantil | Login por correo + documento, nombre preferido y rotación autoservicio de API key | `app/portal.py` + migraciones `004` y `005` |
| Matrícula | 32 estudiantes activos cargados sin almacenar documento o correo en claro | verificación operativa: grupo A 20, grupo B 12 |
| Ronda de práctica | Ciclo abierto con 12 targets, uno por estación, sin activar el reloj sintético | `database/operations/bootstrap-practice.sql` |
| Submissions | API key con scrypt, schema estricto, idempotencia y recibos privados | `app/competition.py` |
| Guardrails | 64 KB, JSON, rate limit, cutoff, targets exactos y 3 intentos | API, Caddy y BD |
| Scheduler | Tick con advisory lock, liberación, ciclos, scoring y snapshots | `app/scheduler.py` |
| Leaderboard | Ventanas cumulative y rolling 24 h | `score_snapshots` y API |
| Despliegue | Stack levantado en el VPS; API enlazada únicamente a `127.0.0.1:8010` | verificación operativa del corte |
| Pruebas | 19 pruebas automatizadas, rotación protegida y entrega pública aceptada 12/12 | `tests/` + verificación del corte |
| Gestión | Proyecto creado en la vertical Academy del Supabase operativo | ID `1dde4b7d-7ab4-4df8-8298-34c25d662750` |

## Implementado parcialmente

| Área | Disponible | Falta para cerrar |
|---|---|---|
| API | Protocolo y ensayo externo de práctica completos | ensayo con el escenario dinámico oficial |
| Scheduler | Flujo completo implementado | prueba integral y observación bajo reloj activo |
| Base de datos | Modelo, constraints y migraciones `002` a `005` | escenario definitivo y automatización de backup |
| Escenarios | Contrato YAML de ejemplo | compilador, cifrado/gestión de semilla, generación y validación |
| Métricas | Tablas y definición de WAPE/accuracy | cálculo transaccional, snapshots y pruebas de casos límite |
| Observabilidad | Healthchecks y logs Docker rotados | métricas, alertas y dashboard operativo |

## No disponible todavía

- catálogo y serie histórica están disponibles como dataset inicial; la ronda de
  práctica está materializada, pero el escenario dinámico aún no está activo;
- escenario activo o reloj virtual en ejecución;
- backup diario externo al VPS;
- starter kit automatizado con GitHub Actions; el baseline manual ya está publicado;
- pipeline de referencia en GitHub Actions;
- prueba end-to-end de todos los estudiantes; la prueba pública controlada ya pasó.

## Decisiones vigentes

1. La plataforma central corre en Docker sobre el VPS y usa PostgreSQL propio.
2. Cada estudiante puede usar GitHub Actions y Supabase en sus planes gratuitos.
3. Vercel se reserva para el dashboard opcional y no es requisito del score.
4. Los datos tienen granularidad de 15 minutos, se publican cada 30 minutos y
   cada ciclo horario exige cuatro horizontes futuros.
5. El ground truth completo se precalcula, pero permanece en `sim` y nunca se
   expone al rol de la API.
6. Los cambios de régimen actúan sobre parámetros causales para exigir monitoreo
   y reentrenamiento, no como ruido arbitrario aplicado al resultado.
7. La métrica principal es accuracy derivada de WAPE por estación, con cobertura
   mínima prevista del 95 %.
8. La competencia no se activa hasta completar calibración, seguridad, backup y
   una prueba integral externa.
9. El login usa correo institucional + documento como guardrail. El nombre
   preferido solo personaliza la sesión; el leaderboard usa el nombre oficial.

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

- [x] implementar reloj y ticks idempotentes con advisory lock;
- [x] liberar observaciones y contexto sin filtrar futuro;
- [x] abrir y cerrar ciclos cada hora;
- [x] autenticar participantes con secretos almacenados como hash;
- [x] validar cutoff, targets, valores finitos, duplicados e idempotencia;
- [x] resolver ciclos y generar snapshots cumulative y rolling 24h;
- [x] ejecutar una prueba integral con ronda de práctica y participante de ensayo.
- [ ] repetir la prueba integral con el escenario dinámico oficial.

**Criterio de salida:** reintentos no duplican datos, una entrega tardía no entra
al score y cada resultado puede reconstruirse desde registros inmutables.

### Hito 4 — Publicación y experiencia estudiantil

- [x] configurar dominio, Caddy y HTTPS;
- [x] aplicar rate limiting y límites de payload;
- [ ] programar backup y probar restauración;
- [x] publicar OpenAPI y ejemplos válidos de requests/responses;
- [ ] crear starter kit con GitHub Actions y manejo de secrets;
- [x] ejecutar el flujo completo desde una identidad de prueba y retirar su acceso.

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
