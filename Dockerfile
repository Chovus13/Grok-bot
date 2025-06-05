FROM python:3.12-slim

WORKDIR /app

# Kreiraj logs direktorijum i bot.log fajl
#RUN useradd -m -u 1000 nginx
#USER nginx
#    chown -R appuser:appuser /app/logs/ && \
# odvojeni deo za cache folder
#RUN mkdir -p /var/cache/nginx && \
#    chown -R nginx:nginx /var/cache/nginx && \
#    chmod 700 /var/cache/nginx
# Kopiraj fajlove i instaliraj zavisnosti
COPY . .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m -u 1000 appuser
COPY html/index.html /usr/share/nginx/html
RUN mkdir -p /app/logs && \
    mkdir -p /app/html && \
    mkdir -p /app/user_data && \
    touch /app/logs/bot.log && \
    touch /app/logs/error.log && \
    touch /app/user_data/candidates.json && \
    touch /app/bot.log && \
    touch /app/logs/access.log

EXPOSE 8080
# Remove default Nginx config
# RUN rm -rf /etc/nginx/conf.d

# Copy custom Nginx config
#COPY nginx.conf /etc/nginx/nginx.conf
# COPY html/index.html /app/html/www
# Kreiraj običnog korisnika
#USER nginx
RUN chown -R "$USER":www-data /usr/share/nginx/html && \
    chmod  777 /app/bot.log && \
    chmod -R 0755 /usr/share/nginx/html && \
    chown -R appuser:appuser /app/user_data && \
    chmod -R 777 /app/user_data && \
    chmod -R 0777  /app/logs

## /app/logs && \
# Prebaci na nginx korisnika
# proveri ko su vlasnici i permisions
#RUN chown -R "$USER":www-data /app/logs && \
#    chmod -R 0777 /app/logs/ \
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
