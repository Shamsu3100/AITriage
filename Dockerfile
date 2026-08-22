FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Hosting platforms inject $PORT. Never hardcode 8000 in production.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
