"""Acceso server-side a las tablas de Supabase.

La clave usada aquí es una clave secreta server-side: solo debe vivir en el
backend, workers o jobs protegidos. Nunca debe exponerse en un frontend ni
commitearse.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

from .local_env import load_local_env


class SupabaseConfigurationError(RuntimeError):
    """La configuración mínima de Supabase no está disponible."""


def create_supabase_client(
    *,
    url: str | None = None,
    service_role_key: str | None = None,
    timeout: float = 30.0,
) -> "Client":
    """Crea un cliente Supabase usando una clave secreta server-side.

    Se acepta ``SUPABASE_SECRET_KEY`` (formato moderno) y, por compatibilidad,
    ``SUPABASE_SERVICE_ROLE_KEY`` (formato legacy). El argumento explícito
    ``service_role_key`` tiene prioridad sobre ambas variables.

    Se importa el SDK de forma diferida para que el resto del proyecto pueda
    cargarse sin Supabase instalado cuando no se ejecutan tareas de persistencia.
    """
    load_local_env()
    supabase_url = url or os.getenv("SUPABASE_URL")
    supabase_key = (
        service_role_key
        or os.getenv("SUPABASE_SECRET_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", supabase_url),
            ("SUPABASE_SECRET_KEY o SUPABASE_SERVICE_ROLE_KEY", supabase_key),
        )
        if not value
    ]
    if missing:
        raise SupabaseConfigurationError(
            f"Faltan variables de entorno de Supabase: {', '.join(missing)}"
        )

    try:
        from supabase import create_client
        from supabase.client import ClientOptions
    except ImportError as exc:
        raise RuntimeError(
            "Instala la dependencia 'supabase' antes de usar el cliente de base de datos"
        ) from exc

    return create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(
            auto_refresh_token=False,
            persist_session=False,
            postgrest_client_timeout=timeout,
            storage_client_timeout=timeout,
            schema="public",
        ),
    )


class SupabaseDB:
    """Pequeño wrapper para operaciones usadas por el colector y el pipeline."""

    def __init__(self, client: "Client | None" = None, **client_kwargs: Any) -> None:
        self.client = client or create_supabase_client(**client_kwargs)

    def select(self, table: str, *, columns: str = "*") -> list[dict[str, Any]]:
        """Devuelve todas las filas seleccionadas de una tabla pública."""
        response = self.client.table(table).select(columns).execute()
        return list(response.data or [])

    def insert(self, table: str, rows: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Inserta una fila o varias y devuelve las filas creadas."""
        payload = [dict(rows)] if isinstance(rows, Mapping) else [dict(row) for row in rows]
        response = self.client.table(table).insert(payload).execute()
        return list(response.data or [])

    def upsert(
        self,
        table: str,
        rows: Mapping[str, Any] | Iterable[Mapping[str, Any]],
        *,
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inserta o actualiza filas, útil para observations y actuals."""
        payload = [dict(rows)] if isinstance(rows, Mapping) else [dict(row) for row in rows]
        kwargs = {"on_conflict": on_conflict} if on_conflict else {}
        response = self.client.table(table).upsert(payload, **kwargs).execute()
        return list(response.data or [])

    def upsert_observations(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Hace upsert del histórico con la PK ``(station_id, ts)``."""
        return self.upsert("observations", rows, on_conflict="station_id,ts")

    def observation_count(self) -> int:
        """Cuenta observaciones sin descargar sus filas."""
        response = self.client.table("observations").select(
            "station_id", count="exact", head=True
        ).execute()
        return int(response.count or 0)

    def latest_confirmed_cursor(self) -> str | None:
        """Obtiene el cursor del último run exitoso registrado."""
        response = (
            self.client.table("collector_runs")
            .select("cursor")
            .eq("status", "succeeded")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0].get("cursor")

    def start_collector_run(self) -> int:
        """Abre un run sin confirmar ningún cursor nuevo."""
        response = (
            self.client.table("collector_runs")
            .insert({"status": "running", "rows_new": 0})
            .select("id")
            .execute()
        )
        if not response.data:
            raise RuntimeError("Supabase no devolvió el id de collector_runs")
        return int(response.data[0]["id"])

    def finish_collector_run(
        self,
        run_id: int,
        *,
        status: str,
        rows_new: int,
        cursor: str | None,
        error: str | None = None,
    ) -> None:
        """Cierra un run; debe llamarse después del upsert de observaciones."""
        values = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "rows_new": rows_new,
            "cursor": cursor,
            "error": error,
        }
        self.client.table("collector_runs").update(values).eq("id", run_id).execute()

    def upload_artifact(self, bucket: str, path: str, content: bytes) -> str:
        """Sube un artefacto sin permitir sobrescritura y devuelve su ruta."""
        options = {
            "cache-control": "3600",
            "content-type": "application/octet-stream",
            "upsert": "false",
        }
        storage = self.client.storage
        try:
            storage.from_(bucket).upload(path, content, file_options=options)
        except Exception as exc:
            if "Bucket not found" not in str(exc):
                raise
            try:
                storage.create_bucket(
                    bucket,
                    options={"public": False, "file_size_limit": 50 * 1024 * 1024},
                )
            except Exception as create_exc:
                if "already exists" not in str(create_exc).lower():
                    raise
            storage.from_(bucket).upload(path, content, file_options=options)
        return path

    def download_artifact(self, bucket: str, path: str) -> bytes:
        """Descarga un artefacto privado desde Supabase Storage."""
        return bytes(self.client.storage.from_(bucket).download(path))
