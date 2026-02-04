FROM python:3.11-slim

WORKDIR /app

# No external dependencies needed for heartbeat
COPY heartbeat.py .

CMD ["python", "-u", "heartbeat.py"]
