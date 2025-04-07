# Use Python 3.11 slim as the base image
FROM python:3.11-slim

# Set environment variables for Python and system
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    DEBIAN_FRONTEND=noninteractive \
    CHROME_BINARY_PATH="/usr/bin/google-chrome-stable"

# Install system dependencies for Chrome, ChromeDriver, and general utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    gnupg \
    curl \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    fonts-liberation \
    libappindicator3-1 \
    xdg-utils \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Dynamically fetch and install matching ChromeDriver version
RUN CHROME_VERSION=$(google-chrome --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "126.0.6478") \
    && echo "Chrome version: $CHROME_VERSION" \
    && CHROMEDRIVER_VERSION=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_VERSION}" || \
       echo "126.0.6478.126") \
    && echo "Installing ChromeDriver version: $CHROMEDRIVER_VERSION" \
    && wget -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip /tmp/chromedriver.zip -d /tmp/ \
    && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/ \
    && rm -rf /tmp/chromedriver.zip /tmp/chromedriver-linux64 \
    && chmod +x /usr/local/bin/chromedriver \
    && chromedriver --version

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install gunicorn  # Ensure gunicorn is installed

# Create necessary directories
RUN mkdir -p /app/static /app/templates /tmp/data \
    && chmod -R 777 /tmp/data  # Ensure write permissions for non-root user

# Copy application files
COPY linkedin_scraper.py .
COPY templates/index.html templates/
COPY static/styles.css static/  # Assuming you have this file

# Create a non-root user and set permissions
RUN groupadd -r appuser && useradd -r -g appuser -d /home/appuser -m appuser \
    && chown -R appuser:appuser /app /tmp/data

# Switch to non-root user
USER appuser

# Expose port (Render will override this with its own PORT env var)
EXPOSE 5000

# Command to run the app with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "180", "--workers", "1", "linkedin_scraper:app"]
