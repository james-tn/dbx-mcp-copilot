FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi>=0.116.1 uvicorn[standard]>=0.35.0 azure-identity>=1.25.0 msal>=1.31.1 PyJWT[crypto]>=2.10.1 httpx>=0.27.0

COPY . /app

WORKDIR /app/databricks_apps/top_opportunities_app
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
