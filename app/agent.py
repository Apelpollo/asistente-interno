"""Agente con tool calling: decide si consultar documentos, la base de datos o ambos.

A diferencia del RAG simple (una búsqueda y responde), el agente trabaja en ciclo:
  1. El LLM lee la pregunta y decide qué herramienta llamar y con qué argumentos.
  2. Ejecutamos la herramienta y le devolvemos el resultado.
  3. El LLM decide: ¿llamo otra herramienta con lo que aprendí, o ya puedo responder?
Así puede encadenar pasos, por ejemplo: base de datos (orden retenida) -> manual
(el defecto es crítico) -> procedimiento PR-07 (qué se hace con una orden retenida).
"""
import json
import unicodedata

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app import rag, sql_agent, tracing
from app.config import settings

MAX_PASOS = 6  # tope de llamadas a herramientas, para que el agente nunca cicle sin fin
MAX_RECORDATORIOS = 2  # veces que se le recuerda usar la herramienta en vez de anunciarla

# Frases con las que el modelo anuncia una acción en lugar de ejecutarla (se comparan sin acentos)
ANUNCIOS = ("consultare", "buscare", "revisare", "usare", "voy a consultar", "voy a buscar", "voy a revisar",
            "voy a usar", "a continuacion consult", "a continuacion busc", "procedere a", "verificare",
            "podrias proporcionar", "puedes proporcionar", "necesito conocer", "necesito saber")

RECORDATORIO = ("Escribiste que vas a consultar algo, pero no llamaste ninguna herramienta. "
                "Si necesitas más información, llama la herramienta ahora. "
                "Si ya tienes todo, da la respuesta final completa.")

RECORDATORIO_SIN_HERRAMIENTAS = (
    "Respondiste sin usar ninguna herramienta. No le pidas datos al usuario ni respondas de memoria: "
    "busca tú mismo con consultar_base_datos (datos de la planta) y buscar_documentos (reglas y procedimientos). "
    "Si la petición es modificar o borrar datos, explica que solo tienes acceso de lectura.")


def anuncia_sin_actuar(texto: str) -> bool:
    """True si el modelo dijo que iba a usar una herramienta pero no la llamó."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return any(a in t for a in ANUNCIOS)

SYSTEM_PROMPT = """Eres el asistente interno de una planta de equipo electro-médico.
Tienes dos herramientas:
- buscar_documentos: manuales, procedimientos y políticas internas (calidad, retención de órdenes, garantías).
  Úsala para preguntas de "qué se hace", "cuál es la regla", "qué dice el procedimiento".
- consultar_base_datos: datos en vivo de la planta (productos, stock, pedidos, clientes, órdenes de producción, inspecciones).
  Úsala para preguntas de "cuántos", "cuáles", "qué pedidos", "qué órdenes", estados actuales.

Reglas:
1. Usa las herramientas; no respondas de memoria datos de la planta ni reglas internas.
2. Si la pregunta habla de algo concreto de la planta ("la orden", "el pedido", "ese producto", "los clientes"),
   PRIMERO usa consultar_base_datos para obtener los datos reales de ese caso. Pide también lo relacionado:
   para una orden, sus inspecciones (defecto) y los pedidos abiertos con sus clientes de ese mismo producto.
   DESPUÉS usa buscar_documentos con los detalles que encontraste (producto, tipo de defecto).
3. Si la pregunta combina datos y reglas, usa ambas herramientas. Una sola llamada casi nunca basta en ese caso.
4. Si un documento menciona otro procedimiento o documento (por ejemplo "conforme al PR-07"), búscalo también.
5. Escribe consultas propias y específicas para el caso; no repitas textos genéricos.
6. Si un resultado no trae lo que necesitas o trae 0 filas, reformula y vuelve a buscar con algo más general.
7. Nunca supongas datos ("asumiremos..."). Si no los encontraste después de reintentar, dilo.

Respuesta final:
- Empieza con los datos concretos del caso (por ejemplo: qué orden, qué producto, qué defecto, qué clientes afectados).
- Luego explica qué procede según los documentos, aplicado a ESE caso.
- Cita las fuentes SOLO como texto: nombre del documento y sección para reglas, y "base de datos" para datos.
  NUNCA escribas enlaces ni URLs: los documentos no tienen dirección web.
- Si después de buscar no encuentras la información, dilo claramente. No inventes.
Responde en español, claro y directo."""


def build_tools(sql_llm: BaseChatModel | None = None, embeddings: Embeddings | None = None):
    """Crea las herramientas. Los parámetros permiten inyectar modelos falsos en pruebas."""

    @tool
    def buscar_documentos(consulta: str) -> str:
        """Busca en los manuales, procedimientos y políticas internas de la planta
        (calidad, clasificación de defectos, retención de órdenes, garantías).
        La consulta debe incluir los detalles concretos del caso: producto, tipo de defecto, situación."""
        docs = rag.retrieve(consulta, k=4, embeddings=embeddings)
        if not docs:
            return "No hay documentos cargados. Ejecuta /rag/ingest."
        return "\n\n".join(
            f'[{d.metadata["documento"]} — {d.metadata["seccion"]}]\n{d.page_content}' for d in docs
        )

    @tool
    def consultar_base_datos(pregunta: str) -> str:
        """Consulta los datos en vivo de la planta en MySQL (solo lectura): productos y stock, pedidos y clientes,
        órdenes de producción y su estado, inspecciones de calidad y defectos.
        El argumento es una pregunta en lenguaje natural (español), NO código SQL: el SQL se genera y valida aparte.
        Pide todos los datos útiles del caso, incluyendo tablas relacionadas."""
        r = sql_agent.generate_and_run(pregunta, llm=sql_llm)
        if not r["ok"]:
            return f"No se pudo consultar: {r['motivo']}"
        if not r["filas"]:
            return (f"SQL ejecutado: {r['sql']}\nFilas (0): []\n"
                    "AVISO: 0 filas no significa que no exista. Probablemente el filtro fue demasiado estricto. "
                    "Vuelve a llamar a consultar_base_datos con una pregunta más general "
                    "(por ejemplo, solo por estado, sin nombres exactos).")
        filas = r["filas"][:30]
        return f"SQL ejecutado: {r['sql']}\nFilas ({len(r['filas'])}):\n{json.dumps(filas, ensure_ascii=False)}"

    return [buscar_documentos, consultar_base_datos]


def run(pregunta: str, llm=None, sql_llm: BaseChatModel | None = None,
        embeddings: Embeddings | None = None) -> dict:
    """Corre el agente dentro de un trace de Langfuse (si está configurado)."""
    with tracing.observar("agente", {"pregunta": pregunta},
                          tags=["agente", settings.llm_model],
                          metadata={"modelo": settings.llm_model}) as obs:
        resultado = _run(pregunta, llm=llm, sql_llm=sql_llm, embeddings=embeddings)
        if obs is not None:
            obs.update(output={"respuesta": resultado["respuesta"],
                               "herramientas": [p["herramienta"] for p in resultado["pasos"]],
                               "recordatorios": resultado.get("recordatorios", 0),
                               "tope_alcanzado": resultado.get("tope_alcanzado", False)})
        return resultado


def _run(pregunta: str, llm=None, sql_llm: BaseChatModel | None = None,
         embeddings: Embeddings | None = None) -> dict:
    cfg = tracing.langchain_config()  # {} si Langfuse no está configurado
    tools = build_tools(sql_llm=sql_llm, embeddings=embeddings)
    tools_by_name = {t.name: t for t in tools}
    llm = llm or rag.get_llm().bind_tools(tools)

    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(pregunta)]
    pasos: list[dict] = []

    recordatorios = 0
    for _ in range(MAX_PASOS):
        ai: AIMessage = llm.invoke(messages, config=cfg)
        messages.append(ai)

        if not ai.tool_calls:
            # Fallas detectadas con tracing + evals: el modelo a veces (1) responde o le pide datos al usuario
            # sin haber usado ninguna herramienta, o (2) anuncia "consultaré los documentos" y termina
            # sin llamarla. En ambos casos se le recuerda (máximo 2 veces) que use las herramientas.
            if recordatorios < MAX_RECORDATORIOS:
                if not pasos:  # quiso terminar sin haber usado ninguna herramienta
                    recordatorios += 1
                    messages.append(HumanMessage(RECORDATORIO_SIN_HERRAMIENTAS))
                    continue
                if anuncia_sin_actuar(ai.content):  # dijo "consultaré..." pero no llamó la herramienta
                    recordatorios += 1
                    messages.append(HumanMessage(RECORDATORIO))
                    continue
            return {"pregunta": pregunta, "respuesta": ai.content, "pasos": pasos,
                    "recordatorios": recordatorios}

        for call in ai.tool_calls:
            herramienta = tools_by_name.get(call["name"])
            if herramienta is None:
                resultado = f"Herramienta desconocida: {call['name']}"
            else:
                try:
                    resultado = herramienta.invoke(call["args"], config=cfg)
                except Exception as e:  # noqa: BLE001
                    resultado = f"Error al ejecutar la herramienta: {e}"
            pasos.append({
                "herramienta": call["name"],
                "argumentos": call["args"],
                "resultado": resultado[:600],
            })
            messages.append(ToolMessage(content=resultado, tool_call_id=call["id"]))

    # Se alcanzó el tope: se le pide al modelo que responda con lo que ya tiene
    messages.append(HumanMessage("Ya no puedes usar más herramientas. Responde con la información que obtuviste."))
    final = (llm.bound if hasattr(llm, "bound") else llm).invoke(messages, config=cfg)
    return {"pregunta": pregunta, "respuesta": final.content, "pasos": pasos, "tope_alcanzado": True}