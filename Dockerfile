FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER 10001
EXPOSE 8443
# HTTPS inside the pod (self-signed, made at start); 1 worker on purpose: SSE broker is in memory
CMD ["python", "-m", "app.serve"]
