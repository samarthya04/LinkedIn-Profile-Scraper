# 🚀 LinkedIn Profile Scraper

A **robust, intelligent scraper** that uses **Selenium**, **LLMs (via OpenRouter)**, and **SQLite** to collect at least 200 LinkedIn profiles based on custom **keywords** and **locations**. Designed to be efficient, resilient to anti-scraping mechanisms, and capable of smart decision-making through AI.

---

## 🧠 High-Level Design

### 💡 Overview
This tool automates LinkedIn profile scraping through:

- **Web automation** with Selenium  
- **LLM-based navigation** for adaptive control  
- **SQLite persistence** for caching and deduplication  
- **Concurrent execution** for faster data collection  

### 🛠 Tools & Libraries
| Component | Purpose |
|----------|---------|
| `Selenium` | Automates browser tasks like login and page traversal |
| `OpenRouter LLM` | Guides scraper decisions using page state |
| `SQLite` | Stores profiles and prevents duplicates |
| `asyncio` | Concurrent scraping for multiple search queries |
| `python-dotenv` | Securely manages credentials via `.env` |

---

## 🔁 Logic Flow

1. **Initialization**  
   - Loads credentials from `.env`  
   - Prepares `linkedin_profiles.db` for storage

2. **Login**  
   - Automates LinkedIn login  
   - Waits for manual CAPTCHA/2FA completion (if prompted)

3. **Search Query Execution**  
   - Executes searches for keyword-location pairs (e.g., `"Data Scientist" in "New Delhi"`)

4. **AI Decision Engine**  
   - The LLM analyzes the current page (profile count, presence of next button, etc.)  
   - Makes decisions: **Scrape / Next Page / Stop**  
   - Stores decisions in `actions_cache.json`

5. **Profile Scraping**  
   - Extracts name, URL, and timestamp  
   - Saves profiles in SQLite and exports them to `profiles.json`

6. **Loop & Control**  
   - Loops until **200 unique profiles** are collected  
   - Avoids infinite loops by tracking repeated LLM actions

---

## 🔐 Anti-Detection Features

- Random **User-Agents** per session  
- Human-like typing (`0.1s–0.3s` per keystroke)  
- Variable wait times between actions (`5s–15s`)  
- Chrome flag `--disable-blink-features=AutomationControlled` to reduce bot detection  

---

## 📦 Dependencies

In `requirements.txt`:

- `selenium`  
- `webdriver-manager`  
- `openai`  
- `python-dotenv`  
- `sqlite3` (built-in)

---

## ⚙️ Setup & Usage

### 🔧 Installation

```bash
git clone https://github.com/yourusername/linkedin-scraper.git
cd linkedin-scraper
pip install -r requirements.txt
```

### 🔑 Configure Environment

Create a `.env` file in the root:

```env
LINKEDIN_EMAIL=your_email@example.com
LINKEDIN_PASSWORD=your_password
OPENROUTER_API_KEY=your_openrouter_api_key
```

> 🔐 Get your OpenRouter API key from [openrouter.ai](https://openrouter.ai)

### ✅ Running the Scraper

```bash
python scraper.py
```

- Chrome will launch (non-headless by default)
- Complete any manual CAPTCHA/2FA and press `Enter` in the terminal
- Scraping begins automatically

### 📤 Output

- `profiles.json`: Final scraped profiles  
- `linkedin_profiles.db`: Persistent storage  
- `profile_scraper.log`: Activity logs  

---

## ✅ Sample Console Output

```bash
2025-04-07 10:00:00 - INFO - Loaded credentials - Username: your_email@example.com
2025-04-07 10:00:05 - INFO - Login successful
2025-04-07 10:00:10 - INFO - Searching for: Data Scientist New Delhi
2025-04-07 10:00:20 - INFO - LLM decided: 2 - Reason: Profiles available on current page
2025-04-07 10:00:30 - INFO - Total profiles collected: 200
```

---

## 📄 Sample Output File (`profiles.json`)

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

## 🧩 Challenges & Solutions

| Challenge | Solution |
|----------|----------|
| LinkedIn anti-bot systems | Random user agents, human typing delays, and stealth flags |
| CAPTCHA & 2FA interruptions | Manual intervention supported mid-script |
| Dynamic content not loading | `WebDriverWait`, full-page scroll, retry mechanism |
| Infinite loops in navigation | Loop breaker using `ScraperMemory` with override options |

---

## 📌 Optimization Highlights

- **Asynchronous Searches**: Concurrent keyword-location executions via `asyncio.gather`
- **LLM Context Control**: Efficient, <150-token prompts include only necessary state data
- **Caching**: LLM decisions saved in `actions_cache.json`; profiles deduplicated in SQLite
- **Checkpoints**: Autosave every 10 profiles + retries on failures
- **Debug Files**: `page_source.html` and `debug_screenshot.png` saved on exceptions

---

## 🔮 Future Improvements

- Enable **headless mode with proxy rotation**  
- Add **rate limiting** to simulate natural user flow  
- Extend scraping depth: job titles, companies, summaries  
- Add CLI args for dynamic keyword-location inputs  

---

## 📁 Project Structure

```
linkedin-scraper/
├── scraper.py             # Main scraping logic
├── requirements.txt       # Python dependencies
├── .env                   # Credentials (not committed)
├── profiles.json          # Output file with profile data
├── linkedin_profiles.db   # SQLite storage of profiles
├── actions_cache.json     # Cached LLM decisions
├── page_source.html       # HTML dump on error
├── debug_screenshot.png   # Screenshot on error
├── profile_scraper.log    # Logging information
├── README.md              # This documentation
```

---

## 🎥 Video Demo & Extras

- A demo video (in progress) will be added in the repo or Releases tab.
- Sample debug files and logs from actual runs are included to help troubleshoot and test.

---

## 🗣 Feedback & Contributions

Have suggestions or found a bug?  
Open a GitHub [Issue](https://github.com/yourusername/linkedin-scraper/issues) or create a [Pull Request](https://github.com/yourusername/linkedin-scraper/pulls).

---

**Happy Scraping! 🚀**
