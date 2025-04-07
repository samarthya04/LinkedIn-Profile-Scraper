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
import signal

# Configure logging with a more reliable approach for cloud environments
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Log to stdout for Render's log collection
    ]
)

# Load environment variables
load_dotenv()

# Initialize Flask app with static files and templates for Render deployment
app = Flask(__name__, static_folder='static', template_folder='templates')

# OpenRouter LLM Client with better error handling for cloud environments
class LLMClient:
    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            logging.error("OPENROUTER_API_KEY not found in environment variables")
            raise ValueError("OPENROUTER_API_KEY not found in environment variables")
        
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        )
        self.retries = 3
        self.backoff_factor = 2

    async def query(self, prompt):
        attempts = 0
        while attempts < self.retries:
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
                
                # More robust parsing with error handling
                try:
                    action = content.split("Action: ")[1].split("\n")[0].strip()
                    reasoning = content.split("Reasoning: ")[1].strip()
                except IndexError:
                    logging.warning(f"Unexpected API response format: {content}")
                    action = "2"  # Default to scraping current page
                    reasoning = "Default action due to parsing error"
                
                return {"action": action, "reasoning": reasoning}
                
            except Exception as e:
                attempts += 1
                wait_time = self.backoff_factor ** attempts
                logging.warning(f"API attempt {attempts} failed: {str(e)}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
        
        logging.error("All API retries failed")
        return {"action": "2", "reasoning": "Default scrape due to persistent API errors"}

# Search keys with Render-friendly environment variable handling
search_keys = { 
    "username": os.getenv("LINKEDIN_EMAIL"),
    "password": os.getenv("LINKEDIN_PASSWORD"),
    "keywords": os.getenv("SEARCH_KEYWORDS", "Data Scientist,Software Engineer").split(","),
    "locations": os.getenv("SEARCH_LOCATIONS", "New Delhi,Bhubaneswar").split(","),
    "filename": os.getenv("OUTPUT_FILENAME", "profiles.json")
}

# Validate required environment variables
if not search_keys["username"] or not search_keys["password"]:
    logging.error("LinkedIn credentials not found in environment variables")
    raise ValueError("LINKEDIN_EMAIL or LINKEDIN_PASSWORD not found in environment variables")

logging.info(f"Loaded credentials - Username: {search_keys['username']}")
logging.info(f"Search parameters - Keywords: {search_keys['keywords']}, Locations: {search_keys['locations']}")

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
        # Stop if same action repeated or if running too long (3 hours max for Render)
        max_runtime = 10800  # 3 hours in seconds
        time_elapsed = (datetime.now() - self.state['start_time']).total_seconds()
        return self.state['action_count'] > 5 or time_elapsed > max_runtime

class LinkedInProfileScraper:
    def __init__(self, search_keys, llm_client):
        self.search_keys = search_keys
        self.llm = llm_client
        # Use a database location that works with Render's ephemeral filesystem
        db_path = os.getenv("DATABASE_PATH", 'linkedin_profiles.db')
        self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self.max_profiles = int(os.getenv("MAX_PROFILES", 200))
        self.session = AsyncHTMLSession()
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'
        ]
        self.active_tasks = []
        self.should_terminate = False

    def register_signal_handlers(self):
        # Handle graceful shutdown for Render's container termination
        def handle_shutdown(signum, frame):
            logging.info(f"Received signal {signum}. Initiating graceful shutdown...")
            self.should_terminate = True
            self.export_to_json()  # Save data before terminating
            for task in self.active_tasks:
                if not task.done():
                    task.cancel()
        
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

    def _init_db(self):
        with self.db_conn:
            self.db_conn.execute('''CREATE TABLE IF NOT EXISTS profiles
                                 (id TEXT PRIMARY KEY,
                                  name TEXT,
                                  url TEXT,
                                  location TEXT,
                                  title TEXT,
                                  company TEXT,
                                  connection_degree TEXT,
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
                cursor.execute('''INSERT OR REPLACE INTO profiles 
                               (id, name, url, location, title, company, connection_degree, last_scraped)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                            (profile.get('id', ''), profile.get('name', ''), 
                             profile.get('url', ''), profile.get('location', ''),
                             profile.get('title', ''), profile.get('company', ''),
                             profile.get('connection_degree', ''), profile.get('timestamp', '')))
        self.export_to_json()

    def _human_like_delay(self):
        if self.should_terminate:
            return
            
        # More varied delays to avoid detection patterns
        base_delay = random.uniform(5, 10)
        
        # Increase delay on larger profile counts
        if self._count_profiles() > 100:
            base_delay *= 1.5
        
        # Add small random variation
        jitter = random.uniform(-1, 1)
        delay = base_delay + jitter
        
        # Cap at reasonable bounds
        delay = max(3, min(delay, 15))
        
        time.sleep(delay)

    async def login(self):
        headers = {'User-Agent': random.choice(self.user_agents)}
        try:
            # Initial GET to login page
            login_url = "https://www.linkedin.com/login"
            response = await self.session.get(login_url, headers=headers)
            await response.html.arender(timeout=30)  # Increased timeout for slower environments
            
            csrf_token = response.html.xpath("//input[@name='csrfToken']/@value", first=True)
            if not csrf_token:
                logging.error("CSRF token not found on login page")
                raise ValueError("CSRF token not found - LinkedIn may have changed their login page")

            logging.info("CSRF token obtained, attempting login")
            
            # POST login data
            login_data = {
                'session_key': self.search_keys["username"],
                'session_password': self.search_keys["password"],
                'csrfToken': csrf_token
            }
            
            login_response = await self.session.post(login_url, data=login_data, headers=headers)
            
            # More robust login verification
            await login_response.html.arender(timeout=30)
            if "feed-navigation" in login_response.text or "global-nav" in login_response.text:
                logging.info("Login successful")
            else:
                logging.error("Login failed - authentication error")
                raise Exception("Login failed - check credentials or CAPTCHA challenge")
                
        except Exception as e:
            logging.error(f"Login failed: {str(e)}")
            raise
            
        self._human_like_delay()

    async def navigate_to_people_search(self):
        return "https://www.linkedin.com/search/results/people/"

    async def enter_search_keys(self, keyword, location):
        # URL encode parameters properly for complex searches
        search_url = f"https://www.linkedin.com/search/results/people/?keywords={keyword.replace(' ', '%20')}%20{location.replace(' ', '%20')}"
        headers = {'User-Agent': random.choice(self.user_agents)}
        try:
            response = await self.session.get(search_url, headers=headers)
            await response.html.arender(timeout=30)  # Increased timeout
            logging.info(f"Searching for: {keyword} in {location}")
            return response
        except Exception as e:
            logging.error(f"Search failed: {str(e)}")
            raise
            
        self._human_like_delay()

    async def get_page_hash(self, response):
        # Get a unique identifier for the page content
        try:
            profile_content = ''.join(response.html.xpath("//a[contains(@href, '/in/')]/@href"))
            return hashlib.md5(profile_content.encode()).hexdigest()
        except:
            # Fallback to full page hash if profile extraction fails
            return hashlib.md5(response.text.encode()).hexdigest()

    async def decide_next_action(self, response, memory):
        profile_count = self._count_profiles()
        page_hash = await self.get_page_hash(response)
        
        try:
            # Enhanced element detection
            profile_cards = len(response.html.xpath("//a[contains(@href, '/in/')]"))
            next_button = bool(response.html.xpath("//button[@aria-label='Next' and not(@disabled)]") or 
                               response.html.xpath("//a[@aria-label='Next']"))
            
            # Check for anti-scraping signals
            captcha_detected = bool(response.html.xpath("//div[contains(text(), 'captcha') or contains(text(), 'CAPTCHA')]"))
            error_detected = bool(response.html.xpath("//div[contains(text(), 'unusual') or contains(text(), 'suspicious')]"))
            
        except Exception as e:
            logging.error(f"Error detecting elements: {str(e)}")
            profile_cards = 0
            next_button = False
            captcha_detected = False
            error_detected = False

        # Enhanced context for better LLM decisions
        summary = (f"Profiles collected: {profile_count}/{self.max_profiles}, "
                  f"Cards visible: {profile_cards}, Next button: {next_button}, "
                  f"CAPTCHA detected: {captcha_detected}, Error: {error_detected}")
        
        prompt = f"""
        Current state: {summary}
        Previous action repeated: {memory.state['action_count']} times
        Time running: {(datetime.now() - memory.state['start_time']).total_seconds()/60:.1f} minutes
        
        Options:
        1. Click next page
        2. Scrape current page
        3. Stop scraping
        
        Respond in this format:
        Action: <number>
        Reasoning: <text>
        """
        
        # Stop immediately if anti-scraping measures detected
        if captcha_detected or error_detected:
            return {"action": "3", "reasoning": "Anti-scraping measures detected. Stopping to avoid account restrictions."}
            
        decision = await self.llm.query(prompt)
        
        # Override if we still need profiles and have content
        if decision["action"] == "3" and profile_count < self.max_profiles and (next_button or profile_cards > 0):
            if profile_cards > 0:
                return {"action": "2", "reasoning": "Override: Less than target profiles and content available for scraping."}
            elif next_button:
                return {"action": "1", "reasoning": "Override: Less than target profiles and next page available."}
                
        return decision

    async def extract_profile_details(self, element):
        """Extract more comprehensive profile information from a profile card"""
        details = {}
        try:
            # Job title
            title_elements = element.xpath(".//div[contains(@class, 'entity-result__primary-subtitle')]")
            if title_elements:
                details['title'] = title_elements[0].text.strip()
                
            # Company/organization
            company_elements = element.xpath(".//div[contains(@class, 'entity-result__secondary-subtitle')]")
            if company_elements:
                details['company'] = company_elements[0].text.strip()
                
            # Location
            location_elements = element.xpath(".//div[contains(@class, 'entity-result__tertiary-subtitle')]")
            if location_elements:
                details['location'] = location_elements[0].text.strip()
                
            # Connection degree (1st, 2nd, 3rd)
            connection_elements = element.xpath(".//span[contains(@class, 'distance-badge')]")
            if connection_elements:
                details['connection_degree'] = connection_elements[0].text.strip()
        except Exception as e:
            logging.warning(f"Error extracting profile details: {str(e)}")
            
        return details

    async def scrape_profiles(self, response, memory):
        profiles = []
        try:
            # More robust profile card detection
            profile_cards = response.html.xpath("//li[contains(@class, 'reusable-search__result-container')]")
            
            for card in profile_cards:
                if self._count_profiles() >= self.max_profiles or self.should_terminate:
                    break
                    
                try:
                    link_element = card.xpath(".//a[contains(@href, '/in/')]", first=True)
                    if not link_element:
                        continue
                        
                    url = link_element.attrs.get('href', '').split("?")[0]
                    if "/in/" not in url or url in memory.state['visited_urls']:
                        continue
                        
                    name = link_element.text.strip()
                    if name and "linkedin.com/in/" in url:
                        profile_id = url.split('/in/')[-1].strip('/')
                        
                        if not self._profile_exists(profile_id):
                            # Extract additional profile details
                            details = await self.extract_profile_details(card)
                            
                            profile_data = {
                                'id': profile_id,
                                'name': name,
                                'url': url,
                                'timestamp': datetime.now().isoformat(),
                                **details  # Add all extracted details
                            }
                            
                            profiles.append(profile_data)
                            memory.state['visited_urls'].add(url)
                            logging.info(f"Scraped profile: {name} - {url}")
                except Exception as e:
                    logging.warning(f"Error processing profile card: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Scraping failed: {str(e)}")
            
        return profiles

    async def click_next_page(self, response):
        try:
            # Try both types of next page navigation
            next_url = response.html.xpath("//a[@aria-label='Next']/@href", first=True)
            
            if next_url:
                headers = {'User-Agent': random.choice(self.user_agents)}
                next_response = await self.session.get(f"https://www.linkedin.com{next_url}", headers=headers)
                await next_response.html.arender(timeout=30)
                self._human_like_delay()
                return next_response
                
            # Alternative next button that uses JavaScript
            next_button = response.html.xpath("//button[@aria-label='Next' and not(@disabled)]", first=True)
            if next_button:
                # Use JavaScript to click the button
                await response.html.page.evaluate('''
                    () => {
                        const nextButton = document.querySelector('button[aria-label="Next"]:not([disabled])');
                        if (nextButton) nextButton.click();
                    }
                ''')
                await asyncio.sleep(3)  # Wait for navigation
                await response.html.page.reload()  # Reload to get updated content
                await response.html.arender(timeout=30)
                self._human_like_delay()
                return response  # Return the same response object with updated content
                
            return None
        except Exception as e:
            logging.error(f"Failed to navigate to next page: {str(e)}")
            return None

    async def run_search(self, keyword, location, memory):
        if self.should_terminate:
            return
            
        try:
            await self.login()
            await self.navigate_to_people_search()
            response = await self.enter_search_keys(keyword, location)
            
            loop_count = 0
            while self._count_profiles() < self.max_profiles and not self.should_terminate:
                loop_count += 1
                
                # Enhanced stopping criteria
                if memory.should_stop() or loop_count > 30:
                    logging.warning(f"Stopping search for '{keyword} {location}' after {loop_count} iterations")
                    break
                
                decision = await self.decide_next_action(response, memory)
                action, reasoning = decision["action"], decision["reasoning"]
                page_hash = await self.get_page_hash(response)
                logging.info(f"Decision for '{keyword} {location}': {action} - {reasoning}")
                memory.update(response.url, action, page_hash)
                
                if action == "1":
                    next_response = await self.click_next_page(response)
                    if not next_response:
                        logging.warning("Could not navigate to next page, stopping")
                        break
                    response = next_response
                    
                elif action == "2":
                    profiles = await self.scrape_profiles(response, memory)
                    if profiles:
                        self._save_profiles(profiles)
                    self._human_like_delay()
                    
                elif action == "3":
                    logging.info(f"Stopping search for '{keyword} {location}' based on LLM decision")
                    break
                    
        except Exception as e:
            logging.error(f"Error in search task for '{keyword} {location}': {str(e)}")
            # Still export what we have before ending
            self.export_to_json()

    async def run_parallel_searches(self):
        self.register_signal_handlers()
        
        # Use a semaphore to limit concurrent searches and avoid overloading
        semaphore = asyncio.Semaphore(2)
        
        async def limited_run(kw, loc):
            async with semaphore:
                if not self.should_terminate and self._count_profiles() < self.max_profiles:
                    memory = ScraperMemory()
                    try:
                        await self.run_search(kw, loc, memory)
                    except Exception as e:
                        logging.error(f"Task for '{kw} {loc}' failed: {str(e)}")

        # Create and track tasks for each keyword-location pair
        tasks = []
        for kw in self.search_keys["keywords"]:
            for loc in self.search_keys["locations"]:
                if self._count_profiles() < self.max_profiles and not self.should_terminate:
                    task = asyncio.create_task(limited_run(kw, loc))
                    tasks.append(task)
                    self.active_tasks.append(task)
        
        # Wait for all tasks or until terminated
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logging.info("Tasks cancelled during shutdown")
        finally:
            # Ensure data is saved before exiting
            self.export_to_json()

    def export_to_json(self):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT id, name, url, location, title, company, connection_degree, last_scraped FROM profiles')
        
        profiles = [{
            'id': row[0],
            'name': row[1],
            'url': row[2],
            'location': row[3],
            'title': row[4],
            'company': row[5],
            'connection_degree': row[6],
            'scraped_at': row[7]
        } for row in cursor.fetchall()]
        
        # Attempt to read existing profiles
        try:
            with open(self.search_keys["filename"], 'r') as f:
                existing_profiles = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing_profiles = []
        
        # Get unique profiles by URL
        existing_urls = {p.get('url') for p in existing_profiles}
        new_profiles = [p for p in profiles if p.get('url') not in existing_urls]
        updated_profiles = existing_profiles + new_profiles
        
        try:
            with open(self.search_keys["filename"], 'w') as f:
                json.dump(updated_profiles, f, indent=2)
            logging.info(f"Exported {len(updated_profiles)} profiles to {self.search_keys['filename']}")
        except Exception as e:
            logging.error(f"Failed to export profiles to JSON: {str(e)}")
            
        return updated_profiles

    def run(self):
        try:
            asyncio.run(self.run_parallel_searches())
            logging.info(f"Total profiles collected: {self._count_profiles()}")
        finally:
            # Clean up resources
            self.db_conn.close()

# Health check endpoint for Render
@app.route('/health')
def health_check():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

# Main application routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    data = request.get_json() or {}
    keyword = data.get('keyword', search_keys["keywords"][0])
    location = data.get('location', search_keys["locations"][0])
    
    # Optional parameters
    max_profiles = data.get('max_profiles', None)
    if max_profiles and max_profiles.isdigit():
        search_keys["max_profiles"] = int(max_profiles)
    
    try:
        llm_client = LLMClient()
        scraper = LinkedInProfileScraper(search_keys, llm_client)
        
        # Create a non-async function that can run in a background thread
        def run_scraper_background():
            try:
                # Create and run a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                memory = ScraperMemory()
                
                # Run the async function in the new event loop
                loop.run_until_complete(scraper.run_search(keyword, location, memory))
                
                # Close the event loop when done
                loop.close()
                
            except Exception as e:
                logging.error(f"Background scraper thread error: {str(e)}")
        
        # Start the background thread
        import threading
        thread = threading.Thread(target=run_scraper_background)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "status": "Scraping started", 
            "keyword": keyword, 
            "location": location,
            "message": "Check /profiles endpoint for results"
        })
    except Exception as e:
        logging.error(f"Error starting scrape: {str(e)}")
        return jsonify({"status": "Scraping failed", "error": str(e)}), 500
    
    
@app.route('/profiles', methods=['GET'])
def get_profiles():
    # Simplified profile retrieval directly from the JSON file
    try:
        with open(search_keys["filename"], 'r') as f:
            profiles = json.load(f)
        return jsonify(profiles)
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify([])

@app.route('/stats', methods=['GET'])
def get_stats():
    # Get statistics about the scraping process
    try:
        llm_client = LLMClient()
        scraper = LinkedInProfileScraper(search_keys, llm_client)
        profile_count = scraper._count_profiles()
        
        try:
            with open(search_keys["filename"], 'r') as f:
                profiles = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            profiles = []
        
        locations = {}
        titles = {}
        companies = {}
        
        for profile in profiles:
            # Count locations
            location = profile.get('location', 'Unknown')
            locations[location] = locations.get(location, 0) + 1
            
            # Count job titles
            title = profile.get('title', 'Unknown')
            titles[title] = titles.get(title, 0) + 1
            
            # Count companies
            company = profile.get('company', 'Unknown')
            companies[company] = companies.get(company, 0) + 1
        
        return jsonify({
            "total_profiles": profile_count,
            "unique_locations": len(locations),
            "unique_titles": len(titles),
            "unique_companies": len(companies),
            "top_locations": dict(sorted(locations.items(), key=lambda x: x[1], reverse=True)[:5]),
            "top_titles": dict(sorted(titles.items(), key=lambda x: x[1], reverse=True)[:5]),
            "top_companies": dict(sorted(companies.items(), key=lambda x: x[1], reverse=True)[:5])
        })
    except Exception as e:
        logging.error(f"Error getting stats: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Get port from environment variable (Render sets this automatically)
    port = int(os.environ.get("PORT", 5000))
    
    # Configure server for Render
    app.run(host='0.0.0.0', port=port, debug=False)