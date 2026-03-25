FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi>=0.116.1 uvicorn[standard]>=0.35.0

COPY . /app

WORKDIR /app/dev_ui
CMD ["python", "-m", "dev_ui.server"]
