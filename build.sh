#!/bin/bash
set -e

# Install Chromium
apt-get update
apt-get install -y chromium

# Install Python dependencies
pip install -r requirements.txt
