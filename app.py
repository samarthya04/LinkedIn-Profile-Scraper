from flask import Flask, request, jsonify
import threading

app = Flask(__name__)

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    data = request.get_json() or {}
    keyword = data.get('keyword', search_keys["keywords"][0])
    location = data.get('location', search_keys["locations"][0])
    
    llm_client = LLMClient()
    scraper = LinkedInProfileScraper(search_keys, llm_client, headless=True)
    
    def run_scraper():
        driver = webdriver.Chrome(options=scraper.get_chrome_options())
        memory = ScraperMemory()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(scraper.login(driver))
            loop.run_until_complete(scraper.run_search(driver, keyword, location, memory))
        except Exception as e:
            logging.error(f"Scraper thread error: {str(e)}")
        finally:
            driver.quit()
            loop.close()
            scraper.db_conn.close()
    
    thread = threading.Thread(target=run_scraper)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "Scraping started", "keyword": keyword, "location": location})

@app.route('/profiles', methods=['GET'])
def get_profiles():
    try:
        with open(search_keys["filename"], 'r') as f:
            profiles = json.load(f)
        return jsonify(profiles)
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify([])

if __name__ == "__main__":
    # For local testing only; Render uses Gunicorn
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
