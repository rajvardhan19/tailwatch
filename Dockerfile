FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.15 && uv sync --locked --no-dev
COPY migrations ./migrations
COPY alembic.ini ./
COPY config/research.synthetic.json ./config/research.synthetic.json
RUN useradd --uid 10001 --create-home tailwatch && chown -R tailwatch:tailwatch /app
USER tailwatch
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "tailwatch.product.server:app", "--host", "0.0.0.0", "--port", "8000"]
