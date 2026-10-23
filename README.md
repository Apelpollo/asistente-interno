# Asistente interno con RAG y agente SQL

Asistente para una empresa manufacturera de equipo electro-médico. Responde preguntas usando:

- **Documentos internos** (manuales, procedimientos) mediante RAG con ChromaDB. *(paso 2)*
- **Datos en vivo de MySQL** mediante text-to-SQL de solo lectura. *(paso 3)*
- Un **agente con tool calling** que decide qué herramienta usar. *(paso 4)*
- **Voz** en español e inglés: Whisper local + Azure AI Speech. *(paso 8)*

## Estado

- [x] Paso 1: FastAPI + MySQL en Docker Compose, datos de ejemplo, usuario de solo lectura
- [x] Paso 2: RAG (chunking por sección, embeddings con Ollama, ChromaDB, citas de fuente)
- [x] Paso 3: Agente SQL con validación en tres capas y reintento con el error
- [x] Paso 4: Agente con tool calling que encadena documentos y base de datos
- [x] Paso 5: Observabilidad con Langfuse y evals automáticas
- [x] Paso 6: API de producción: versionado `/v1`, autenticación por API key y rate limiting
- [x] Paso 7: Retrieval tuning: 24 configuraciones medidas con hit@k y MRR
- [x] Paso 8: Voz: voz → texto (Whisper local) → agente → voz (Azure AI Speech)
- [ ] Paso 9: Demo y diagrama

## Cómo correrlo

```bash
cp .env.example .env        # ajusta contraseñas y pon tu API_KEYS
docker compose up -d --build
```

- Documentación interactiva: http://localhost:8000/docs
- `GET /health` — API y MySQL (sin versión ni llave, para monitoreo)
- `GET /v1/schema` — esquema que verá el LLM
- `GET /v1/productos/bajo-stock` — consulta de ejemplo

Todo lo que está bajo `/v1` pide el header `X-API-Key` (ver paso 6):

```bash
curl -X POST http://localhost:8000/v1/agente/preguntar \
  -H "X-API-Key: tu-llave" -H "Content-Type: application/json" \
  -d '{"pregunta": "¿Qué órdenes de producción están retenidas y por qué?"}'
```

> Si cambias algo en `db/init/`, borra el volumen para que MySQL lo vuelva a cargar:
> `docker compose down -v && docker compose up -d --build`

## Requisitos

- Docker Desktop
- [Ollama](https://ollama.com) corriendo en tu máquina, con los modelos:

```bash
ollama pull nomic-embed-text   # embeddings
ollama pull qwen2.5:7b         # modelo que responde (~4.7 GB de VRAM)
```

- Opcional, para respuestas habladas: un recurso **Azure AI Speech** (plan F0 gratuito) con `AZURE_SPEECH_KEY` y `AZURE_SPEECH_REGION` en el `.env`. Sin él, `/v1/voz/preguntar` responde solo en texto.

## RAG sobre documentos internos (paso 2)

Documentos de ejemplo en `docs/`: Manual de Calidad MQ-01, Procedimiento de retención PR-07 y Política de garantías PG-03.

1. `POST /v1/rag/ingest` — carga los documentos en ChromaDB (correrlo cada vez que cambien los `.md`).
2. `POST /v1/rag/buscar` — búsqueda semántica sin LLM; muestra qué fragmentos recupera.
3. `POST /v1/rag/preguntar` — respuesta del LLM citando los fragmentos `[1]`, `[2]`… y la lista de fuentes.

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

## Agente SQL de solo lectura (paso 3)

- `POST /v1/sql/preguntar` — pregunta en español → el LLM genera SQL → se valida → se ejecuta → respuesta con los datos reales.
- `POST /v1/sql/validar` — prueba la validación sin LLM: pega cualquier SQL y ve si pasa o por qué se bloquea.

Flujo:

1. El modelo recibe el **esquema real** leído de `information_schema` (tablas, columnas, valores de los ENUM y llaves foráneas), así no inventa columnas.
2. Genera **una** consulta `SELECT`.
3. Se **valida** (capa 2) y se le fuerza un `LIMIT` (capa 3).
4. Se ejecuta con el usuario de **solo lectura** (capa 1) y tiempo máximo de 5 s.
5. Si MySQL marca error (columna inexistente, sintaxis), el modelo recibe el error y **corrige una vez**. Un rechazo por seguridad **no** se reintenta.
6. El modelo resume los datos en español, sin agregar nada que no esté en el resultado.

### Las tres capas de seguridad

| Capa | Qué hace | Qué detiene |
|---|---|---|
| 1. Permisos de MySQL | El agente usa `agente_lectura`, que solo tiene `SELECT` | Cualquier escritura, aunque todo lo demás falle |
| 2. Validación con sqlglot | Analiza el SQL como árbol: una sola sentencia, solo `SELECT`, solo tablas de `planta`, sin `SLEEP`/`LOAD_FILE`/`INTO OUTFILE` | Inyección de sentencias, lectura de `mysql.user` o `information_schema`, ataques de tiempo, escritura de archivos |
| 3. Límites de ejecución | `LIMIT` máximo de 100 filas y `MAX_EXECUTION_TIME` de 5 s | Consultas que traen toda la base o se quedan colgadas |

¿Por qué tres y no una? La validación (capa 2) puede tener huecos; los permisos de la base (capa 1) no se pueden saltar desde el SQL. Y aunque una consulta sea segura, puede ser costosa (capa 3).

Casos probados en `/v1/sql/validar`: `DELETE`, `UPDATE`, `DROP`, `SET GLOBAL`, `SELECT 1; DROP TABLE`, `mysql.user`, `information_schema`, tabla inexistente, `SLEEP()`, `LOAD_FILE()` e `INTO OUTFILE` se bloquean; `SELECT`, `JOIN`, `GROUP BY` y `WITH` (CTE) pasan con `LIMIT` forzado.

Preguntas para probar:

- ¿Qué pedidos abiertos hay de productos que están por debajo de su stock mínimo?
- ¿Qué órdenes de producción están retenidas y por qué defecto?
- ¿Cuántas piezas ha pedido cada cliente en total?

## Agente con tool calling (paso 4)

`POST /v1/agente/preguntar` — un solo punto de entrada. El modelo decide qué herramientas usar:

| Herramienta | Qué hace | Cuándo la usa |
|---|---|---|
| `buscar_documentos` | Búsqueda semántica en ChromaDB (paso 2) | Reglas, procedimientos, políticas |
| `consultar_base_datos` | Text-to-SQL con las tres capas (paso 3) | Datos actuales: stock, pedidos, órdenes |

Cómo funciona el ciclo:

1. El modelo recibe la pregunta y la descripción de las dos herramientas.
2. Si decide usar una, devuelve una *tool call* (nombre y argumentos); el código la ejecuta y le regresa el resultado.
3. Con ese resultado decide: ¿otra herramienta, o ya puede responder?
4. Tope de 6 llamadas para que nunca entre en un ciclo infinito. Si una herramienta falla, el error se le pasa al modelo en lugar de tumbar la API.

La respuesta incluye `pasos`: qué herramienta usó, con qué argumentos y qué obtuvo. Así se puede auditar cómo llegó a la respuesta.

**Por qué un agente y no solo RAG:** la pregunta *"¿Qué hago con la orden retenida de bombas de infusión?"* necesita tres pasos encadenados, que un RAG de una sola búsqueda no puede hacer:

1. Base de datos → la orden 4 está retenida por flujo fuera de tolerancia (+6%).
2. Manual MQ-01 → el flujo fuera de tolerancia es un defecto **crítico**.
3. Procedimiento PR-07 → con un defecto crítico: cuarentena, aviso en 4 horas, causa raíz en 5 días y decisión del comité.

Preguntas para probar:

- ¿Qué hago con la orden retenida de bombas de infusión?
- ¿Hay pedidos de hospitales afectados por productos sin stock suficiente, y qué dice la política sobre hospitales?
- ¿Cuánto tiempo lleva retenida la orden y cuál es el plazo máximo permitido?

## Observabilidad y evaluación (paso 5)

### Tracing con Langfuse

Cada pregunta a `/v1/agente/preguntar` genera un trace en Langfuse con todo lo que hizo el agente:

- cada llamada al LLM: prompt, respuesta, tokens y latencia
- cada herramienta (`buscar_documentos`, `consultar_base_datos`) con sus argumentos y resultado
- dentro de la herramienta de base de datos, la generación del SQL y el reintento si lo hubo

Es opcional: si el `.env` no trae las llaves de Langfuse, el asistente funciona igual sin tracing (`app/tracing.py`).
El LLM sigue corriendo local en Ollama; a Langfuse solo salen los traces. En un entorno regulado se puede self-hostear Langfuse para que nada salga de la red.

### Evals

`evals/dataset.py` tiene 15 casos con su respuesta esperada (sacada de los datos de ejemplo y los documentos):

| Tipo | Casos | Qué se mide |
|---|---|---|
| SQL | 5 | `herramientas_ok`: usó `consultar_base_datos` · `contenido`: la respuesta trae los datos correctos |
| RAG | 4 | `herramientas_ok`: usó `buscar_documentos` · `contenido`: cita el dato correcto del documento |
| Mixto | 2 | `herramientas_ok`: encadenó **las dos** herramientas · `contenido` |
| Ataque | 4 | `integridad_bd`: la base queda idéntica después de pedirle borrar, hacer `DROP` o cambiar datos |

```bash
docker compose exec api python -m evals.run_evals                # modelo del .env (qwen2.5:7b)
docker compose exec api python -m evals.run_evals llama3.1:8b    # comparar otro modelo
docker compose exec api python -m evals.run_evals --rapido       # 1 caso por tipo, para probar
```

Cada corrida queda como experimento en Langfuse (comparables lado a lado) y en `evals/resultados/<modelo>.json`.

### Resultados (qwen2.5:7b, 15 casos)

| Métrica | Línea base | Con el fix (corrida 1) | Con el fix (corrida 2) |
|---|---|---|---|
| Herramienta correcta | 90.9% | **100%** | **100%** |
| Contenido correcto | 86.4% | 81.8% | 90.9% |
| Integridad de la BD ante ataques | 100% | 100% | 100% |

**Qué encontraron el tracing y las evals:** en las preguntas que combinan datos y documentos, el agente a veces
terminaba sin llamar herramientas: escribía "consultaré los documentos…" o le pedía al usuario el número de orden
en vez de buscarlo. Se corrigió en el loop del agente (`app/agent.py`): si intenta responder sin haber usado
ninguna herramienta, o anuncia una acción sin ejecutarla, se le recuerda que las use (máximo 2 veces).

Un primer intento fue agregar una regla al prompt; las evals mostraron que **bajaba** el contenido correcto
(86% → 68%) porque cambiaba el comportamiento en todas las preguntas. Se descartó en favor del arreglo en código.

**Variación entre corridas:** aun con temperatura 0, el modelo local no es 100% determinista. Con 15 casos,
un solo caso mueve el contenido ~9 puntos, por eso cada cambio se mide con al menos dos corridas.

**Pendiente (lo que muestran las fallas restantes):**
- Text-to-SQL: a veces filtra con `LIKE` sobre la columna equivocada (busca "cables de paciente" en `defecto` en vez de en el producto) o arma mal el JOIN de pedidos, y responde "no hay pedidos" cuando sí los hay.
- RAG: la sección "Comunicación con clientes" del PR-07 no siempre se recupera.
- El modelo a veces manda argumentos con forma de objeto a `buscar_documentos`; el error se le regresa y reintenta.

## API de producción (paso 6)

| Qué | Cómo | Por qué |
|---|---|---|
| Versionado | Todo bajo `/v1` con un `APIRouter`; `/health` queda fuera | Cambios incompatibles futuros van a `/v2` sin romper a quien ya integra `/v1` |
| Autenticación | Header `X-API-Key`; llaves en `API_KEYS` (varias separadas por coma), comparación en tiempo constante (`secrets.compare_digest`) | Rotar llaves sin downtime y no filtrar información por tiempos de respuesta |
| Rate limiting | `slowapi`, contado **por llave** (o por IP si no hay llave); responde `429` | Cada llamada al LLM cuesta GPU; un cliente no puede acaparar el servicio |

Límites según lo que cuesta cada endpoint:

| Endpoint | Límite |
|---|---|
| `/v1/voz/preguntar` (Whisper + agente + TTS) | 5/min |
| `/v1/agente/preguntar`, `/v1/rag/preguntar`, `/v1/sql/preguntar` (LLM) | 10/min |
| `/v1/voz/transcribir` (Whisper) | 10/min |
| `/v1/rag/buscar` (solo embeddings) | 30/min |
| `/v1/rag/ingest` (reconstruye la colección) | 2/min |
| Resto | 60/min |

Probado: sin llave → `401`; con llave, 30 llamadas a `/v1/rag/buscar` → `200` y la 31 → `429`.

## Retrieval tuning (paso 7)

Las evals del paso 5 miden la respuesta final; aquí se mide **solo la búsqueda**, sin LLM, para ajustar el chunking con números.

- `evals/retrieval_dataset.py`: 22 preguntas **parafraseadas** (no repiten las palabras del documento) con la "aguja": el texto que tiene que aparecer en algún fragmento recuperado.
- `evals/tune_retrieval.py`: prueba 24 configuraciones en colecciones temporales en memoria (no toca `chroma_data/`):
  estrategia (por secciones `#`/`##` o solo por tamaño) × tamaño (200, 400, 800) × traslape (0 o 12.5%) × prefijos de nomic (sí/no), y para cada una k = 1, 2, 3, 4, 6.
- Métricas: **hit@k** (¿la aguja está en los k fragmentos?), **MRR** (¿qué tan arriba sale el primero correcto?) y caracteres de contexto que se mandarían al LLM.

```bash
docker compose exec api python -m evals.tune_retrieval
```

Resultados (22 preguntas, `nomic-embed-text`):

| Configuración | hit@k | MRR | Contexto |
|---|---|---|---|
| **Por secciones, 800, prefijos, k=6 (la actual)** | **1.00** | 0.65 | 2546 chars |
| La misma **sin prefijos** de nomic | 0.86 | 0.64 | — |
| Solo por tamaño, 800, prefijos, k=6 | 0.95 | 0.73 | 3823 chars |
| Por secciones, 400, traslape 50, k=4 | 0.91 | 0.74 | 1133 chars |

Con la configuración actual, al bajar k:

| k | 6 | 4 | 3 | 1 |
|---|---|---|---|---|
| hit@k | 1.00 | 0.91 | 0.82 | 0.41 |

**Lo que dicen los números:**

1. **Los prefijos `search_document:` / `search_query:` valen 14 puntos** de hit@6 (0.86 → 1.00). Confirma con datos lo que se había visto a mano en el paso 2.
2. **Cortar por secciones le gana a cortar solo por tamaño** (1.00 vs 0.95) y manda menos contexto: cada fragmento es una idea completa con su título.
3. **El traslape no cambia nada en estos documentos**: ninguna sección pasa de 800 caracteres, así que el segundo splitter nunca corta dentro de una sección.
4. **El ranking es el punto débil**: el fragmento correcto sale primero solo en el 41% de las preguntas. Funciona porque k=6 es casi un tercio de los 19 fragmentos del corpus; con muchos más documentos no alcanzaría.

**Siguiente paso:** búsqueda híbrida (BM25 + vectores con Reciprocal Rank Fusion) o un reranker, medidos con este mismo script, para llegar a hit@3 ≈ 1.00 con la mitad del contexto.

## Voz (paso 8)

```
audio (es/en) → faster-whisper (local, CPU) → texto + idioma → agente (paso 4) → respuesta → Azure AI Speech → MP3
```

- `POST /v1/voz/transcribir` — solo voz → texto; útil para revisar qué entendió.
- `POST /v1/voz/preguntar` — flujo completo. Devuelve MP3 (la transcripción y la respuesta van en headers `X-Transcripcion` / `X-Respuesta`, URL-encoded) o JSON con `?formato=json`.

```bash
curl -X POST "http://localhost:8000/v1/voz/preguntar?formato=json" \
  -H "X-API-Key: tu-llave" -F "audio=@pregunta.m4a"

curl -X POST http://localhost:8000/v1/voz/preguntar \
  -H "X-API-Key: tu-llave" -F "audio=@pregunta.m4a" -o respuesta.mp3
```

| Decisión | Por qué |
|---|---|
| **Whisper local** (`faster-whisper`, modelo `small`, int8 en CPU) | El audio no sale de la máquina, sin costo por minuto; el modelo se descarga una vez a `whisper_cache/` |
| **Decodificación propia con PyAV** (cualquier formato → mono 16 kHz) | El decodificador de faster-whisper usa un parámetro que PyAV quitó en versiones recientes; así no depende de esa versión |
| **Detección de idioma** | Whisper detecta español o inglés; si duda (audios cortos), reintenta forzando español |
| **Bilingüe sin traducir los datos** | En inglés, el agente responde en inglés pero llama las herramientas en español, porque documentos y base están en español |
| **Azure AI Speech por REST** (voces `es-MX-DaliaNeural`, `en-US-JennyNeural`) | Voces neuronales sin instalar el SDK (que en Linux pide librerías del sistema) |
| **Limpieza antes de hablar** | Se quitan citas `[1]` y markdown; se recorta a 1500 caracteres para cuidar la cuota F0 |
| **Errores claros** | Audio vacío o ilegible → `422`; más de 10 MB → `413`; falla de Azure → `502`; sin llave de Azure → respuesta solo en texto |

Ejemplo real: *"¿Cuántos meses de garantía tienen las bombas de infusión?"* → Whisper transcribe exacto (idioma `es`, confianza 0.98) → el agente usa `buscar_documentos` → *"18 meses en partes y mano de obra, desde la fecha de entrega"* (PG-03) → MP3 con la respuesta.

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