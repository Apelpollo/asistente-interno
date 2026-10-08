"""RAG sobre documentos internos.

Flujo:
  ingesta:  .md  ->  chunks por sección  ->  embeddings (Ollama)  ->  ChromaDB
  consulta: pregunta -> embedding -> top-k chunks similares -> LLM responde citando [n]
"""
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.config import settings


# ---------- Modelos ----------

class PrefixedEmbeddings(Embeddings):
    """nomic-embed-text fue entrenado con prefijos de tarea.

    Los documentos deben ir con "search_document: " y las preguntas con "search_query: ".
    Sin ellos, el modelo mezcla ambos tipos de texto y la búsqueda pierde precisión.
    """

    def __init__(self, base: Embeddings):
        self.base = base

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.base.embed_documents([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.base.embed_query(f"search_query: {text}")


def get_embeddings() -> Embeddings:
    base = OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)
    return PrefixedEmbeddings(base) if "nomic" in settings.embed_model else base


def get_llm() -> BaseChatModel:
    # temperature=0: queremos respuestas fieles al documento, no creativas
    return ChatOllama(model=settings.llm_model, base_url=settings.ollama_base_url, temperature=0)


def get_store(embeddings: Embeddings | None = None) -> Chroma:
    return Chroma(
        collection_name=settings.chroma_collection,
        embedding_function=embeddings or get_embeddings(),
        persist_directory=settings.chroma_dir,
    )


# ---------- Ingesta ----------

def load_and_split(docs_dir: str | None = None) -> list[Document]:
    """Parte cada documento en chunks, conservando de qué documento y sección viene.

    1. MarkdownHeaderTextSplitter corta por títulos (#, ##), así cada chunk sabe su sección.
    2. RecursiveCharacterTextSplitter corta secciones largas en pedazos de ~chunk_size
       caracteres con traslape, para no perder contexto en los cortes.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "documento"), ("##", "seccion")],
        strip_headers=False,
    )
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    chunks: list[Document] = []
    for path in sorted(Path(docs_dir or settings.docs_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sections = header_splitter.split_text(text)
        for doc in size_splitter.split_documents(sections):
            doc.metadata["archivo"] = path.name
            doc.metadata.setdefault("documento", path.stem)
            doc.metadata.setdefault("seccion", "General")
            chunks.append(doc)
    return chunks


def ingest(embeddings: Embeddings | None = None, docs_dir: str | None = None) -> dict:
    """Reconstruye la colección desde cero (borra y vuelve a cargar)."""
    store = get_store(embeddings)
    store.reset_collection()
    chunks = load_and_split(docs_dir)
    ids = [f'{c.metadata["archivo"]}::{i}' for i, c in enumerate(chunks)]
    store.add_documents(chunks, ids=ids)
    archivos = sorted({c.metadata["archivo"] for c in chunks})
    return {"documentos": len(archivos), "chunks": len(chunks), "archivos": archivos}


# ---------- Consulta ----------

PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Eres el asistente interno de una planta de equipo electro-médico.\n"
     "Reglas:\n"
     "1. Responde SOLO con la información de los fragmentos de contexto. No inventes ni supongas.\n"
     "2. Si el contexto menciona el caso de la pregunta como ejemplo de una categoría "
     "(por ejemplo, un tipo de defecto), usa EXACTAMENTE esa categoría. No la reinterpretes.\n"
     "3. Si una regla lleva a otro procedimiento (por ejemplo, retener una orden), menciónalo.\n"
     "4. Cita cada dato con el número del fragmento entre corchetes, por ejemplo [1] o [2][3].\n"
     "5. Si el contexto no contiene la respuesta, di exactamente: "
     "\"No encontré esa información en los documentos internos.\"\n\n"
     "Formato de respuesta (en español):\n"
     "Evidencia: copia TEXTUALMENTE la línea COMPLETA del contexto que menciona el caso de la pregunta, "
     "desde su inicio (incluida la etiqueta o categoría con la que empieza, por ejemplo \"Crítico:\"), "
     "sin recortarla ni cambiar palabras, cada una con su [n].\n"
     "Respuesta: conclusión breve que se apoye ÚNICAMENTE en la evidencia anterior."),
    ("human", "Contexto:\n{contexto}\n\nPregunta: {pregunta}"),
])


def retrieve(pregunta: str, k: int | None = None, embeddings: Embeddings | None = None) -> list[Document]:
    return get_store(embeddings).similarity_search(pregunta, k=k or settings.top_k)


def format_context(docs: list[Document]) -> str:
    return "\n\n".join(
        f'[{i}] ({d.metadata["documento"]} — {d.metadata["seccion"]})\n{d.page_content}'
        for i, d in enumerate(docs, start=1)
    )


def answer(pregunta: str, llm: BaseChatModel | None = None, embeddings: Embeddings | None = None) -> dict:
    docs = retrieve(pregunta, embeddings=embeddings)
    if not docs:
        return {"respuesta": "No hay documentos cargados. Ejecuta primero /rag/ingest.", "fuentes": []}

    chain = PROMPT | (llm or get_llm())
    result = chain.invoke({"contexto": format_context(docs), "pregunta": pregunta})

    fuentes = [
        {
            "n": i,
            "documento": d.metadata["documento"],
            "seccion": d.metadata["seccion"],
            "archivo": d.metadata["archivo"],
            "fragmento": d.page_content[:300],
        }
        for i, d in enumerate(docs, start=1)
    ]
    return {"respuesta": result.content, "fuentes": fuentes}
