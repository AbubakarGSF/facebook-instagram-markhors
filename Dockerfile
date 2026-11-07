# Social Markhors Bot - Dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Koi system compiler zaroori nahi: saari dependencies wheels deti hain
# (cryptography, httpx, python-telegram-bot). Is liye gcc install nahi karte.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway PORT env use hota hai (HEALTH_CHECK_PORT override kar sakta hai)
EXPOSE 8080

CMD ["python", "src/main.py"]
