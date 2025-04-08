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
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import threading
from shutil import which

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Changed from INFO to DEBUG for more detailed logs
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')

class LLMClient:
    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            logging.warning("OPENROUTER_API_KEY not found, using fallback decision logic")
        self.client = AsyncOpenAI(
            api_key=api_key or "dummy_key",
            base_url="https://openrouter.ai/api/v1"
        )

    async def query(self, prompt):
        try:
            if not os.getenv("OPENROUTER_API_KEY"):
                return {"action": "2", "reasoning": "Default scrape because no API key is available"}
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
            try:
                action = content.split("Action: ")[1].split("\n")[0] if "Action: " in content else "2"
                reasoning = content.split("Reasoning: ")[1] if "Reasoning: " in content else "Default action"
            except IndexError:
                action = "2"
                reasoning = "Default action due to parsing error"
            return {"action": action, "reasoning": reasoning}
        except Exception as e:
            logging.error(f"OpenRouter API query failed: {str(e)}")
            return {"action": "2", "reasoning": "Default scrape due to API error"}

class ScraperMemory:
    def __init__(self):
        self.state = {
            'visited_urls': set(),
            'page_hashes': set(),
            'last_action': None,
            'action_count': 0,
            'start_time': datetime.now()
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
        max_runtime = 10800  # 3 hours
        time_elapsed = (datetime.now() - self.state['start_time']).total_seconds()
        return self.state['action_count'] > 5 or time_elapsed > max_runtime

class LinkedInProfileScraper:
    def __init__(self, search_keys, llm_client, headless=True):
        self.search_keys = search_keys
        os.makedirs("/tmp", exist_ok=True)
        self.db_conn = sqlite3.connect('/tmp/linkedin_profiles.db', check_same_thread=False)
        self._init_db()
        self.max_profiles = int(os.getenv("MAX_PROFILES", 200))
        self.headless = headless
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
        ]
        self.llm = llm_client
        # Create a screenshots directory
        os.makedirs("/tmp/screenshots", exist_ok=True)

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

    def _human_like_typing(self, element, text):
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.25))

    def _human_like_delay(self):
        base_delay = random.uniform(3, 7)
        if random.random() < 0.2:
            base_delay += random.uniform(5, 15)
        profile_count = self._count_profiles()
        if profile_count > 50:
            base_delay *= 1.2
        if profile_count > 100:
            base_delay *= 1.5
        if profile_count > 150:
            base_delay *= 1.8
        actual_delay = base_delay * random.uniform(0.8, 1.2)
        if random.random() < 0.1:
            actual_delay += random.uniform(15, 30)
        logging.info(f"Waiting for {actual_delay:.2f} seconds")
        time.sleep(actual_delay)

    def _randomize_browser_behavior(self, driver):
        driver.execute_script("""
            var event = new MouseEvent('mousemove', {
                'view': window,
                'bubbles': true,
                'cancelable': true,
                'clientX': Math.floor(Math.random() * 1920),
                'clientY': Math.floor(Math.random() * 1080)
            });
            document.dispatchEvent(event);
        """)
        time.sleep(random.uniform(0.5, 1.5))

    def get_chrome_options(self):
        options = webdriver.ChromeOptions()
        # Find Chrome binary automatically if not found at default location
        chrome_binary = os.getenv("CHROME_BINARY_PATH", "/usr/lib/chromium-browser/chrome")
        if not os.path.exists(chrome_binary):
            logging.warning(f"Chrome binary not found at {chrome_binary}")
            chrome_path = which("google-chrome") or which("chrome") or which("chromium")
            if chrome_path:
                logging.info(f"Found Chrome at {chrome_path}")
                chrome_binary = chrome_path
            else:
                logging.warning("Chrome binary not found automatically. Using default configuration.")
        
        if os.path.exists(chrome_binary):
            options.binary_location = chrome_binary
            logging.info(f"Using Chrome binary at: {chrome_binary}")
        
        options.add_argument(f"user-agent={random.choice(self.user_agents)}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # Comment out headless mode for troubleshooting login issues
        # options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-extensions")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        return options

    async def login(self, driver, max_retries=3):
        for attempt in range(max_retries):
            try:
                logging.info(f"Login attempt {attempt + 1}/{max_retries}")
                logging.info("Deleting cookies")
                driver.delete_all_cookies()
                logging.info("Navigating to LinkedIn homepage")
                driver.get("https://www.linkedin.com")
                # Take screenshot of the initial page
                driver.save_screenshot(f"/tmp/screenshots/initial_page_{attempt}.png")
                logging.info(f"Saved screenshot of initial page: /tmp/screenshots/initial_page_{attempt}.png")
                self._human_like_delay()
                
                try:
                    logging.info("Looking for sign-in button")
                    sign_in = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-tracking-control-name='guest_homepage-basic_nav-header-signin']"))
                    )
                    logging.info("Sign-in button found, clicking")
                    driver.save_screenshot(f"/tmp/screenshots/before_signin_click_{attempt}.png")
                    sign_in.click()
                    logging.info("Clicked sign in from homepage")
                except Exception as e:
                    logging.info(f"Could not find sign-in button: {str(e)}")
                    logging.info("Navigating directly to login page")
                    driver.get("https://www.linkedin.com/login")
                    driver.save_screenshot(f"/tmp/screenshots/login_page_direct_{attempt}.png")
                
                # Take screenshot of login page
                driver.save_screenshot(f"/tmp/screenshots/login_page_{attempt}.png")
                logging.info(f"Waiting for username field")
                
                try:
                    WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.ID, "username"))
                    )
                    logging.info("Username field found")
                    email_field = driver.find_element(By.ID, "username")
                    logging.info(f"Typing email: {self.search_keys['username'][:3]}***")
                    self._human_like_typing(email_field, self.search_keys["username"])
                    time.sleep(random.uniform(1.5, 3.0))
                    
                    logging.info("Looking for password field")
                    password_field = driver.find_element(By.ID, "password")
                    logging.info("Typing password: ********")
                    self._human_like_typing(password_field, self.search_keys["password"])
                    time.sleep(random.uniform(1.5, 3.0))
                    
                    # Take screenshot before clicking login
                    driver.save_screenshot(f"/tmp/screenshots/before_login_click_{attempt}.png")
                    logging.info("Randomizing browser behavior")
                    self._randomize_browser_behavior(driver)
                    
                    logging.info("Finding login button")
                    login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
                    logging.info("Clicking login button")
                    login_button.click()
                    logging.info("Login button clicked")
                    
                    # Take screenshot after clicking login
                    driver.save_screenshot(f"/tmp/screenshots/after_login_click_{attempt}.png")
                    
                    # Check for CAPTCHA or security challenges
                    if self._check_for_captcha(driver):
                        logging.warning("CAPTCHA detected! Need manual intervention")
                        driver.save_screenshot(f"/tmp/screenshots/captcha_detected_{attempt}.png")
                        # Wait longer to allow manual CAPTCHA solving if running in visible mode
                        time.sleep(60)
                    
                    logging.info("Waiting for successful login...")
                    try:
                        WebDriverWait(driver, 180).until(
                            EC.any_of(
                                EC.presence_of_element_located((By.ID, "global-nav")),
                                EC.presence_of_element_located((By.ID, "voyager-feed")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".feed-container"))
                            )
                        )
                        # Take screenshot of successful login
                        driver.save_screenshot(f"/tmp/screenshots/login_successful_{attempt}.png")
                        logging.info("Login successful")
                        self._randomize_browser_behavior(driver)
                        break
                    except TimeoutException:
                        logging.warning(f"Login attempt {attempt + 1} failed: Timeout waiting for home page")
                        driver.save_screenshot(f"/tmp/screenshots/login_timeout_{attempt}.png")
                        if attempt == max_retries - 1:
                            raise Exception("Login failed - Timeout or CAPTCHA after retries")
                except TimeoutException:
                    logging.warning(f"Login attempt {attempt + 1} failed: Timeout waiting for username field")
                    driver.save_screenshot(f"/tmp/screenshots/username_timeout_{attempt}.png")
                    if attempt == max_retries - 1:
                        raise Exception("Login failed - Timeout waiting for login form")
            except Exception as e:
                logging.error(f"Login attempt failed: {str(e)}")
                driver.save_screenshot(f"/tmp/screenshots/login_error_{attempt}.png")
                if attempt == max_retries - 1:
                    raise
                time.sleep(random.uniform(30, 60))
        self._human_like_delay()

    def _check_for_captcha(self, driver):
        """Check if a CAPTCHA or security challenge is present"""
        captcha_indicators = [
            "//div[contains(text(), 'CAPTCHA')]",
            "//div[contains(text(), 'security check')]",
            "//div[contains(text(), 'unusual activity')]",
            "//div[contains(text(), 'verify')]",
            "//iframe[contains(@src, 'captcha')]",
            "//iframe[contains(@src, 'challenge')]"
        ]
        
        for indicator in captcha_indicators:
            try:
                elements = driver.find_elements(By.XPATH, indicator)
                if elements:
                    logging.warning(f"CAPTCHA/Security check indicator found: {indicator}")
                    return True
            except Exception:
                pass
        return False

    async def navigate_to_people_search(self, driver):
        url = "https://www.linkedin.com/search/results/people/"
        logging.info(f"Navigating to people search: {url}")
        driver.get(url)
        driver.save_screenshot("/tmp/screenshots/people_search_page.png")
        self._human_like_delay()
        logging.info("Navigated to people search page")

    async def enter_search_keys(self, driver, keyword, location):
        try:
            logging.info("Waiting for search bar")
            WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search']"))
            )
            driver.save_screenshot("/tmp/screenshots/before_search.png")
            search_bar = driver.find_element(By.XPATH, "//input[@placeholder='Search']")
            search_bar.clear()
            search_query = f"{keyword} {location}"
            logging.info(f"Typing search query: {search_query}")
            for char in search_query:
                search_bar.send_keys(char)
                time.sleep(random.uniform(0.1, 0.3))
            logging.info("Pressing Enter to search")
            search_bar.send_keys(Keys.RETURN)
            driver.save_screenshot("/tmp/screenshots/after_search.png")
            self._human_like_delay()
            logging.info(f"Searching for: {search_query}")
        except Exception as e:
            logging.error(f"Search input failed: {str(e)}")
            driver.save_screenshot("/tmp/screenshots/search_error.png")
            raise

    async def get_page_hash(self, driver):
        return hashlib.md5(driver.page_source.encode()).hexdigest()

    async def decide_next_action(self, driver, memory):
        profile_count = self._count_profiles()
        page_hash = await self.get_page_hash(driver)
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(5)
            profile_cards = len(driver.find_elements(By.XPATH, "//a[contains(@href, '/in/')]"))
            next_button_exists = len(driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Next']")) > 0
            next_button = False
            if next_button_exists:
                next_button = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Next']").is_enabled()
        except Exception:
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
        
        if decision["action"] not in ["1", "2", "3"]:
            return {
                "action": "2" if profile_cards > 0 else "1" if next_button else "3",
                "reasoning": "Fallback: Invalid LLM response, choosing based on page state"
            }
        
        if decision["action"] == "3" and profile_count < self.max_profiles and (next_button or profile_cards > 0):
            return {
                "action": "1" if next_button else "2",
                "reasoning": "Override: Less than max profiles, continuing with next page or scrape"
            }
        return decision

    async def scrape_profiles(self, driver, memory, retries=2):
        for attempt in range(retries):
            try:
                logging.info(f"Scraping profiles attempt {attempt+1}/{retries}")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(5)
                driver.save_screenshot(f"/tmp/screenshots/before_scrape_attempt_{attempt}.png")
                
                logging.info("Waiting for profile links")
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/in/')]"))
                )
                
                links = driver.find_elements(By.XPATH, "//a[contains(@href, '/in/')]")
                logging.info(f"Found {len(links)} potential profile links")
                
                profiles = []
                for i, link in enumerate(links):
                    if self._count_profiles() >= self.max_profiles:
                        logging.info(f"Reached maximum profile count ({self.max_profiles})")
                        break
                    try:
                        url = link.get_attribute("href").split("?")[0]
                        if "/in/" not in url or url in memory.state['visited_urls']:
                            continue
                        
                        logging.info(f"Processing link {i+1}: {url}")
                        name_elem = link.find_element(By.XPATH, ".//span[contains(@class, 'entity-result__title-text')] | .//span")
                        name = name_elem.text.strip()
                        
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
                    except Exception as e:
                        logging.debug(f"Error processing link: {str(e)}")
                        continue
                
                logging.info(f"Successfully scraped {len(profiles)} new profiles")
                driver.save_screenshot("/tmp/screenshots/after_scrape.png")
                self._randomize_browser_behavior(driver)
                return profiles
            except TimeoutException:
                logging.warning(f"Attempt {attempt + 1}/{retries} failed: Timeout waiting for profile links")
                driver.save_screenshot(f"/tmp/screenshots/scrape_timeout_{attempt}.png")
                if attempt == retries - 1:
                    logging.error("Max retries reached for scraping profiles")
                    return []
                self._human_like_delay()
        return []

    async def click_next_with_retry(self, driver, retries=3):
        for attempt in range(retries):
            try:
                logging.info(f"Trying to click next button, attempt {attempt+1}/{retries}")
                driver.save_screenshot(f"/tmp/screenshots/before_next_click_{attempt}.png")
                
                next_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Next']"))
                )
                logging.info("Next button found and clickable")
                next_button.click()
                logging.info("Next button clicked")
                driver.save_screenshot(f"/tmp/screenshots/after_next_click_{attempt}.png")
                self._human_like_delay()
                self._randomize_browser_behavior(driver)
                return True
            except Exception as e:
                logging.warning(f"Retry {attempt+1}/{retries} for next button: {str(e)}")
                driver.save_screenshot(f"/tmp/screenshots/next_button_error_{attempt}.png")
                await asyncio.sleep(5)
        logging.error("Failed to click next after retries")
        return False

    async def navigate_search_results(self, driver, keyword, location, memory):
        await self.enter_search_keys(driver, keyword, location)
        while self._count_profiles() < self.max_profiles:
            if memory.should_stop():
                logging.error("Stopping due to potential infinite loop or timeout")
                break
            
            decision = await self.decide_next_action(driver, memory)
            action, reasoning = decision["action"], decision["reasoning"]
            page_hash = await self.get_page_hash(driver)
            logging.info(f"LLM decided: {action} - {reasoning}")
            memory.update(driver.current_url, action, page_hash)
            
            if action == "1":
                if not await self.click_next_with_retry(driver):
                    break
            elif action == "2":
                profiles = await self.scrape_profiles(driver, memory)
                if profiles:
                    self._save_profiles(profiles)
                self._human_like_delay()
            else:  # action == "3" or any other value
                break

    async def run_search(self, driver, keyword, location, memory):
        await self.navigate_to_people_search(driver)
        await self.navigate_search_results(driver, keyword, location, memory)

    def export_to_json(self):
        try:
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
        except Exception as e:
            logging.error(f"Error exporting profiles: {str(e)}")
            return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    data = request.get_json() or {}
    email = data.get('email')
    password = data.get('password')
    keyword = data.get('keyword', 'Data Scientist')
    location = data.get('location', 'New Delhi')
    
    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password are required"})
    
    search_keys = {
        "username": email,
        "password": password,
        "keywords": [keyword],
        "locations": [location],
        "filename": os.getenv("OUTPUT_FILENAME", "/tmp/profiles.json")
    }
    
    llm_client = LLMClient()
    scraper = LinkedInProfileScraper(search_keys, llm_client, headless=False)  # Changed to non-headless
    
    def run_scraper():
        try:
            logging.info("Initializing Chrome driver")
            driver = webdriver.Chrome(options=scraper.get_chrome_options())
            memory = ScraperMemory()
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                logging.info("Starting login process")
                loop.run_until_complete(scraper.login(driver, max_retries=3))
                logging.info("Login successful, starting search")
                loop.run_until_complete(scraper.run_search(driver, keyword, location, memory))
                logging.info("Search completed")
            except Exception as e:
                logging.error(f"Scraper thread error: {str(e)}")
            finally:
                logging.info("Cleaning up resources")
                driver.quit()
                loop.close()
                scraper.db_conn.close()
        except Exception as e:
            logging.error(f"Failed to initialize Chrome: {str(e)}")
    
    thread = threading.Thread(target=run_scraper)
    thread.daemon = True
    thread.start()
    logging.info(f"Scraper thread started for keyword '{keyword}' in '{location}'")
    
    return jsonify({"status": "Scraping started", "keyword": keyword, "location": location})

@app.route('/profiles', methods=['GET'])
def get_profiles():
    filename = os.getenv("OUTPUT_FILENAME", "/tmp/profiles.json")
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                profiles = json.load(f)
            return jsonify(profiles)
        else:
            return jsonify([])
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify([])

if __name__ == "__main__":
    # Render expects the app to bind to 0.0.0.0 and use the PORT env variable
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port)
