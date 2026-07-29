FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir -e . --no-deps

RUN mkdir -p data reports/output logs

ENV APP_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Railway / Fly / Render inject PORT; fall back to 8000 locally
ENV API_PORT=8000
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; p=os.environ.get('PORT') or os.environ.get('API_PORT','8000'); urllib.request.urlopen(f'http://127.0.0.1:{p}/health')" || exit 1

CMD ["python", "main.py", "serve"]
