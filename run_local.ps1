# Arranca la API en local (sin Docker), apuntando a la base de energía
$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_DATABASE = "energia_bim"
$env:LLM_MODEL = "qwen2.5:7b"
# uvicorn siempre vigila también la carpeta actual: se excluye .venv (con ruta absoluta) para que no se reinicie
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app --reload-exclude "$PSScriptRoot\.venv"