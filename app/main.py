"""API del asistente interno."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler  # NUEVO
from slowapi.errors import RateLimitExceeded  # NUEVO
from slowapi.middleware import SlowAPIMiddleware  # NUEVO

from app import agent, db, rag, sql_agent, voice
from app.ratelimit import limiter  # NUEVO
from app.security import require_api_key  # NUEVO

app = FastAPI(
    title="Asistente interno",
    description="Asistente con RAG sobre documentos internos y agente SQL de solo lectura sobre MySQL.",
    version="0.6.0",
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


# ---------- Paso 8: voz ----------

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB


def _leer_audio(audio: UploadFile) -> bytes:
    datos = audio.file.read()
    if not datos:
        raise HTTPException(status_code=422, detail="El archivo de audio está vacío.")
    if len(datos) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="El audio pasa de 10 MB.")
    return datos


@api.post("/voz/transcribir")
@limiter.limit("10/minute")
def voz_transcribir(request: Request, audio: UploadFile = File(...)):
    """Solo voz -> texto (Whisper local): útil para revisar qué entendió antes de usar el agente."""
    try:
        return voice.transcribir(_leer_audio(audio))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@api.post("/voz/preguntar")
@limiter.limit("5/minute")  # Whisper + agente + TTS: es el endpoint más caro
def voz_preguntar(request: Request, audio: UploadFile = File(...), formato: str = "audio"):
    """Pregunta hablada (español o inglés) -> agente -> respuesta hablada en el mismo idioma.

    formato=audio (default): devuelve MP3; la transcripción y la respuesta van en headers X-*.
    formato=json: devuelve transcripción, respuesta y pasos del agente, sin audio.
    """
    try:
        stt = voice.transcribir(_leer_audio(audio))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if len(stt["texto"]) < 3:
        raise HTTPException(status_code=422, detail="No se entendió ninguna pregunta en el audio.")

    try:
        r = agent.run(stt["texto"], idioma=stt["idioma"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Error en el agente (¿Ollama está corriendo?): {e}")

    resultado = {"transcripcion": stt["texto"], "idioma": stt["idioma"],
                 "respuesta": r["respuesta"], "herramientas": [p["herramienta"] for p in r["pasos"]]}
    if formato == "json" or not voice.tts_disponible():
        if not voice.tts_disponible():
            resultado["aviso"] = "Sin AZURE_SPEECH_KEY: respuesta solo en texto."
        return resultado

    try:
        mp3 = voice.sintetizar(r["respuesta"], stt["idioma"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Error en Azure AI Speech: {e}")
    return Response(content=mp3, media_type="audio/mpeg", headers={
        "X-Idioma": stt["idioma"],
        "X-Transcripcion": quote(stt["texto"]),          # URL-encoded: los headers no aceptan acentos
        "X-Respuesta": quote(r["respuesta"][:1500]),
    })


# Siempre al final, después de definir todas las rutas
app.include_router(api)