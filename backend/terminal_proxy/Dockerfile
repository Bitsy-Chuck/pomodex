FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    websockets==15.0.1 \
    aiohttp==3.11.* \
    docker==7.*

COPY . /app/backend/terminal_proxy/

# Need the parent package
RUN mkdir -p /app/backend && touch /app/backend/__init__.py

EXPOSE 9000

CMD ["python", "-m", "backend.terminal_proxy.proxy"]
