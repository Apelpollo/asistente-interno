"""Configuración leída de variables de entorno (.env)."""
import os


class Settings:
    # MySQL — el agente SIEMPRE se conecta con el usuario de solo lectura
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database: str = os.getenv("MYSQL_DATABASE", "planta")
    mysql_user: str = os.getenv("MYSQL_READONLY_USER", "agente_lectura")
    mysql_password: str = os.getenv("MYSQL_READONLY_PASSWORD", "lectura_segura_123")

    # Ollama (corre en tu máquina, fuera de Docker)
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-embed-text")

    # RAG
    docs_dir: str = os.getenv("DOCS_DIR", "docs")
    chroma_dir: str = os.getenv("CHROMA_DIR", "chroma_data")
    chroma_collection: str = "documentos_internos"
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "800"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    top_k: int = int(os.getenv("TOP_K", "6"))


settings = Settings()
