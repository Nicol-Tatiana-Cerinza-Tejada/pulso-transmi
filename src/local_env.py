"""Carga opcional y segura de credenciales locales no versionadas."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    """Carga ``.env.local`` desde la raíz actual sin reemplazar variables exportadas.

    El archivo es deliberadamente simple: una variable ``CLAVE=valor`` por línea.
    No se evalúa código shell ni sustituciones de comandos.
    """
    path = Path.cwd() / ".env.local"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
