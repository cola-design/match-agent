FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SYNC_STATE_DIR=/state
WORKDIR /app
COPY product_sync/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home syncuser && mkdir /state && chown syncuser:syncuser /state
COPY --chown=syncuser:syncuser product_sync /app/product_sync
COPY --chown=syncuser:syncuser mongodb_api/product_contract.py /app/mongodb_api/product_contract.py
USER syncuser
ENTRYPOINT ["python", "-m", "product_sync.sync"]
