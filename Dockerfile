# --- Stage 1: build the React frontend ---
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python backend serving /api and the built frontend ---
FROM python:3.13-slim
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.13 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
RUN rm -f .python-version
COPY --from=frontend /frontend/dist ./static

ENV STATIC_DIR=/app/static PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
