# Build stage - Install dependencies with uv
FROM python:3.11-slim AS builder

# Copy uv from official image (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Environment variables for uv optimization
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies (cached separately from app code)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-editable

# Copy application code
COPY /app ./app/

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# Runtime stage - Minimal production image
FROM python:3.11-slim AS runtime

# Create non-root user for security
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -m appuser

WORKDIR /app

# Copy uv for consistency with dev workflow
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv

# Copy application code
COPY --from=builder --chown=appuser:appgroup /app/app /app/app
COPY --from=builder --chown=appuser:appgroup /app/pyproject.toml /app/pyproject.toml
COPY --from=builder --chown=appuser:appgroup /app/uv.lock /app/uv.lock


# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/python/health').read()"

CMD ["uv", "run", "fastapi", "run"]