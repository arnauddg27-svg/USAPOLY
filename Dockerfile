FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY polyedge/ polyedge/
COPY config/ config/
RUN mkdir -p logs/audit
CMD ["python", "-m", "polyedge.main"]
