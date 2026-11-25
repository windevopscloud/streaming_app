FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER 10001
EXPOSE 8000
# 1 worker on purpose: SSE fan-out broker is in memory
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "75"]
