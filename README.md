```markdown
# LinkedIn Profile Scraper

A robust, Selenium-based tool designed to scrape at least 200 LinkedIn profiles based on specified keywords and locations. It leverages an LLM (via OpenRouter) for intelligent decision-making, caching for efficiency, and SQLite for persistent storage.

---

## Technical Specs

### High-Level Design
This scraper automates the collection of LinkedIn profiles using a combination of web automation, AI-driven navigation, and data persistence. Here's how it works:

- **Tools and Libraries**:
  - **Selenium**: Automates browser interactions to log in, search, and scrape profiles.
  - **OpenRouter LLM (via AsyncOpenAI)**: Decides whether to scrape the current page, move to the next page, or stop, based on page state.
  - **SQLite**: Stores scraped profiles to avoid duplicates and enable persistence.
  - **asyncio**: Runs multiple keyword-location searches concurrently for efficiency.
  - **python-dotenv**: Manages credentials securely via a `.env` file.

- **Logic Flow**:
  1. **Initialization**: Loads credentials from `.env` and sets up SQLite database (`linkedin_profiles.db`).
  2. **Login**: Uses Selenium to log into LinkedIn, handling CAPTCHA/2FA manually if needed.
  3. **Search**: Navigates to the people search page and enters keyword-location pairs (e.g., "Data Scientist New Delhi").
  4. **Decision-Making**: The LLM analyzes the current page (profile count, available cards, next button) and decides the next action, with decisions cached in `actions_cache.json`.
  5. **Scraping**: Extracts profile names and URLs, storing them in SQLite and exporting to `profiles.json`.
  6. **Loop Control**: Continues until 200 profiles are collected, overriding stop signals if necessary.

- **Key Features**:
  - **Caching**: LLM decisions cached in JSON; profiles stored in SQLite.
  - **Anti-Detection**: Random user agents, human-like typing (0.1-0.3s delays), and variable page waits (5-15s).
  - **Debugging**: Screenshots (`debug_screenshot.png`) and HTML dumps (`page_source.html`) on failures.

### Dependencies
Listed in `requirements.txt`:
- `selenium`
- `webdriver-manager`
- `openai`
- `python-dotenv`
- `sqlite3` (Python built-in)

---

## Setup & Usage

### Installation
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/yourusername/linkedin-scraper.git
   cd linkedin-scraper
   ```
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure Credentials**:
   Create a `.env` file in the root directory with:
   ```plaintext
   LINKEDIN_EMAIL=your_email@example.com
   LINKEDIN_PASSWORD=your_password
   OPENROUTER_API_KEY=your_openrouter_api_key
   ```
   - Obtain an OpenRouter API key from [openrouter.ai](https://openrouter.ai).
4. **Ensure Chrome Compatibility**:
   - The `webdriver-manager` automatically downloads the appropriate ChromeDriver.

### Running the Scraper
```bash
python scraper.py
```
- **What Happens**:
  - A Chrome browser opens (non-headless by default for visibility).
  - Logs in to LinkedIn; if CAPTCHA/2FA appears, complete it manually and press Enter.
  - Scrapes profiles for combinations of keywords ("Data Scientist", "Software Engineer") and locations ("New Delhi", "Bhubaneswar").
  - Outputs to `profiles.json` and logs progress in `profile_scraper.log`.

- **Example Command Output**:
  ```
  2025-04-07 10:00:00 - INFO - Loaded credentials - Username: your_email@example.com
  2025-04-07 10:00:05 - INFO - Login successful
  2025-04-07 10:00:10 - INFO - Searching for: Data Scientist New Delhi
  2025-04-07 10:00:20 - INFO - LLM decided: 2 - Reasoning: Profiles available on current page
  2025-04-07 10:00:30 - INFO - Total profiles collected: 200
  ```

### Sample Output (`profiles.json`)
```json
[
  {
    "name": "Praneesh Sharma",
    "url": "https://www.linkedin.com/in/ACoAADnfzRQBYUFuSALlJ7N7wVefvatBB85yWKw",
    "scraped_at": "2025-04-05T03:27:39.406009"
  },
  {
    "name": "Medhavi Sahgal",
    "url": "https://www.linkedin.com/in/ACoAAD0qVRABro1W9l99jUN1g6axAMArkA5d7lE",
    "scraped_at": "2025-04-05T03:27:39.445114"
  }
]
```

---

## Discussion

### Challenges Encountered
1. **LinkedIn Anti-Scraping Measures**:
   - **Issue**: LinkedIn detects bots through consistent behavior or automation flags.
   - **Solution**: Implemented random user agents, human-like typing delays (0.1-0.3s per character), and variable page delays (5-15s, increasing after 100 profiles). Disabled automation flags with `--disable-blink-features=AutomationControlled`.

2. **Login Issues**:
   - **Issue**: CAPTCHA or 2FA prompts interrupt automation.
   - **Solution**: Added a manual intervention step: the script pauses, allowing the user to complete verification in the browser, then checks the URL to confirm success.

3. **Data Loading Delays**:
   - **Issue**: Dynamic content (profile cards) loads slowly or fails to appear.
   - **Solution**: Used `WebDriverWait` with 60s timeouts, full-page scrolling, and 3 retries with 5s delays. Saved debug files (`page_source.html`, `debug_screenshot.png`) for manual inspection on failure.

### Key Points Addressed
- **Context Limits**:
  - The LLM prompt is kept concise (<150 tokens) by focusing on essential state data (URL, profile count, cards, next button, action repeats), fitting within the model's context window.
- **Preventing Loops**:
  - `ScraperMemory` tracks repeated actions, stopping after 5 iterations to avoid infinite loops. An override ensures scraping continues until 200 profiles are collected if more pages are available.
- **Optimizations Implemented**:
  - **Concurrency**: `asyncio.gather` runs searches for all keyword-location pairs simultaneously.
  - **Caching**: LLM decisions cached in `actions_cache.json`; profiles stored in `linkedin_profiles.db` to skip duplicates.
  - **Robustness**: Checkpoints every 10 profiles, retries on timeouts, and detailed logging.
- **Future Suggestions**:
  - Use headless mode with proxy rotation to further evade detection.
  - Add rate limiting to mimic human browsing patterns more closely.
  - Extend scraping to include deeper profile data (e.g., job titles, companies) if needed.

---

## Repository Structure
```
linkedin-scraper/
├── scraper.py             # Main script with scraper logic
├── requirements.txt      # List of dependencies
├── .env                  # Credentials (not committed; add to .gitignore)
├── profiles.json         # Final output of scraped profiles
├── linkedin_profiles.db  # SQLite database for profile storage
├── actions_cache.json    # Cached LLM decisions
├── page_source.html      # Debug HTML dump on failure
├── debug_screenshot.png  # Debug screenshot on failure
├── profile_scraper.log   # Detailed logs of scraper actions
├── README.md            # This documentation
```

---

## Additional Notes
- **Video Demonstration**: A video will be provided showing the script execution, browser navigation, LLM decision logs, and the resulting `profiles.json`. Look for it in the repo's releases or a linked URL.
- **Logs & Intermediate Data**: Sample logs and debug files are included after a run. The SQLite database and cache file demonstrate persistence and optimization.

For issues or enhancements, please open a GitHub issue. Happy scraping!
```