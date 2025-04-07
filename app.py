import os
import asyncio
import json
import sqlite3
import logging
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from dotenv import load_dotenv
from openai import AsyncOpenAI
import random
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')

# OpenRouter LLM Client
class LLMClient:
    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not found in .env")
        self.client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

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

# Scraper Memory
class ScraperMemory:
    def __init__(self):
        self.state = {'visited_urls': set(), 'page_hashes': set(), 'last_action': None, 'action_count': 0}

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

# LinkedIn Profile Scraper
class LinkedInProfileScraper:
    def __init__(self, headless=True):
        self.search_keys = {
            "username": os.getenv("LINKEDIN_EMAIL"),
            "password": os.getenv("LINKEDIN_PASSWORD"),
            "keywords": ["Data Scientist", "Software Engineer"],
            "locations": ["New Delhi", "Bhubaneswar"],
            "filename": "profiles.json"
        }
        if not self.search_keys["username"] or not self.search_keys["password"]:
            raise ValueError("LINKEDIN_EMAIL or LINKEDIN_PASSWORD not found in .env")
        self.llm = LLMClient()
        self.db_conn = sqlite3.connect('linkedin_profiles.db', check_same_thread=False)
        self._init_db()
        self.max_profiles = 200
        self.headless = headless
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
        ]

    def _init_db(self):
        with self.db_conn:
            self.db_conn.execute('''CREATE TABLE IF NOT EXISTS profiles
                                 (id TEXT PRIMARY KEY, name TEXT, url TEXT, last_scraped TEXT)''')

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
                            (profile['id'], profile['name'], profile['url'], profile['timestamp']))
        self.export_to_json()

    def _human_like_delay(self):
        time.sleep(random.uniform(5, 10))

    def get_chrome_options(self):
        options = webdriver.ChromeOptions()
        options.add_argument(f"user-agent={random.choice(self.user_agents)}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        if self.headless:
            options.add_argument("--headless")
        return options

    async def login(self, driver):
        driver.get("https://www.linkedin.com/login")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "username")))
        email_field = driver.find_element(By.ID, "username")
        email_field.send_keys(self.search_keys["username"])
        password_field = driver.find_element(By.ID, "password")
        password_field.send_keys(self.search_keys["password"])
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "global-nav")))
        self._human_like_delay()

    async def navigate_to_people_search(self, driver):
        driver.get("https://www.linkedin.com/search/results/people/")
        self._human_like_delay()

    async def enter_search_keys(self, driver, keyword, location):
        search_bar = WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search']"))
        )
        search_bar.clear()
        search_query = f"{keyword} {location}"
        for char in search_query:
            search_bar.send_keys(char)
            time.sleep(random.uniform(0.1, 0.3))
        search_bar.send_keys(Keys.RETURN)
        self._human_like_delay()

    async def get_page_hash(self, driver):
        return hashlib.md5(driver.page_source.encode()).hexdigest()

    async def decide_next_action(self, driver, memory):
        profile_count = self._count_profiles()
        page_hash = await self.get_page_hash(driver)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(5)
        profile_cards = len(driver.find_elements(By.XPATH, "//a[contains(@href, '/in/')]"))
        try:
            next_button = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Next']").is_enabled()
        except NoSuchElementException:
            next_button = False

        summary = f"Profiles: {profile_count}/{self.max_profiles}, Cards: {profile_cards}, Next: {next_button}"
        prompt = f"Current state: {summary}\nPrevious action repeated: {memory.state['action_count']} times\nOptions: 1. Click next page 2. Scrape current page 3. Stop scraping\nRespond: Action: <number>\nReasoning: <text>"
        decision = await self.llm.query(prompt)
        if decision["action"] == "3" and profile_count < self.max_profiles and (next_button or profile_cards > 0):
            return {"action": "1" if next_button else "2", "reasoning": "Override: Less than 200 profiles, continuing."}
        return decision

    async def scrape_profiles(self, driver, memory):
        profiles = []
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(5)
        links = WebDriverWait(driver, 60).until(EC.presence_of_all_elements_located((By.XPATH, "//a[contains(@href, '/in/')]")))
        for link in links:
            if self._count_profiles() >= self.max_profiles:
                break
            url = link.get_attribute("href").split("?")[0]
            if "/in/" not in url or url in memory.state['visited_urls']:
                continue
            name = link.find_element(By.XPATH, ".//span[contains(@class, 'entity-result__title-text')] | .//span").text.strip()
            if name and "linkedin.com/in/" in url:
                profile_id = url.split('/in/')[-1].strip('/')
                if not self._profile_exists(profile_id):
                    profiles.append({'id': profile_id, 'name': name, 'url': url, 'timestamp': datetime.now().isoformat()})
                    memory.state['visited_urls'].add(url)
        return profiles

    async def click_next_with_retry(self, driver):
        next_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Next']")))
        next_button.click()
        self._human_like_delay()
        return True

    async def run_search(self, keyword, location):
        driver = webdriver.Chrome(options=self.get_chrome_options())
        memory = ScraperMemory()
        try:
            await self.login(driver)
            await self.navigate_to_people_search(driver)
            await self.enter_search_keys(driver, keyword, location)
            while self._count_profiles() < self.max_profiles:
                if memory.should_stop():
                    break
                decision = await self.decide_next_action(driver, memory)
                page_hash = await self.get_page_hash(driver)
                memory.update(driver.current_url, decision["action"], page_hash)
                if decision["action"] == "1":
                    await self.click_next_with_retry(driver)
                elif decision["action"] == "2":
                    profiles = await self.scrape_profiles(driver, memory)
                    if profiles:
                        self._save_profiles(profiles)
        finally:
            driver.quit()

    def export_to_json(self):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT id, name, url, last_scraped FROM profiles')
        profiles = [{'name': row[1], 'url': row[2], 'scraped_at': row[3]} for row in cursor.fetchall()]
        with open(self.search_keys["filename"], 'w') as f:
            json.dump(profiles, f, indent=2)
        return profiles

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    data = request.get_json()
    keyword = data.get('keyword', 'Data Scientist')
    location = data.get('location', 'New Delhi')
    scraper = LinkedInProfileScraper(headless=True)
    asyncio.run(scraper.run_search(keyword, location))
    return jsonify({"status": "Scraping started", "keyword": keyword, "location": location})

@app.route('/profiles', methods=['GET'])
def get_profiles():
    scraper = LinkedInProfileScraper()
    profiles = scraper.export_to_json()
    return jsonify(profiles)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)