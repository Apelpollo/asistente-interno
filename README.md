# Asistente interno con RAG y agente SQL

Asistente para una empresa manufacturera de equipo electro-médico. Responde preguntas usando:

- **Documentos internos** (manuales, procedimientos) mediante RAG con ChromaDB. *(paso 2)*
- **Datos en vivo de MySQL** mediante text-to-SQL de solo lectura. *(paso 3)*
- Un **agente con tool calling** que decide qué herramienta usar. *(paso 4)*

## Estado

- [x] Paso 1: FastAPI + MySQL en Docker Compose, datos de ejemplo, usuario de solo lectura
- [x] Paso 2: RAG (chunking por sección, embeddings con Ollama, ChromaDB, citas de fuente)
- [x] Paso 3: Agente SQL con validación en tres capas y reintento con el error
- [x] Paso 4: Agente con tool calling que encadena documentos y base de datos
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

## Agente SQL de solo lectura (paso 3)

- `POST /sql/preguntar` — pregunta en español → el LLM genera SQL → se valida → se ejecuta → respuesta con los datos reales.
- `POST /sql/validar` — prueba la validación sin LLM: pega cualquier SQL y ve si pasa o por qué se bloquea.

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

Casos probados en `/sql/validar`: `DELETE`, `UPDATE`, `DROP`, `SET GLOBAL`, `SELECT 1; DROP TABLE`, `mysql.user`, `information_schema`, tabla inexistente, `SLEEP()`, `LOAD_FILE()` e `INTO OUTFILE` se bloquean; `SELECT`, `JOIN`, `GROUP BY` y `WITH` (CTE) pasan con `LIMIT` forzado.

Preguntas para probar:

- ¿Qué pedidos abiertos hay de productos que están por debajo de su stock mínimo?
- ¿Qué órdenes de producción están retenidas y por qué defecto?
- ¿Cuántas piezas ha pedido cada cliente en total?

## Agente con tool calling (paso 4)

`POST /agente/preguntar` — un solo punto de entrada. El modelo decide qué herramientas usar:

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
