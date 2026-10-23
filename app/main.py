"""API del asistente interno."""
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request  # NUEVO: APIRouter, Depends, Request
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler  # NUEVO
from slowapi.errors import RateLimitExceeded  # NUEVO
from slowapi.middleware import SlowAPIMiddleware  # NUEVO

from app import agent, db, rag, sql_agent
from app.ratelimit import limiter  # NUEVO
from app.security import require_api_key  # NUEVO

app = FastAPI(
    title="Asistente interno",
    description="Asistente con RAG sobre documentos internos y agente SQL de solo lectura sobre MySQL.",
    version="0.5.0",  # NUEVO: subimos versión
)

# NUEVO: rate limiting global
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# NUEVO: todo lo de negocio vive bajo /v1 y exige API key
api = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


class Pregunta(BaseModel):
    pregunta: str = Field(..., min_length=3, examples=["¿Qué pedidos abiertos hay de productos que están por debajo de su stock mínimo?"])


# ---------- Paso 1: base ----------

@app.get("/health")  # se queda en app: sin versión, sin key
@limiter.exempt  # NUEVO: el healthcheck no cuenta para el límite
def health():
    """Verifica que la API y la base de datos respondan."""
    try:
        db.run_query("SELECT 1 AS ok")
        return {"api": "ok", "mysql": "ok"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"MySQL no disponible: {e}")


@api.get("/schema")  # NUEVO: @app -> @api
def schema():
    """Esquema de la base (lo que verá el LLM en el paso 3)."""
    return {"schema": db.get_schema()}


@api.get("/productos/bajo-stock")  # NUEVO: @app -> @api
def productos_bajo_stock():
    """Consulta fija de ejemplo: productos por debajo de su stock mínimo."""
    return db.run_query(
        "SELECT sku, nombre, stock, stock_minimo FROM productos WHERE stock < stock_minimo ORDER BY stock"
    )


# ---------- Paso 2: RAG ----------

@api.post("/rag/ingest")  # NUEVO: @app -> @api
@limiter.limit("2/minute")  # NUEVO: la ingesta es pesada
def rag_ingest(request: Request):  # NUEVO: request
    """Lee los .md de docs/, los parte en chunks, genera embeddings y los guarda en ChromaDB."""
    try:
        return rag.ingest()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error en la ingesta (¿Ollama está corriendo?): {e}")


@api.post("/rag/buscar")  # NUEVO: @app -> @api
@limiter.limit("30/minute")  # NUEVO: solo embeddings, más barato
def rag_buscar(request: Request, body: Pregunta):  # NUEVO: request
    """Solo búsqueda semántica, sin LLM: útil para ver qué chunks recupera."""
    docs = rag.retrieve(body.pregunta)
    return [{"documento": d.metadata["documento"], "seccion": d.metadata["seccion"], "fragmento": d.page_content}
            for d in docs]


@api.post("/rag/preguntar")  # NUEVO: @app -> @api
@limiter.limit("10/minute")  # NUEVO: llama al LLM
def rag_preguntar(request: Request, body: Pregunta):  # NUEVO: request
    """Responde con el LLM usando solo los documentos internos, citando las fuentes."""
    try:
        return rag.answer(body.pregunta)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error al consultar el modelo (¿Ollama está corriendo?): {e}")


# ---------- Paso 3: agente SQL ----------

class ConsultaSQL(BaseModel):
    sql: str = Field(..., examples=["DELETE FROM pedidos"])


@api.post("/sql/preguntar")  # NUEVO: @app -> @api
@limiter.limit("10/minute")  # NUEVO: llama al LLM
def sql_preguntar(request: Request, body: Pregunta):  # NUEVO: request
    """Pregunta en lenguaje natural -> el LLM genera SQL -> se valida -> se ejecuta -> respuesta con los datos."""
    try:
        return sql_agent.ask(body.pregunta)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error en el agente SQL (¿Ollama está corriendo?): {e}")


@api.post("/sql/validar")  # NUEVO: @app -> @api (usa el límite global de 60/min)
def sql_validar(body: ConsultaSQL):
    """Prueba la capa 2 sin LLM: muestra si un SQL se permite (y cómo queda) o por qué se bloquea."""
    try:
        return {"permitido": True, "sql_final": sql_agent.validate_sql(body.sql, sql_agent.allowed_tables())}
    except sql_agent.SQLRechazado as e:
        return {"permitido": False, "motivo": str(e)}


# ---------- Paso 4: agente con tool calling ----------

@api.post("/agente/preguntar")  # NUEVO: @app -> @api
@limiter.limit("10/minute")  # NUEVO: llama al LLM
def agente_preguntar(request: Request, body: Pregunta):  # NUEVO: request
    """El modelo decide qué herramientas usar (documentos, base de datos o ambas) y puede encadenarlas.
    La respuesta incluye los pasos que siguió: herramienta, argumentos y resultado."""
    try:
        return agent.run(body.pregunta)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error en el agente (¿Ollama está corriendo?): {e}")


# NUEVO: siempre al final, después de definir todas las rutas
app.include_router(api)