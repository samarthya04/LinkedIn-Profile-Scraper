from flask import Flask, jsonify, request
from scraper import LinkedInProfileScraper, LLMClient, search_keys  # Absolute import
import asyncio
import os
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

llm_client = LLMClient()
scraper = LinkedInProfileScraper(search_keys, llm_client, headless=True)

@app.route('/api/start-scrape', methods=['POST'])
def start_scrape():
    try:
        asyncio.run(scraper.run_parallel_searches())
        profiles = scraper.export_to_json()
        return jsonify({"status": "success", "profiles": profiles})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/profiles', methods=['GET'])
def get_profiles():
    try:
        profiles = scraper.export_to_json()
        return jsonify({"status": "success", "profiles": profiles})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
