FROM python:3.12-slim

WORKDIR /app


COPY . .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY html/index.html /usr/share/nginx/html
RUN mkdir -p /app/logs && \
    mkdir -p /app/html && \
    mkdir -p /app/user_data && \
    touch /app/logs/bot.log && \
    touch /app/logs/error.log && \
    touch /app/user_data/candidates.json && \
    touch /app/user_data/chovusbot.db && \
    touch /app/bot.log && \
    touch /app/logs/access.log

EXPOSE 8080

RUN useradd -m -u 1000 appuser
RUN chown -R "$USER":www-data /usr/share/nginx/html && \
    chown -R "$USER":appuser /app/user_data && \
    chmod -R 666 /app/user_data && \
    chmod -R 0755 /usr/share/nginx/html && \
    chmod -R 0755 /app/logs

RUN chown -R "$USER":www-data /usr/share/nginx/html && \
    chmod -R 777 /app/user_data && \
    chmod -R 0755 /usr/share/nginx/html



CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
RUN chown -R "$USER":appuser /app/user_data && \
    chmod -R 666 /app/user_data
USER appuser