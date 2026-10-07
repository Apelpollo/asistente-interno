# Asistente interno con RAG y agente SQL

Asistente para una empresa manufacturera de equipo electro-médico. Responde preguntas usando:

- **Documentos internos** (manuales, procedimientos) mediante RAG con ChromaDB. *(paso 2)*
- **Datos en vivo de MySQL** mediante text-to-SQL de solo lectura. *(paso 3)*
- Un **agente con tool calling** que decide qué herramienta usar. *(paso 4)*

## Estado

- [x] Paso 1: FastAPI + MySQL en Docker Compose, datos de ejemplo, usuario de solo lectura
- [ ] Paso 2: RAG (chunking, embeddings, ChromaDB, citas de fuente)
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
