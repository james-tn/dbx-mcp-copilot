FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY m365_wrapper/ ./
COPY shared/ /app/shared/

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 3978

CMD ["python", "server.py"]
