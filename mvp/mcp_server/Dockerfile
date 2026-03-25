FROM python:3.12-slim

WORKDIR /app

COPY mcp_server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

WORKDIR /app/mcp_server
CMD ["python", "-m", "mcp_server.server"]
