import sqlite3
import json
import time
import random
import logging
import asyncio
from datetime import datetime
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI
import hashlib
from flask import Flask, request, jsonify, render_template
from requests_html import AsyncHTMLSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('profile_scraper.log'), logging.StreamHandler()]
)

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')

# OpenRouter LLM Client
class LLMClient:
    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not found in .env")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        )

    async def query(self, prompt):
        try:
            response = await self.client.chat.completions.create(
                model="openai/gpt-3.5-turbo",
                max_tokens=150,
                temperature=0.5,
                messages=[
                    {"role": "system", "content": "You are an AI assistant helping to decide actions for a LinkedIn profile scraper."},
                    {"role": "user", "content": prompt}
                ]
            )
            content = response.choices[0].message.content.strip()
            action = content.split("Action: ")[1].split("\n")[0]
            reasoning = content.split("Reasoning: ")[1]
            return {"action": action, "reasoning": reasoning}
        except Exception as e:
            logging.error(f"OpenRouter API query failed: {str(e)}")
            return {"action": "2", "reasoning": "Default scrape due to OpenRouter API error"}

# Search keys
search_keys = { 
    "username": os.getenv("LINKEDIN_EMAIL"),
    "password": os.getenv("LINKEDIN_PASSWORD"),
    "keywords": ["Data Scientist", "Software Engineer"],
    "locations": ["New Delhi", "Bhubaneswar"],
    "filename": "profiles.json"
}

if not search_keys["username"] or not search_keys["password"]:
    raise ValueError("LINKEDIN_EMAIL or LINKEDIN_PASSWORD not found in .env")

logging.info(f"Loaded credentials - Username: {search_keys['username']}")

class ScraperMemory:
    def __init__(self):
        self.state = {
            'visited_urls': set(),
            'page_hashes': set(),
            'last_action': None,
            'action_count': 0
        }
    
    def update(self, url, action, page_hash):
        self.state['visited_urls'].add(url)
        if self.state['last_action'] == action and page_hash in self.state['page_hashes']:
            self.state['action_count'] += 1
        else:
            self.state['action_count'] = 1
        self.state['last_action'] = action
        self.state['page_hashes'].add(page_hash)
    
    def should_stop(self):
        return self.state['action_count'] > 5

class LinkedInProfileScraper:
    def __init__(self, search_keys, llm_client):
        self.search_keys = search_keys
        self.llm = llm_client
        self.db_conn = sqlite3.connect('linkedin_profiles.db', check_same_thread=False)
        self._init_db()
        self.max_profiles = 200
        self.session = AsyncHTMLSession()
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
        ]

    def _init_db(self):
        with self.db_conn:
            self.db_conn.execute('''CREATE TABLE IF NOT EXISTS profiles
                                 (id TEXT PRIMARY KEY,
                                  name TEXT,
                                  url TEXT,
                                  last_scraped TEXT)''')

    def _profile_exists(self, profile_id):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT 1 FROM profiles WHERE id = ?', (profile_id,))
        return cursor.fetchone() is not None

    def _count_profiles(self):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM profiles')
        return cursor.fetchone()[0]

    def _save_profiles(self, profiles):
        with self.db_conn:
            cursor = self.db_conn.cursor()
            for profile in profiles:
                cursor.execute('''INSERT OR IGNORE INTO profiles 
                               (id, name, url, last_scraped)
                               VALUES (?, ?, ?, ?)''',
                            (profile['id'], profile['name'], 
                             profile['url'], profile['timestamp']))
        self.export_to_json()

    def _human_like_delay(self):
        delay = random.uniform(5, 10)
        if self._count_profiles() > 100:
            delay *= 1.5
        time.sleep(delay)

    async def login(self):
        headers = {'User-Agent': random.choice(self.user_agents)}
        try:
            # Initial GET to login page
            login_url = "https://www.linkedin.com/login"
            response = await self.session.get(login_url, headers=headers)
            await response.html.arender()  # Render JS
            csrf_token = response.html.xpath("//input[@name='csrfToken']/@value", first=True)

            # POST login data
            login_data = {
                'session_key': self.search_keys["username"],
                'session_password': self.search_keys["password"],
                'csrfToken': csrf_token
            }
            login_response = await self.session.post(login_url, data=login_data, headers=headers)
            if "global-nav" in login_response.text:
                logging.info("Login successful")
            else:
                raise Exception("Login failed - check credentials or CAPTCHA")
        except Exception as e:
            logging.error(f"Login failed: {str(e)}")
            raise
        self._human_like_delay()

    async def navigate_to_people_search(self):
        return "https://www.linkedin.com/search/results/people/"

    async def enter_search_keys(self, keyword, location):
        search_url = f"https://www.linkedin.com/search/results/people/?keywords={keyword}%20{location}"
        headers = {'User-Agent': random.choice(self.user_agents)}
        try:
            response = await self.session.get(search_url, headers=headers)
            await response.html.arender(timeout=20)  # Render JS
            logging.info(f"Searching for: {keyword} {location}")
            return response
        except Exception as e:
            logging.error(f"Search failed: {str(e)}")
            raise
        self._human_like_delay()

    async def get_page_hash(self, response):
        return hashlib.md5(response.text.encode()).hexdigest()

    async def decide_next_action(self, response, memory):
        profile_count = self._count_profiles()
        page_hash = await self.get_page_hash(response)
        try:
            profile_cards = len(response.html.xpath("//a[contains(@href, '/in/')]"))
            next_button = bool(response.html.xpath("//button[@aria-label='Next' and not(@disabled)]"))
        except Exception as e:
            logging.error(f"Error detecting elements: {str(e)}")
            profile_cards = 0
            next_button = False

        summary = f"Profiles: {profile_count}/{self.max_profiles}, Cards: {profile_cards}, Next: {next_button}"
        prompt = f"""
        Current state: {summary}
        Previous action repeated: {memory.state['action_count']} times
        
        Options:
        1. Click next page
        2. Scrape current page
        3. Stop scraping
        
        Respond in this format:
        Action: <number>
        Reasoning: <text>
        """
        decision = await self.llm.query(prompt)
        if decision["action"] == "3" and profile_count < self.max_profiles and (next_button or profile_cards > 0):
            return {"action": "1" if next_button else "2", 
                    "reasoning": "Override: Less than 200 profiles, continuing with next page or scrape."}
        return decision

    async def scrape_profiles(self, response, memory):
        profiles = []
        try:
            links = response.html.xpath("//a[contains(@href, '/in/')]")
            for link in links:
                if self._count_profiles() >= self.max_profiles:
                    break
                try:
                    url = link.attrs.get('href', '').split("?")[0]
                    if "/in/" not in url or url in memory.state['visited_urls']:
                        continue
                    name = link.text.strip()
                    if name and "linkedin.com/in/" in url:
                        profile_id = url.split('/in/')[-1].strip('/')
                        if not self._profile_exists(profile_id):
                            profiles.append({
                                'id': profile_id,
                                'name': name,
                                'url': url,
                                'timestamp': datetime.now().isoformat()
                            })
                            memory.state['visited_urls'].add(url)
                            logging.info(f"Scraped profile: {name} - {url}")
                except Exception:
                    continue
        except Exception as e:
            logging.error(f"Scraping failed: {str(e)}")
        return profiles

    async def click_next_page(self, response):
        try:
            next_url = response.html.xpath("//a[@aria-label='Next']/@href", first=True)
            if next_url:
                headers = {'User-Agent': random.choice(self.user_agents)}
                next_response = await self.session.get(f"https://www.linkedin.com{next_url}", headers=headers)
                await next_response.html.arender(timeout=20)
                self._human_like_delay()
                return next_response
            return None
        except Exception as e:
            logging.error(f"Failed to navigate to next page: {str(e)}")
            return None

    async def run_search(self, keyword, location, memory):
        await self.login()
        await self.navigate_to_people_search()
        response = await self.enter_search_keys(keyword, location)
        while self._count_profiles() < self.max_profiles:
            if memory.should_stop():
                logging.error("Stopping due to potential infinite loop")
                break
            
            decision = await self.decide_next_action(response, memory)
            action, reasoning = decision["action"], decision["reasoning"]
            page_hash = await self.get_page_hash(response)
            logging.info(f"LLM decided: {action} - {reasoning}")
            memory.update(response.url, action, page_hash)
            
            if action == "1":
                response = await self.click_next_page(response)
                if not response:
                    break
            elif action == "2":
                profiles = await self.scrape_profiles(response, memory)
                if profiles:
                    self._save_profiles(profiles)
                self._human_like_delay()

    async def run_parallel_searches(self):
        semaphore = asyncio.Semaphore(2)
        async def limited_run(kw, loc):
            async with semaphore:
                memory = ScraperMemory()
                try:
                    await self.run_search(kw, loc, memory)
                except Exception as e:
                    logging.error(f"Task for {kw} {loc} failed: {str(e)}")

        tasks = [
            limited_run(kw, loc)
            for kw in self.search_keys["keywords"] 
            for loc in self.search_keys["locations"]
            if self._count_profiles() < self.max_profiles
        ]
        await asyncio.gather(*tasks)

    def export_to_json(self):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT id, name, url, last_scraped FROM profiles')
        profiles = [{
            'name': row[1],
            'url': row[2],
            'scraped_at': row[3]
        } for row in cursor.fetchall()]
        
        try:
            with open(self.search_keys["filename"], 'r') as f:
                existing_profiles = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing_profiles = []
        
        updated_profiles = existing_profiles + [p for p in profiles if p not in existing_profiles]
        with open(self.search_keys["filename"], 'w') as f:
            json.dump(updated_profiles, f, indent=2)
        return updated_profiles

    def run(self):
        try:
            asyncio.run(self.run_parallel_searches())
            logging.info(f"Total profiles collected: {self._count_profiles()}")
        finally:
            self.db_conn.close()

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    data = request.get_json() or {}
    keyword = data.get('keyword', 'Data Scientist')
    location = data.get('location', 'New Delhi')
    llm_client = LLMClient()
    scraper = LinkedInProfileScraper(search_keys, llm_client)
    try:
        memory = ScraperMemory()
        asyncio.run(scraper.run_search(keyword, location, memory))
        return jsonify({"status": "Scraping completed", "keyword": keyword, "location": location})
    except Exception as e:
        logging.error(f"Scraping failed: {str(e)}")
        return jsonify({"status": "Scraping failed", "error": str(e)}), 500

@app.route('/profiles', methods=['GET'])
def get_profiles():
    llm_client = LLMClient()
    scraper = LinkedInProfileScraper(search_keys, llm_client)
    profiles = scraper.export_to_json()
    return jsonify(profiles)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
