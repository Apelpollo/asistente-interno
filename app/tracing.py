"""Observabilidad con Langfuse.

Es opcional: si el .env no trae LANGFUSE_PUBLIC_KEY y LANGFUSE_SECRET_KEY,
todo funciona igual, solo que sin tracing.

Qué se registra en cada pregunta al agente:
  - un trace "agente" con la pregunta, la respuesta y el modelo usado
  - dentro, cada llamada al LLM (prompt, respuesta, tokens, latencia)
  - cada herramienta (buscar_documentos / consultar_base_datos) con sus argumentos y resultado
  - dentro de consultar_base_datos, la generación del SQL y el reintento si lo hubo
"""
import os
from contextlib import contextmanager


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def langchain_config() -> dict:
    """Config para pasarle a .invoke(): engancha el callback de Langfuse a LangChain."""
    if not enabled():
        return {}
    from langfuse.langchain import CallbackHandler

    return {"callbacks": [CallbackHandler()]}


@contextmanager
def observar(nombre: str, entrada: dict, tags: list[str] | None = None, metadata: dict | None = None):
    """Abre un trace de tipo agente. Todo lo que pase adentro queda anidado en él."""
    if not enabled():
        yield None
        return
    from langfuse import get_client, propagate_attributes

    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="agent", name=nombre, input=entrada,
                                               metadata=metadata) as obs:
        with propagate_attributes(tags=tags or []):
            yield obs
