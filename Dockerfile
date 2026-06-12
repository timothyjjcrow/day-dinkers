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

# Update backend.app:app if the Flask entrypoint lives elsewhere.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} backend.app:app"]
