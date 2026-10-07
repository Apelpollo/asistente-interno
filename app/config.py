"""Configuración leída de variables de entorno (.env)."""
import os


class Settings:
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database: str = os.getenv("MYSQL_DATABASE", "planta")
    # El agente SIEMPRE se conecta con el usuario de solo lectura
    mysql_user: str = os.getenv("MYSQL_READONLY_USER", "agente_lectura")
    mysql_password: str = os.getenv("MYSQL_READONLY_PASSWORD", "lectura_segura_123")


settings = Settings()
