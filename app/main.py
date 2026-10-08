"""API del asistente interno."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import db, rag

app = FastAPI(
    title="Asistente interno",
    description="Asistente con RAG sobre documentos internos y agente SQL de solo lectura sobre MySQL.",
    version="0.2.0",
)


class Pregunta(BaseModel):
    pregunta: str = Field(..., min_length=3, examples=["¿Qué hago si una bomba de infusión sale con flujo fuera de tolerancia?"])


# ---------- Paso 1: base ----------

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


# ---------- Paso 2: RAG ----------

@app.post("/rag/ingest")
def rag_ingest():
    """Lee los .md de docs/, los parte en chunks, genera embeddings y los guarda en ChromaDB."""
    try:
        return rag.ingest()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error en la ingesta (¿Ollama está corriendo?): {e}")


@app.post("/rag/buscar")
def rag_buscar(body: Pregunta):
    """Solo búsqueda semántica, sin LLM: útil para ver qué chunks recupera."""
    docs = rag.retrieve(body.pregunta)
    return [{"documento": d.metadata["documento"], "seccion": d.metadata["seccion"], "fragmento": d.page_content}
            for d in docs]


@app.post("/rag/preguntar")
def rag_preguntar(body: Pregunta):
    """Responde con el LLM usando solo los documentos internos, citando las fuentes."""
    try:
        return rag.answer(body.pregunta)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error al consultar el modelo (¿Ollama está corriendo?): {e}")
