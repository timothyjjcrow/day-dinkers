FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Keep one process because the app's rate limiter and push queue are in memory.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 8 --bind 0.0.0.0:${PORT:-8000} backend.wsgi:app"]
