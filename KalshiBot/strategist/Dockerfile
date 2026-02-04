FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Strategist code
COPY . /app/strategist

# We need the trader modules (scanner, probability) available
# In docker-compose we will likely mount or build context that includes them
# For now, let's assume we copy them or mount them.
# The code expects them in /app/trader or relative path.
# IMPORTANT: When building this image, the context needs to be the root KalshiBot dir
# or we copy them specifically. Since docker-compose build context is usually fixed per service...
# A cleaner way for this phase is to copy the trader files we need into the strategist image build
# OR handle it via volume mounts in dev, but for deployment COPY is safer.

CMD ["python", "-u", "/app/strategist/ai_strategist.py"]
