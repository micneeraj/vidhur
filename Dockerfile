FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VIDHUR_IN_DOCKER=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Secrets are never baked in: pass them at run time with --env-file .env
# Inside the container localhost is the container itself, so Nightshift and Zetaris are reached
# through host.docker.internal (mapped automatically by core/config.py when VIDHUR_IN_DOCKER=1).
CMD ["python", "main.py"]
