"""API del asistente interno — Paso 1: base FastAPI + MySQL."""
from fastapi import FastAPI, HTTPException

from app import db

app = FastAPI(
    title="Asistente interno",
    description="Asistente con RAG sobre documentos internos y agente SQL de solo lectura sobre MySQL.",
    version="0.1.0",
)


@app.get("/health")
def health():
    """Verifica que la API y la base de datos respondan."""
    try:
        db.run_query("SELECT 1 AS ok")
        return {"api": "ok", "mysql": "ok"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"MySQL no disponible: {e}")


@app.get("/schema")
def schema():
    """Esquema de la base (lo que verá el LLM en el paso 3)."""
    return {"schema": db.get_schema()}


@app.get("/productos/bajo-stock")
def productos_bajo_stock():
    """Consulta fija de ejemplo: productos por debajo de su stock mínimo."""
    return db.run_query(
        "SELECT sku, nombre, stock, stock_minimo FROM productos WHERE stock < stock_minimo ORDER BY stock"
    )
