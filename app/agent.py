"""Agente con tool calling: decide si consultar documentos, la base de datos o ambos.

A diferencia del RAG simple (una búsqueda y responde), el agente trabaja en ciclo:
  1. El LLM lee la pregunta y decide qué herramienta llamar y con qué argumentos.
  2. Ejecutamos la herramienta y le devolvemos el resultado.
  3. El LLM decide: ¿llamo otra herramienta con lo que aprendí, o ya puedo responder?
Así puede encadenar pasos, por ejemplo: base de datos (orden retenida) -> manual
(el defecto es crítico) -> procedimiento PR-07 (qué se hace con una orden retenida).
"""
import json

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app import rag, sql_agent

MAX_PASOS = 6  # tope de llamadas a herramientas, para que el agente nunca cicle sin fin

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
    tools = build_tools(sql_llm=sql_llm, embeddings=embeddings)
    tools_by_name = {t.name: t for t in tools}
    llm = llm or rag.get_llm().bind_tools(tools)

    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(pregunta)]
    pasos: list[dict] = []

    for _ in range(MAX_PASOS):
        ai: AIMessage = llm.invoke(messages)
        messages.append(ai)

        if not ai.tool_calls:
            return {"pregunta": pregunta, "respuesta": ai.content, "pasos": pasos}

        for call in ai.tool_calls:
            herramienta = tools_by_name.get(call["name"])
            if herramienta is None:
                resultado = f"Herramienta desconocida: {call['name']}"
            else:
                try:
                    resultado = herramienta.invoke(call["args"])
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
    final = (llm.bound if hasattr(llm, "bound") else llm).invoke(messages)
    return {"pregunta": pregunta, "respuesta": final.content, "pasos": pasos, "tope_alcanzado": True}