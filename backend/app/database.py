"""Conexión a PostgreSQL/PostGIS mediante un pool de psycopg 3."""
import logging
import os
import re

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger("sismos.db")

_REQUIRED = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        # Fail closed: nunca usar credenciales por defecto en el código.
        raise RuntimeError(f"Variable de entorno requerida no definida: {name}")
    return value


def _conninfo() -> str:
    for var in _REQUIRED:
        _env(var)
    # TODO(security): habilitar TLS/mTLS hacia PostgreSQL (sslmode=verify-full +
    # certificados) cuando el servidor knossos tenga SSL configurado.
    sslmode = os.getenv("DB_SSLMODE", "prefer")
    return (
        f"host={_env('DB_HOST')} port={_env('DB_PORT')} dbname={_env('DB_NAME')} "
        f"user={_env('DB_USER')} password={_env('DB_PASSWORD')} sslmode={sslmode} "
        f"application_name=sismos-web connect_timeout=5"
    )


# Nombre de tabla configurable, validado contra un patrón estricto y luego
# escapado como identificador (nunca concatenado como texto plano).
_TABLE_RAW = os.getenv("SISMOS_TABLE", "public.sismos")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", _TABLE_RAW):
    raise RuntimeError("SISMOS_TABLE tiene un formato inválido")
SISMOS_TABLE = sql.Identifier(*_TABLE_RAW.split("."))

pool: ConnectionPool | None = None


def open_pool() -> None:
    global pool
    pool = ConnectionPool(
        conninfo=_conninfo(),
        min_size=1,
        max_size=int(os.getenv("DB_POOL_MAX", "10")),
        kwargs={"row_factory": dict_row},
        open=True,
    )
    logger.info("Pool de conexiones a PostgreSQL abierto")


def close_pool() -> None:
    if pool is not None:
        pool.close()
        logger.info("Pool de conexiones cerrado")


def fetch_all(query: sql.Composable, params: dict | None = None) -> list[dict]:
    if pool is None:
        raise RuntimeError("Pool no inicializado")
    with pool.connection() as conn:
        # Sesión de sólo lectura: la app no necesita modificar datos.
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            return cur.fetchall()
