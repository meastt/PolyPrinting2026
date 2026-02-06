FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    requests \
    python-dotenv \
    cryptography \
    groq \
    schedule

# Copy weather module
COPY . /app/weather/

# Set Python path
ENV PYTHONPATH=/app:/app/weather

# Default command (override in docker-compose)
CMD ["python3", "/app/weather/weather_strategist.py"]
