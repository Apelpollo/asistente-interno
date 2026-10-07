"""Conexión a MySQL con el usuario de solo lectura."""
import pymysql
from pymysql.cursors import DictCursor

from app.config import settings


def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        cursorclass=DictCursor,
        charset="utf8mb4",
        connect_timeout=5,
    )


def run_query(sql: str, params: tuple | None = None) -> list[dict]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def get_schema() -> str:
    """Devuelve el esquema de las tablas como texto.

    Esto es lo que más adelante se le pasa al LLM para que genere SQL
    sabiendo qué tablas y columnas existen.
    """
    rows = run_query(
        """
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        (settings.mysql_database,),
    )
    tables: dict[str, list[str]] = {}
    for r in rows:
        tables.setdefault(r["TABLE_NAME"], []).append(f'{r["COLUMN_NAME"]} {r["COLUMN_TYPE"]}')
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())
