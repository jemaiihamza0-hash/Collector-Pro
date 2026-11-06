FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /opt/collector
COPY requirements.txt requirements-docling.txt ./
ARG INSTALL_DOCLING=false
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DOCLING" = "true" ]; then pip install --no-cache-dir -r requirements-docling.txt; fi \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 collector \
    && mkdir -p /storage && chown collector:collector /storage
COPY --chown=collector:collector app ./app
USER collector
ENV STORAGE_DIR=/storage
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
