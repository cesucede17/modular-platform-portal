# El recibidor. Imagen pequena a proposito: no necesita el stack cientifico
# que carga PALBE, solo servir una pagina y hablar OIDC.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Las dependencias se instalan DESDE pyproject.toml, no repetidas aqui: dos
# listas que deben decir lo mismo acaban diciendo cosas distintas.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY herramientas.yaml ./

RUN groupadd -r sge && useradd -r -g sge -d /app sge && chown -R sge:sge /app
USER sge

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# --app-dir apunta a backend/, no a /app: asi "import catalogo" e "import sso"
# resuelven igual que en los tests (conftest pone esa misma carpeta en
# sys.path). Con --app-dir /app fallarian, porque backend/ no estaria dentro.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
