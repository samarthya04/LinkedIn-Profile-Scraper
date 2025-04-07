# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies and Google Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Verify Chrome installation
RUN google-chrome --version

# Copy application files
COPY requirements.txt .
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 5000

# Set environment variable for Chrome binary
ENV CHROME_BIN=/usr/bin/google-chrome

# Run the app with Gunicorn, using PORT environment variable
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT app:app"]
