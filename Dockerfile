# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build/src/frontend
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm config set registry "${NPM_REGISTRY}"
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY tokens.css /build/tokens.css
COPY src/frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend-builder
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
ARG PYPI_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "${PYPI_INDEX_URL}" "uv==0.11.6"
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_DEFAULT_INDEX="${PYPI_INDEX_URL}" uv sync --frozen --no-dev --no-install-project
COPY src/backend/ src/backend/
# Legacy-layout bytecode remains importable without the original .py files.
RUN python -m compileall -b -q src/backend \
    && find src/backend -type f -name '*.py' -delete \
    && find src/backend -type d -name '__pycache__' -prune -exec rm -rf '{}' +

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_RUNTIME_DIR=/mnt/workspace/awesome-animal-helper/runtime
WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --uid 10001 --create-home app \
    && mkdir -p /mnt/workspace/awesome-animal-helper/runtime \
    && chown -R app:app /mnt/workspace/awesome-animal-helper

COPY --from=backend-builder /opt/venv /opt/venv
COPY --from=backend-builder /build/src/backend /app/src/backend
COPY --from=frontend-builder /build/src/frontend/dist /app/src/frontend/dist
COPY src/data/animals.csv src/data/animal_sites.xlsx src/data/intro.md \
     src/data/hongshan_zoo_boundary.geojson /app/src/data/
COPY src/data/wx_info/wiki /app/src/data/wx_info/wiki

USER app
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "7860", "--no-access-log"]
