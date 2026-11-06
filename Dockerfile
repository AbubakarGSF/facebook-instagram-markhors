# Social Markhors Bot - Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose health check port
EXPOSE 8080

# Run the bot
CMD ["python", "src/main.py"]
