# Asistente interno con RAG y agente SQL

Asistente para una empresa manufacturera de equipo electro-médico. Responde preguntas usando:

- **Documentos internos** (manuales, procedimientos) mediante RAG con ChromaDB. *(paso 2)*
- **Datos en vivo de MySQL** mediante text-to-SQL de solo lectura. *(paso 3)*
- Un **agente con tool calling** que decide qué herramienta usar. *(paso 4)*

## Estado

- [x] Paso 1: FastAPI + MySQL en Docker Compose, datos de ejemplo, usuario de solo lectura
- [x] Paso 2: RAG (chunking por sección, embeddings con Ollama, ChromaDB, citas de fuente)
- [ ] Paso 3: Agente SQL con validación en tres capas
- [ ] Paso 4: Agente con tool calling
- [ ] Paso 5: Demo y diagrama

## Cómo correrlo

```bash
cp .env.example .env        # ajusta contraseñas si quieres
docker compose up -d --build
```

- Documentación interactiva: http://localhost:8000/docs
- `GET /health` — API y MySQL
- `GET /schema` — esquema que verá el LLM
- `GET /productos/bajo-stock` — consulta de ejemplo

> Si cambias algo en `db/init/`, borra el volumen para que MySQL lo vuelva a cargar:
> `docker compose down -v && docker compose up -d --build`

## Requisitos

- Docker Desktop
- [Ollama](https://ollama.com) corriendo en tu máquina, con los modelos:

```bash
ollama pull nomic-embed-text   # embeddings
ollama pull qwen2.5:7b         # modelo que responde (~4.7 GB de VRAM)
```

## RAG sobre documentos internos (paso 2)

Documentos de ejemplo en `docs/`: Manual de Calidad MQ-01, Procedimiento de retención PR-07 y Política de garantías PG-03.

1. `POST /rag/ingest` — carga los documentos en ChromaDB (correrlo cada vez que cambien los `.md`).
2. `POST /rag/buscar` — búsqueda semántica sin LLM; muestra qué fragmentos recupera.
3. `POST /rag/preguntar` — respuesta del LLM citando los fragmentos `[1]`, `[2]`… y la lista de fuentes.

Cómo funciona:

| Etapa | Qué hace | Por qué |
|---|---|---|
| Chunking | Corta por títulos `#`/`##` y luego en pedazos de ~800 caracteres con 100 de traslape | Cada fragmento sabe de qué documento y sección viene; el traslape evita perder contexto en los cortes |
| Embeddings | `nomic-embed-text` en Ollama convierte cada fragmento en un vector | Textos con significado parecido quedan cerca aunque usen otras palabras |
| ChromaDB | Guarda vectores + metadatos en `chroma_data/` | Búsqueda por similitud rápida y persistente |
| Recuperación | Top 6 fragmentos más parecidos a la pregunta | Solo se le pasa al modelo lo relevante |
| Generación | `qwen2.5:7b` con temperatura 0; el prompt obliga a copiar primero la evidencia textual y luego concluir | Respuestas fieles al documento, sin inventar ni reinterpretar |

### Lo que aprendí ajustándolo

Pregunta de prueba: *"¿Qué pasa si una bomba de infusión sale con flujo fuera de tolerancia?"*
Respuesta correcta según los documentos: defecto **crítico** → **retener la orden** (PR-07).

1. **Primer intento (llama3.1:8b):** recuperaba el fragmento correcto, pero respondía "defecto mayor". El error no estaba en la búsqueda sino en la generación.
2. **Prefijos de nomic-embed-text:** el modelo de embeddings espera `search_document:` y `search_query:`. Al agregarlos se fueron fragmentos irrelevantes de garantías.
3. **Prompt de evidencia primero:** obligar al modelo a copiar la línea textual antes de concluir. Llama la recortaba o copiaba la línea equivocada.
4. **Cambio de modelo (qwen2.5:7b):** con el mismo prompt, copió la línea completa con su categoría y concluyó correctamente.

Pendiente: el procedimiento PR-07 no aparece en los fragmentos recuperados porque la pregunta habla de bombas y el PR-07 de "defecto crítico". Conectar ambos requiere dos búsquedas encadenadas, que es lo que resuelve el agente del paso 4.

## Datos de ejemplo

Base `planta` con cinco tablas: `clientes`, `productos`, `ordenes_produccion`, `pedidos`, `inspecciones_calidad`.
Hay casos interesantes para preguntar después: productos bajo stock mínimo, una orden **retenida** por calidad,
inspecciones rechazadas con su defecto, pedidos abiertos de producto sin stock suficiente.

## Seguridad: capa 1

El agente se conecta con `agente_lectura`, un usuario de MySQL que **solo tiene `SELECT`**.
Aunque el modelo generara un `DELETE`, MySQL lo rechaza:

```
DELETE command denied to user 'agente_lectura'
```

Las capas 2 (validar el SQL generado antes de ejecutarlo) y 3 (límite de filas y timeout) llegan en el paso 3.
