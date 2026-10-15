"""Agente text-to-SQL de solo lectura sobre MySQL.

Flujo:
  pregunta -> LLM genera SQL (con el esquema real) -> validación -> ejecución -> LLM resume los datos

Tres capas de seguridad:
  1. Permisos: la conexión usa `agente_lectura`, que en MySQL solo tiene SELECT.
     Aunque todo lo demás fallara, la base rechaza cualquier escritura.
  2. Validación del SQL antes de ejecutarlo (sqlglot): una sola sentencia, solo SELECT,
     solo tablas conocidas, sin funciones peligrosas (SLEEP, LOAD_FILE...) ni INTO OUTFILE.
  3. Límites de ejecución: LIMIT máximo de filas y tiempo máximo por consulta.
"""
import json
import re
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from app import db
from app.config import settings
from app.rag import get_llm

MAX_ROWS = 100
MAX_EXECUTION_MS = 5000

FORBIDDEN_FUNCTIONS = {"SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "SYS_EXEC", "SYS_EVAL"}
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.Command, exp.Merge, exp.TruncateTable, exp.Grant, exp.Set, exp.Use,
)


class SQLRechazado(Exception):
    """El SQL generado no pasó la validación (capa 2)."""


class SQLInvalido(Exception):
    """El SQL no se pudo interpretar (error de sintaxis). Se reintenta con el error, no es tema de seguridad."""


# ---------- Capa 2 y 3: validación y límites ----------

def allowed_tables() -> set[str]:
    rows = db.run_query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
        (settings.mysql_database,),
    )
    return {r["TABLE_NAME"].lower() for r in rows}


def validate_sql(sql: str, tablas_permitidas: set[str]) -> str:
    """Valida el SQL y devuelve una versión segura (con LIMIT). Lanza SQLRechazado si no pasa."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SQLRechazado("La consulta está vacía.")
    if re.search(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", sql, re.IGNORECASE):
        raise SQLRechazado("No se permite escribir archivos (INTO OUTFILE/DUMPFILE).")

    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError as e:
        # Quita los códigos de color de terminal que sqlglot mete en el mensaje
        limpio = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
        raise SQLInvalido(f"SQL inválido: {limpio}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLRechazado("Solo se permite una sentencia por consulta.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLRechazado(f"Solo se permiten consultas SELECT (se recibió {tree.key.upper()}).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRechazado(f"Operación no permitida: {node.key.upper()}.")
        if isinstance(node, exp.Into):
            raise SQLRechazado("No se permite SELECT ... INTO.")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()
            if name in FORBIDDEN_FUNCTIONS:
                raise SQLRechazado(f"Función no permitida: {name}.")

    # Solo tablas de nuestra base (ignora los alias de CTEs definidos en la misma consulta)
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.db and table.db.lower() != settings.mysql_database.lower():
            raise SQLRechazado(f"No se permite consultar otra base de datos: {table.db}.")
        if name not in tablas_permitidas and name not in ctes:
            raise SQLRechazado(f"Tabla no permitida o inexistente: {table.name}.")

    # Capa 3: siempre con LIMIT, y nunca mayor a MAX_ROWS
    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        current = int(limit.expression.this)
    if current is None or current > MAX_ROWS:
        tree = tree.limit(MAX_ROWS, copy=False) if hasattr(tree, "limit") else tree

    return tree.sql(dialect="mysql")


def execute_safe(sql: str) -> list[dict]:
    """Ejecuta con tiempo máximo (capa 3) usando el usuario de solo lectura (capa 1)."""
    with db.get_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {MAX_EXECUTION_MS}")  # MySQL (milisegundos)
        except Exception:  # noqa: BLE001
            cur.execute(f"SET SESSION max_statement_time = {MAX_EXECUTION_MS / 1000}")  # MariaDB (segundos)
        cur.execute(sql)
        return list(cur.fetchall())


# ---------- Generación con el LLM ----------

def schema_for_llm() -> str:
    """Esquema real de la base, leído de information_schema, con llaves foráneas."""
    fks = db.run_query(
        """
        SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
        """,
        (settings.mysql_database,),
    )
    rel = "\n".join(
        f"- {r['TABLE_NAME']}.{r['COLUMN_NAME']} -> {r['REFERENCED_TABLE_NAME']}.{r['REFERENCED_COLUMN_NAME']}"
        for r in fks
    )
    return (f"Tablas:\n{db.get_schema()}\n\nRelaciones (únicas formas válidas de unir tablas):\n{rel}"
            f"\n\nValores reales de columnas de texto:\n{categorical_values()}")


def categorical_values(max_distinct: int = 15) -> str:
    """Valores reales de las columnas de texto con pocos valores distintos.

    Así el modelo sabe que el producto se llama "Bomba de infusión volumétrica BI-50"
    y no inventa filtros como nombre = 'bombas de infusión'. En una base grande se limitaría
    a columnas de catálogo; aquí se detectan solas por su número de valores distintos.
    """
    cols = db.run_query(
        """
        SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND DATA_TYPE IN ('varchar', 'char')
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        (settings.mysql_database,),
    )
    lines = []
    for c in cols:
        t, col = c["TABLE_NAME"], c["COLUMN_NAME"]
        # Los nombres vienen de information_schema (no del usuario), y se escapan con backticks
        rows = db.run_query(f"SELECT DISTINCT `{col}` AS v FROM `{t}` WHERE `{col}` IS NOT NULL LIMIT {max_distinct + 1}")
        if 0 < len(rows) <= max_distinct:
            lines.append(f"- {t}.{col}: " + ", ".join(repr(r["v"]) for r in rows))
    return "\n".join(lines) or "(ninguna)"


SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Eres un experto en MySQL 8. Convierte la pregunta del usuario en UNA consulta SELECT.\n"
     "Reglas:\n"
     "- Usa SOLO las tablas y columnas del esquema. No inventes columnas.\n"
     "- Solo SELECT. Nunca INSERT, UPDATE, DELETE ni DDL.\n"
     "- Une tablas SOLO con las relaciones indicadas (por ejemplo pedidos.producto_id = productos.id). "
     "Nunca compares el id de una tabla con el id de otra tabla distinta.\n"
     "- Para filtrar por texto libre (nombres, defectos) usa LIKE '%palabra clave%' con una palabra clave, "
     "no igualdad exacta. Revisa los valores reales listados abajo.\n"
     "- Usa LEFT JOIN cuando la información relacionada podría no existir (por ejemplo pedidos o inspecciones).\n"
     "- Los valores de columnas ENUM deben escribirse exactamente como aparecen en el esquema.\n"
     "- Devuelve columnas con nombres claros (usa alias en español si ayuda).\n"
     "- Los alias deben ser UNA sola palabra en snake_case, sin espacios ni acentos (por ejemplo kwh_por_m2).\n"
     "- Si la pregunta pide un total o promedio por edificio, mes u otro grupo, usa SUM o AVG con GROUP BY.\n"
     "- Glosario de energía: consumo por metro cuadrado (intensidad energética) = SUM(kwh) / area_construida_m2 "
     "del edificio, en kWh/m². Las áreas están en metros CUADRADOS (m2).\n"
     "- Desviación de un mes = (kwh - promedio del mes anterior y el siguiente) / ese promedio * 100. "
     "Para calcularla usa LAG(kwh) y LEAD(kwh) OVER (PARTITION BY edificio_id ORDER BY mes) en una subconsulta.\n"
     "  La base es MariaDB: LAG y LEAD llevan UN solo argumento (nunca LAG(kwh, 1, 0)) y sin SUM. Plantilla:\n"
     "  SELECT mes, kwh, ROUND((kwh - (anterior + siguiente) / 2) / ((anterior + siguiente) / 2) * 100, 1) AS desviacion_pct\n"
     "  FROM (SELECT c.mes, c.kwh, LAG(c.kwh) OVER (PARTITION BY c.edificio_id ORDER BY c.mes) AS anterior,\n"
     "        LEAD(c.kwh) OVER (PARTITION BY c.edificio_id ORDER BY c.mes) AS siguiente\n"
     "        FROM consumos_energia c JOIN edificios e ON c.edificio_id = e.id WHERE e.nombre LIKE '%palabra%') t\n"
     "  WHERE YEAR(mes) = 2025 ORDER BY mes\n"     "- Responde SOLO con el SQL dentro de un bloque ```sql```, sin explicación.\n\n"
     "Esquema:\n{esquema}"),
    ("human", "{pregunta}"),
])

RETRY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Eres un experto en MySQL 8. La consulta anterior falló. Corrígela.\n"
     "Responde SOLO con el SQL corregido dentro de un bloque ```sql```.\n\nEsquema:\n{esquema}"),
    ("human", "Pregunta: {pregunta}\n\nSQL anterior:\n{sql}\n\nError:\n{error}"),
])

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Eres el asistente de una consultora de eficiencia energética y gestión de proyectos BIM. "
     "Responde la pregunta usando ÚNICAMENTE los datos del resultado de la consulta. "
     "Si el resultado está vacío, dilo claramente. No inventes datos. "
     "Responde en español. Si hay varios registros, usa una lista e incluye en cada renglón "
     "los datos relevantes del resultado (por ejemplo cliente, producto y cantidad), no solo el identificador."),
    ("human", "Pregunta: {pregunta}\n\nSQL ejecutado:\n{sql}\n\nResultado ({n} filas):\n{filas}"),
])


def extract_sql(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()


def _json_default(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def generate_and_run(pregunta: str, llm: BaseChatModel | None = None) -> dict:
    """Genera el SQL, lo valida y lo ejecuta (sin resumir). Lo usa /sql/preguntar y el agente del paso 4."""
    llm = llm or get_llm()
    esquema = schema_for_llm()
    tablas = allowed_tables()

    sql_generado = extract_sql((SQL_PROMPT | llm).invoke({"esquema": esquema, "pregunta": pregunta}).content)
    intentos = [sql_generado]

    # Un reintento: si MySQL marca error, el modelo ve el error y corrige
    for intento in range(2):
        try:
            sql_seguro = validate_sql(sql_generado, tablas)
            filas = execute_safe(sql_seguro)
            return {"ok": True, "sql": sql_seguro, "intentos": intentos,
                    "filas": json.loads(json.dumps(filas, default=_json_default))}
        except SQLRechazado as e:
            # Un rechazo por seguridad no se reintenta: se reporta tal cual
            return {"ok": False, "rechazado": True, "sql": sql_generado, "intentos": intentos,
                    "motivo": str(e), "filas": []}
        except Exception as e:  # noqa: BLE001 — error de MySQL (columna inexistente, sintaxis...)
            if intento == 1:
                return {"ok": False, "rechazado": False, "sql": sql_generado, "intentos": intentos,
                        "motivo": str(e), "filas": []}
            sql_generado = extract_sql((RETRY_PROMPT | llm).invoke(
                {"esquema": esquema, "pregunta": pregunta, "sql": sql_generado, "error": str(e)}).content)
            intentos.append(sql_generado)


def ask(pregunta: str, llm: BaseChatModel | None = None) -> dict:
    llm = llm or get_llm()
    r = generate_and_run(pregunta, llm)
    if not r["ok"]:
        msg = (f"Consulta bloqueada por seguridad: {r['motivo']}" if r["rechazado"]
               else "No pude generar una consulta válida para esa pregunta.")
        return {"pregunta": pregunta, **r, "respuesta": msg}

    filas_json = json.dumps(r["filas"][:50], ensure_ascii=False)
    respuesta = (ANSWER_PROMPT | llm).invoke(
        {"pregunta": pregunta, "sql": r["sql"], "n": len(r["filas"]), "filas": filas_json}).content
    return {"pregunta": pregunta, **r, "respuesta": respuesta}
