"""Retrieval tuning: compara estrategias de chunking, tamaño, traslape, prefijos y top-k.

Evalúa SOLO la búsqueda (sin LLM), así que es rápido y no depende de cómo redacte el modelo.
Para cada configuración arma una colección temporal en memoria (no toca chroma_data/),
hace las preguntas de evals/retrieval_dataset.py y mide:

    hit@k     fracción de preguntas donde la "aguja" aparece en alguno de los k chunks
    MRR       qué tan arriba sale el primer chunk correcto (1 = siempre en el lugar 1)
    contexto  caracteres promedio que se le mandarían al LLM con ese k (costo y ruido)

Uso (dentro del contenedor):
    docker compose exec api python -m evals.tune_retrieval

Guarda evals/resultados/retrieval_tuning.json y retrieval_tuning.md (tabla para el README).
"""
import json
import time
import unicodedata
import uuid
from itertools import product
from pathlib import Path

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from app import rag
from app.config import settings
from evals.retrieval_dataset import CASOS

ESTRATEGIAS = ["secciones", "solo_tamano"]   # secciones = títulos markdown + tamaño (lo actual)
TAMANOS = [200, 400, 800]
TRASLAPES = [0.0, 0.125]                      # fracción del tamaño; 0.125 * 800 = 100 (lo actual)
KS = [1, 2, 3, 4, 6]
K_MAX = max(KS)
ACTUAL = {"estrategia": "secciones", "chunk_size": settings.chunk_size,
          "chunk_overlap": settings.chunk_overlap, "prefijos": True, "k": settings.top_k}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto).lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def validar_dataset():
    """Si alguien edita docs/ y una aguja ya no existe, la métrica mentiría: mejor fallar."""
    for c in CASOS:
        texto = normalizar((Path(settings.docs_dir) / c["archivo"]).read_text(encoding="utf-8"))
        if normalizar(c["aguja"]) not in texto:
            raise SystemExit(f"La aguja '{c['aguja']}' ya no está en {c['archivo']}: actualiza retrieval_dataset.py")


def evaluar_config(estrategia, chunk_size, overlap, prefijos, base, vectores_pregunta):
    embeddings = rag.PrefixedEmbeddings(base) if prefijos else base
    chunks = rag.load_and_split(chunk_size=chunk_size, chunk_overlap=overlap,
                                por_secciones=(estrategia == "secciones"))
    store = Chroma(collection_name=f"tuning_{uuid.uuid4().hex[:8]}", embedding_function=embeddings)
    try:
        store.add_documents(chunks)
        rangos, contextos = [], {k: [] for k in KS}
        for caso, vec in zip(CASOS, vectores_pregunta):
            docs = store.similarity_search_by_vector(vec, k=K_MAX)
            aguja = normalizar(caso["aguja"])
            rango = next((i for i, d in enumerate(docs, start=1) if aguja in normalizar(d.page_content)), None)
            rangos.append(rango)
            for k in KS:
                contextos[k].append(sum(len(d.page_content) for d in docs[:k]))
    finally:
        store.delete_collection()

    n = len(CASOS)
    filas = []
    for k in KS:
        filas.append({
            "estrategia": estrategia, "chunk_size": chunk_size, "chunk_overlap": overlap,
            "prefijos": prefijos, "k": k, "chunks_totales": len(chunks),
            "hit_at_k": round(sum(1 for r in rangos if r and r <= k) / n, 3),
            "mrr": round(sum(1 / r for r in rangos if r and r <= k) / n, 3),
            "contexto_prom": round(sum(contextos[k]) / n),
            "fallas": [c["pregunta"] for c, r in zip(CASOS, rangos) if not (r and r <= k)],
        })
    return filas


def main():
    validar_dataset()
    base = OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)
    usar_prefijos = [True, False] if "nomic" in settings.embed_model else [False]

    # Las preguntas se convierten a vector una sola vez por modo de prefijo
    vectores = {}
    for p in usar_prefijos:
        emb = rag.PrefixedEmbeddings(base) if p else base
        vectores[p] = [emb.embed_query(c["pregunta"]) for c in CASOS]

    configs = list(product(ESTRATEGIAS, TAMANOS, TRASLAPES, usar_prefijos))
    print(f"{len(CASOS)} preguntas x {len(configs)} configuraciones x k={KS} (embeddings con {settings.embed_model})")
    inicio = time.perf_counter()
    filas = []
    for i, (estrategia, size, frac, prefijos) in enumerate(configs, start=1):
        overlap = int(size * frac)
        print(f"  [{i:2}/{len(configs)}] {estrategia:11} size={size:4} overlap={overlap:3} prefijos={prefijos}")
        filas += evaluar_config(estrategia, size, overlap, prefijos, base, vectores[prefijos])

    def es_actual(f):
        return all(f[c] == ACTUAL[c] for c in ACTUAL)

    actual = next((f for f in filas if es_actual(f)), None)
    # Mejor = máximo hit@k; a igualdad, mayor MRR; a igualdad, menos contexto
    ranking = sorted(filas, key=lambda f: (-f["hit_at_k"], -f["mrr"], f["contexto_prom"]))
    mejor = ranking[0]

    print(f"\nListo en {time.perf_counter() - inicio:.0f}s\n")
    encabezado = "| estrategia | size | overlap | prefijos | k | hit@k | MRR | contexto (chars) |"
    sep = "|---|---|---|---|---|---|---|---|"
    def fila_md(f, marca=""):
        return (f"| {f['estrategia']}{marca} | {f['chunk_size']} | {f['chunk_overlap']} | "
                f"{'sí' if f['prefijos'] else 'no'} | {f['k']} | {f['hit_at_k']:.2f} | {f['mrr']:.2f} | {f['contexto_prom']} |")

    tabla = [encabezado, sep] + [fila_md(f, " ⬅ actual" if es_actual(f) else "") for f in ranking[:10]]
    if actual and actual not in ranking[:10]:
        tabla.append(fila_md(actual, " ⬅ actual"))
    print("\n".join(tabla))

    if actual:
        print(f"\nActual: hit@{actual['k']}={actual['hit_at_k']:.2f}, MRR={actual['mrr']:.2f}, contexto={actual['contexto_prom']} chars")
    print(f"Mejor:  {mejor['estrategia']} size={mejor['chunk_size']} overlap={mejor['chunk_overlap']} "
          f"prefijos={mejor['prefijos']} k={mejor['k']} -> hit@k={mejor['hit_at_k']:.2f}, "
          f"MRR={mejor['mrr']:.2f}, contexto={mejor['contexto_prom']} chars")
    if mejor["fallas"]:
        print("Preguntas que la mejor configuración no encuentra:", *mejor["fallas"], sep="\n  - ")

    # Ablación de prefijos: misma config que la mejor, solo cambiando prefijos
    if len(usar_prefijos) == 2:
        par = next(f for f in filas if f["prefijos"] != mejor["prefijos"] and
                   all(f[c] == mejor[c] for c in ("estrategia", "chunk_size", "chunk_overlap", "k")))
        print(f"Misma config con prefijos={par['prefijos']}: hit@k={par['hit_at_k']:.2f}, MRR={par['mrr']:.2f}")

    salida = Path(__file__).parent / "resultados"
    salida.mkdir(exist_ok=True)
    (salida / "retrieval_tuning.json").write_text(json.dumps(
        {"embed_model": settings.embed_model, "preguntas": len(CASOS), "actual": actual,
         "mejor": mejor, "resultados": ranking}, ensure_ascii=False, indent=2), encoding="utf-8")
    (salida / "retrieval_tuning.md").write_text("\n".join(tabla) + "\n", encoding="utf-8")
    print(f"\nGuardado en {salida / 'retrieval_tuning.json'} y retrieval_tuning.md")


if __name__ == "__main__":
    main()
