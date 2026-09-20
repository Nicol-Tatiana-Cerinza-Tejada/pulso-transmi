"""Cliente HTTP para los endpoints de competencia de Pulso TransMi.

La clave se toma de ``PULSO_API_KEY`` y nunca se incluye en los cuerpos JSON.
Las respuestas se devuelven como diccionarios/listas nativos de Python para
conservar exactamente el contrato publicado por la API.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class PulsoTransmiError(RuntimeError):
    """Error HTTP con el cuerpo y el identificador de solicitud del servidor."""

    def __init__(self, response: httpx.Response) -> None:
        self.status_code = response.status_code
        self.request_id = response.headers.get("X-Request-ID")
        self.body = response.text
        try:
            detail: Any = response.json()
        except ValueError:
            detail = self.body
        self.detail = detail
        suffix = f" (X-Request-ID: {self.request_id})" if self.request_id else ""
        super().__init__(f"Pulso TransMi respondió HTTP {self.status_code}{suffix}: {detail}")


class PulsoTransmiClient:
    """Cliente síncrono con reintentos exponenciales para la API pública.

    ``PULSO_API_URL`` puede usarse para apuntar a otro despliegue. Los endpoints
    autenticados exigen ``PULSO_API_KEY`` o un ``api_key`` explícito.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | httpx.Timeout = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries debe ser mayor o igual a cero")
        if backoff_factor < 0:
            raise ValueError("backoff_factor no puede ser negativo")
        self.base_url = (base_url or os.getenv("PULSO_API_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("PULSO_API_KEY")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def __enter__(self) -> "PulsoTransmiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self, *, require_auth: bool = False) -> dict[str, str]:
        if require_auth and not self.api_key:
            raise ValueError("Define PULSO_API_KEY para usar este endpoint")
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        require_auth: bool = False,
    ) -> Any:
        request_headers = self._headers(require_auth=require_auth)
        request_headers.update(headers or {})
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        url = f"{self.base_url}{path}"

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    params=clean_params,
                    json=json,
                    headers=request_headers,
                )
            except httpx.RequestError:
                if attempt >= self.max_retries:
                    raise
                self._sleep(attempt)
                continue

            if response.status_code not in RETRYABLE_STATUS_CODES:
                if response.is_error:
                    raise PulsoTransmiError(response)
                if response.status_code == 204:
                    return None
                return response.json()

            if attempt >= self.max_retries:
                raise PulsoTransmiError(response)
            self._sleep(attempt, response)

        raise AssertionError("el bucle de reintentos terminó inesperadamente")

    def _sleep(self, attempt: int, response: httpx.Response | None = None) -> None:
        retry_after = response.headers.get("Retry-After") if response else None
        try:
            delay = float(retry_after) if retry_after is not None else self.backoff_factor * (2**attempt)
        except ValueError:
            delay = self.backoff_factor * (2**attempt)
        if delay > 0:
            time.sleep(delay)

    def health(self) -> dict[str, str]:
        return self._request("GET", "/health")

    def clock(self) -> dict[str, Any]:
        """Obtiene el reloj virtual autoritativo (`GET /v1/clock`)."""
        return self._request("GET", "/v1/clock")

    def stations(self) -> dict[str, Any]:
        """Obtiene el catálogo de estaciones (`GET /v1/stations`)."""
        return self._request("GET", "/v1/stations")

    def observations(
        self,
        *,
        station_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Obtiene histórico paginado (`GET /v1/observations`)."""
        return self._request(
            "GET",
            "/v1/observations",
            params={"station_id": station_id, "start": start, "end": end, "cursor": cursor, "limit": limit},
        )

    def stream_observations(self, *, cursor: str | None = None, limit: int = 1000) -> dict[str, Any]:
        """Obtiene la siguiente página del stream incremental."""
        return self._request(
            "GET",
            "/v1/stream/observations",
            params={"cursor": cursor, "limit": limit},
        )

    def current_cycle(self) -> dict[str, Any]:
        """Obtiene el ciclo abierto y sus targets (`GET /v1/forecast-cycles/current`)."""
        return self._request("GET", "/v1/forecast-cycles/current")

    def me(self) -> dict[str, Any]:
        """Obtiene la identidad del participante autenticado."""
        return self._request("GET", "/v1/me", require_auth=True)

    def create_submission(self, payload: Mapping[str, Any], *, idempotency_key: str) -> Any:
        """Crea una entrega usando la clave idempotente obligatoria."""
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key debe tener entre 8 y 128 caracteres")
        return self._request(
            "POST",
            "/v1/submissions",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
            require_auth=True,
        )

    def submission_receipt(self, submission_id: str) -> dict[str, Any]:
        """Obtiene el recibo propio de una entrega."""
        return self._request("GET", f"/v1/submissions/{submission_id}", require_auth=True)

    def leaderboard(self, *, window: str = "cumulative") -> dict[str, Any]:
        """Obtiene el ranking autenticado acumulado o de las últimas 24 horas."""
        if window not in {"cumulative", "rolling_24h"}:
            raise ValueError("window debe ser 'cumulative' o 'rolling_24h'")
        return self._request(
            "GET",
            "/v1/leaderboard",
            params={"window": window},
            require_auth=True,
        )


# Resumen OpenAPI real (0.5.0): GET /v1/clock; GET /v1/stations; GET
# /v1/observations (station_id, start, end, cursor, limit 1..5000); GET
# /v1/stream/observations (cursor, limit 1..5000); GET /v1/forecast-cycles/current;
# POST /v1/submissions (body SubmissionInput y header Idempotency-Key 8..128);
# GET /v1/submissions/{submission_id}; y GET /v1/leaderboard (window=cumulative o
# rolling_24h). SubmissionInput exige schema_version 1.0, cycle_id, client_run_id,
# data_cutoff, model y predictions. PredictionInput exige station_id de 5 dígitos,
# target_at y value entre 0 y 100000; ModelTrace exige version y admite fechas y
# git_commit. Las respuestas dinámicas son objetos JSON con data/targets/recibos
# según el endpoint; los errores de validación usan HTTPValidationError.
