# Pulso TransMi Dashboard

Dashboard Next.js 15 con App Router, TypeScript, Tailwind y Recharts. Lee
exclusivamente las vistas públicas creadas por `sql/02_readonly_views.sql`:

- `v_accuracy_timeline`
- `v_accuracy_by_station`
- `v_champion_current`
- `v_model_history`
- `v_drift_signals`
- `v_pipeline_runs`
- `v_leaderboard_snapshot`
- `v_accuracy_by_horizon`, `v_demand_recent`, `v_pipeline_health`
- `v_snapshot_history`, `v_retrain_history`

No se consulta ninguna tabla base. El cliente de Supabase usa solamente la
publishable key; nunca agregues `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_SECRET_KEY` ni otra clave secreta a este proyecto.

## Desarrollo local

Desde esta carpeta:

```bash
cp .env.example .env.local
# Edita .env.local con la URL y publishable key de Supabase.
npm install
npm run dev
```

Abre <http://localhost:3000>.

Antes, ejecuta en Supabase las migraciones del repositorio, especialmente
`database/migrations/007_dataset_snapshots.sql`,
`database/migrations/008_dashboard_observability.sql`,
`sql/03_model_promotion_events.sql` y `sql/02_readonly_views.sql`. Las vistas
deben tener `SELECT` para `anon` y las tablas base deben conservar RLS activo.

La migración `008` también agrega el horizonte de cada predicción nueva. Las
predicciones históricas anteriores a esa migración pueden aparecer sin datos en
el gráfico por horizonte hasta que se generen nuevas entregas.

## Despliegue

### Vercel

1. Importa el repositorio y selecciona `pulso-transmi/dashboard` como
   **Root Directory**.
2. Usa `npm install` y `npm run build` como instalación y build.
3. Define únicamente estas variables en el proyecto:

```text
NEXT_PUBLIC_SUPABASE_URL=https://tu-proyecto.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

4. Despliega. No definas variables `SUPABASE_SERVICE_ROLE_KEY` en el proyecto
   frontend.

### Servidor Node

```bash
npm ci
npm run build
npm run start
```

El navegador refresca los datos cada 60 segundos. Durante el arranque, ante
una consulta vacía o mientras la competencia no esté activa, muestra estados
de carga y “sin datos aún” en lugar de fallar.

Cuando `src.train --from-supabase`, `src.evaluate` o `src.monitor` escriben en
Supabase, el dashboard recoge esos cambios en el siguiente refresco automático;
no necesita un redeploy de Vercel para actualizar sus datos.

## Seguridad

Las variables `NEXT_PUBLIC_*` se incluyen en el bundle por diseño: la
publishable key no es un secreto. La protección real está en RLS y en el
`GRANT SELECT` limitado a las vistas. Las claves service-role/secret nunca
deben estar en `.env.local` del dashboard, componentes cliente, código
TypeScript o configuración de Vercel.
