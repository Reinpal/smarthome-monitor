FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY scraper/ ./scraper/

# Run as non-root user
RUN useradd --create-home appuser
USER appuser

CMD ["python", "-m", "scraper.main"]
