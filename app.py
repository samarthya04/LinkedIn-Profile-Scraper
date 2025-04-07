# flask_server.py

from flask import Flask, request, jsonify, Response
import asyncio
from scraper import LinkedInProfileScraper, LLMClient, search_keys
import threading
import logging
import json
from datetime import datetime
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('flask_server.log'), logging.StreamHandler()]
)

load_dotenv()

app = Flask(__name__)

# Global state to track scraper instances and results
scraper_instance = None
scraper_thread = None
scraper_running = False
last_results = []

def run_scraper():
    global scraper_instance, scraper_running, last_results
    try:
        llm_client = LLMClient()
        scraper_instance = LinkedInProfileScraper(search_keys, llm_client, headless=True)  # Headless for server
        logging.info("Starting scraper in thread")
        scraper_instance.run()
        last_results = scraper_instance.export_to_json()
        logging.info("Scraper completed")
    except Exception as e:
        logging.error(f"Scraper failed: {str(e)}")
    finally:
        scraper_running = False

@app.route('/start', methods=['POST'])
def start_scraper():
    global scraper_thread, scraper_running
    if scraper_running:
        return jsonify({"error": "Scraper is already running"}), 400
    
    data = request.get_json()
    if data:
        # Optionally update search_keys with POST data
        if 'keywords' in data:
            search_keys['keywords'] = data['keywords']
        if 'locations' in data:
            search_keys['locations'] = data['locations']
        if 'filename' in data:
            search_keys['filename'] = data['filename']
    
    scraper_running = True
    scraper_thread = threading.Thread(target=run_scraper)
    scraper_thread.start()
    
    return jsonify({
        "message": "Scraper started",
        "keywords": search_keys["keywords"],
        "locations": search_keys["locations"],
        "filename": search_keys["filename"]
    }), 202

@app.route('/status', methods=['GET'])
def get_status():
    global scraper_running, scraper_instance
    profile_count = scraper_instance._count_profiles() if scraper_instance else 0
    return jsonify({
        "running": scraper_running,
        "profiles_collected": profile_count,
        "max_profiles": 200,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/results', methods=['GET'])
def get_results():
    global last_results
    try:
        with open(search_keys["filename"], 'r') as f:
            profiles = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        profiles = last_results  # Fallback to in-memory results if file not available
    
    return jsonify({
        "profiles": profiles,
        "total": len(profiles),
        "last_updated": datetime.now().isoformat()
    })

@app.route('/stop', methods=['POST'])
def stop_scraper():
    global scraper_running, scraper_instance
    if not scraper_running:
        return jsonify({"error": "No scraper is running"}), 400
    
    # Note: This is a basic stop mechanism. The actual stopping depends on scraper implementation.
    # Since the original scraper doesn't have a built-in stop, we'll just mark it as stopped
    # and rely on the thread to finish naturally or implement a more robust stop in scraper.py if needed.
    scraper_running = False
    if scraper_instance and scraper_instance.db_conn:
        scraper_instance.db_conn.close()
    
    return jsonify({"message": "Scraper stop requested"}), 200

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "Server is running", "version": "1.0"}), 200

if __name__ == '__main__':
    # Ensure the event loop is handled correctly in the main thread
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
