'''
🌙 Moon Dev's Web Search Research Agent 🌙
This agent uses AI to generate search queries and then searches the web for trading strategies

Features:
- Uses OpenRouter GLM model to generate optimal search queries
- Uses OpenAI's Chat Completions API with specialized web search models
- Heavy instrumentation with print statements to understand the flow
- Logs all search results to CSV for analysis
- Animated terminal output with colorful displays
- Runs in continuous loop by default

Configuration:
- SLEEP_BETWEEN_SEARCHES: Time in seconds between search cycles (default: 60)
- GLM_MODEL: OpenRouter model to use for query generation (default: llama-3.3-70b)
  Available models:
  * meta-llama/llama-3.3-70b-instruct:free (default) - Reliable English responses
  * z-ai/glm-4.6 - Zhipu AI GLM (may respond in Chinese)
  * deepseek/deepseek-chat - DeepSeek Chat
- OPENAI_WEB_SEARCH_MODEL: OpenAI search model to use (default: gpt-4o-mini-search-preview)
  Available models:
  * gpt-4o-mini-search-preview (default) - Fast & cheap
  * gpt-4o-search-preview - More powerful
  * gpt-5-search-api - Most powerful (GPT-5)

Usage:
- Run continuous loop: python src/agents/websearch_agent.py
- Run single search: python src/agents/websearch_agent.py --once

Created with ❤️ by Moon Dev
'''

import os
import time
import csv
import random
import json
from datetime import datetime
from pathlib import Path
from termcolor import cprint, colored
import pandas as pd
import sys
import shutil
import textwrap
from typing import Dict, List, Optional
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

# 🌙 Moon Dev Configuration 🌙
SLEEP_BETWEEN_SEARCHES = 60  # Seconds to wait between searches (default: 60)
GLM_MODEL = "z-ai/glm-4.6"  # Zhipu AI GLM - Moon Dev's choice!
# Alternative models:
# GLM_MODEL = "meta-llama/llama-3.3-70b-instruct:free"  # Llama 3.3 70B (reliable English)
# GLM_MODEL = "deepseek/deepseek-chat"  # DeepSeek Chat

# OpenAI Web Search Models (for Chat Completions API)
# These are specialized models with built-in web search capabilities
OPENAI_WEB_SEARCH_MODEL = "gpt-4o-mini-search-preview"  # Default: Fast & cheap
# Alternative models to try:
# OPENAI_WEB_SEARCH_MODEL = "gpt-4o-search-preview"  # More powerful
# OPENAI_WEB_SEARCH_MODEL = "gpt-5-search-api"  # Most powerful (GPT-5)

# Define paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data" / "web_search_research"
SEARCH_RESULTS_CSV = DATA_DIR / "search_results.csv"
SEARCH_QUERIES_CSV = DATA_DIR / "search_queries.csv"
STRATEGIES_DIR = DATA_DIR / "strategies"
STRATEGIES_INDEX_CSV = DATA_DIR / "strategies_index.csv"
FINAL_STRATEGIES_DIR = DATA_DIR / "final_strategies"
FINAL_STRATEGIES_INDEX_CSV = DATA_DIR / "final_strategies_index.csv"

# API Keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_KEY")

# Fun emojis for animation
EMOJIS = ["🚀", "💫", "✨", "🌟", "💎", "🔮", "🌙", "⭐", "🌠", "💰", "📈", "🧠", "🔍", "🌐"]
MOON_PHASES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]

# Get terminal width for better formatting
TERM_WIDTH = shutil.get_terminal_size().columns

# Prompt for generating search queries
SEARCH_QUERY_GENERATION_PROMPT = """
⚠️ CRITICAL INSTRUCTION: YOU MUST RESPOND IN ENGLISH ONLY ⚠️
⚠️ DO NOT USE CHINESE, JAPANESE, OR ANY OTHER LANGUAGE ⚠️
⚠️ ENGLISH TEXT ONLY - NO EXCEPTIONS ⚠️

You are Moon Dev's Web Search Query Generator 🌙

Generate ONE creative search query to find unique, backtestable trading strategies on the web.

Be creative and varied! Each query should explore DIFFERENT strategy types:
- Momentum, mean reversion, breakout, arbitrage, statistical arbitrage
- Price action, volume analysis, order flow, market microstructure
- Time-based patterns, seasonal effects, intraday patterns
- Multi-asset strategies, pairs trading, correlation strategies
- Exotic indicators, custom combinations, unconventional approaches

Mix up your approach:
- Sometimes use site filters (reddit, tradingview, forexfactory, blogs)
- Sometimes search for specific file types (PDF, docs with detailed rules)
- Sometimes focus on backtest results and performance metrics
- Sometimes look for academic or quantitative approaches
- Keep queries diverse - don't repeat the same pattern!

⚠️ RESPONSE REQUIREMENTS ⚠️
- MUST be in English language
- ONLY output the search query text
- NO explanations, NO reasoning, NO quotes, NO formatting
- Just the raw English search query

Examples of CORRECT responses (English only):
swing trading momentum strategy rules backtest results
intraday volume profile trading system specific parameters
correlation pairs trading cryptocurrency backtest performance
"""

# Prompt for extracting individual strategies from scraped content
STRATEGY_EXTRACTION_PROMPT = """
⚠️ CRITICAL INSTRUCTION: YOU MUST RESPOND IN ENGLISH ONLY ⚠️
⚠️ DO NOT USE CHINESE, JAPANESE, OR ANY OTHER LANGUAGE ⚠️
⚠️ ENGLISH TEXT ONLY - NO EXCEPTIONS ⚠️

You are Moon Dev's Strategy Extraction Agent 🌙

Your task is to read trading content and extract EVERY distinct strategy idea mentioned.

INSTRUCTIONS:
- Be AGGRESSIVE in splitting out strategies - more is better than less
- Each strategy should be a distinct trading approach or concept
- Extract the core IDEA of each strategy (don't worry about complete entry/exit rules)
- If content mentions multiple strategies, extract ALL of them
- Even if a strategy isn't fully detailed, extract what's there
- Minimum: Extract at least ONE strategy from any trading content

For each strategy you find, extract:
- A clear title/name for the strategy (IN ENGLISH)
- The full description/explanation of the strategy concept (IN ENGLISH)
- Any rules, parameters, indicators, or techniques mentioned (IN ENGLISH)
- Include all relevant details from the original content (IN ENGLISH)

Return your response as valid JSON in this exact format:
{
  "strategies_count": <number>,
  "strategies": [
    {
      "title": "Strategy Name",
      "description": "Full detailed description of the strategy idea, including all relevant information from the source..."
    }
  ]
}

⚠️ RESPONSE REQUIREMENTS ⚠️
- MUST respond in English language only
- Return ONLY valid JSON, no other text
- Be thorough in extracting strategy details
- If you find 1 strategy, return 1. If you find 12, return 12.
- All text in the JSON must be in English
"""

def clear_line():
    """Clear the current line in the terminal"""
    print("\r" + " " * TERM_WIDTH, end="\r", flush=True)

def animate_text(text, color="yellow", bg_color="on_blue", delay=0.03):
    """Animate text with a typewriter effect"""
    clear_line()
    text = ' '.join(text.split())
    result = ""
    for char in text:
        result += char
        print("\r" + " " * len(result), end="\r", flush=True)
        print(f"\r{colored(result, color, bg_color)}", end='', flush=True)
        time.sleep(delay)
    print()

def animate_loading(duration=2, message="Processing", emoji="🌙"):
    """Show a fun loading animation"""
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    colors = ["cyan", "magenta", "blue", "green", "yellow"]
    bg_colors = ["on_blue", "on_magenta", "on_cyan"]

    end_time = time.time() + duration
    i = 0

    while time.time() < end_time:
        frame = frames[i % len(frames)]
        color = colors[(i // 3) % len(colors)]
        bg_color = bg_colors[(i // 6) % len(bg_colors)]

        clear_line()
        print(f"\r{colored(f' {frame} {message} {emoji} ', color, bg_color)}", end="", flush=True)

        time.sleep(0.2)
        i += 1

    clear_line()

def animate_moon_dev():
    """Show a fun Moon Dev animation"""
    moon_dev = [
        "  __  __                         ____                 ",
        " |  \\/  |  ___    ___   _ __   |  _ \\   ___  __   __ ",
        " | |\\/| | / _ \\  / _ \\ | '_ \\  | | | | / _ \\ \\ \\ / / ",
        " | |  | || (_) || (_) || | | | | |_| ||  __/  \\ V /  ",
        " |_|  |_| \\___/  \\___/ |_| |_| |____/  \\___|   \\_/   ",
        "                                                      ",
        "         🌐 WEB SEARCH RESEARCH AGENT 🔍             "
    ]

    colors = ["white", "white", "white", "white", "white", "cyan", "yellow"]
    bg_colors = ["on_blue", "on_cyan", "on_magenta", "on_green", "on_blue", "on_magenta", "on_blue"]

    print()
    for i, line in enumerate(moon_dev):
        color = colors[i % len(colors)]
        bg = bg_colors[i % len(bg_colors)]
        cprint(line, color, bg)
        time.sleep(0.3)

    for _ in range(3):
        emoji = random.choice(EMOJIS)
        position = random.randint(0, min(50, TERM_WIDTH-5))
        print(" " * position + emoji)
        time.sleep(0.3)

def setup_files():
    """Set up the necessary files if they don't exist"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)

    if not SEARCH_QUERIES_CSV.exists():
        cprint(f"📝 Creating search queries CSV at {SEARCH_QUERIES_CSV}", "yellow", "on_blue")
        with open(SEARCH_QUERIES_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'model', 'search_query'])

    if not SEARCH_RESULTS_CSV.exists():
        cprint(f"📊 Creating search results CSV at {SEARCH_RESULTS_CSV}", "white", "on_magenta")
        with open(SEARCH_RESULTS_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'search_query', 'title', 'url', 'snippet', 'content'])

    if not STRATEGIES_INDEX_CSV.exists():
        cprint(f"📚 Creating strategies index CSV at {STRATEGIES_INDEX_CSV}", "cyan", "on_blue")
        with open(STRATEGIES_INDEX_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'search_query', 'url', 'title', 'filename', 'content_length'])

    if not FINAL_STRATEGIES_INDEX_CSV.exists():
        cprint(f"✨ Creating final strategies index CSV at {FINAL_STRATEGIES_INDEX_CSV}", "white", "on_green")
        with open(FINAL_STRATEGIES_INDEX_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'original_file', 'strategy_number', 'title', 'filename', 'source_url'])

def check_duplicate_query(new_query: str, max_recent_checks: int = 10) -> bool:
    """
    Check if the new query is too similar to recent queries
    Returns True if duplicate detected, False otherwise
    """
    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint(" 🔍 CHECKING FOR DUPLICATE QUERIES 🔍 ", "white", "on_cyan")
    print("=" * min(70, TERM_WIDTH))

    cprint(f"\n📝 New Query: {new_query}", "cyan")

    # Check if search_queries.csv exists and has data
    if not SEARCH_QUERIES_CSV.exists():
        cprint("✅ No previous queries found - this is the first one!", "green")
        return False

    # Read recent queries
    try:
        df = pd.read_csv(SEARCH_QUERIES_CSV)
        if df.empty:
            cprint("✅ No previous queries in CSV - this is the first one!", "green")
            return False

        # Get the last N queries
        recent_queries = df['search_query'].tail(max_recent_checks).tolist()

        cprint(f"\n📚 Checking against last {len(recent_queries)} queries:", "yellow")
        for i, query in enumerate(recent_queries, 1):
            print(f"  [{i}] {query[:80]}...")

        # Simple similarity check - normalize and compare
        new_query_lower = new_query.lower().strip()
        new_query_words = set(new_query_lower.split())

        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("🧮 SIMILARITY ANALYSIS:", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))

        for i, old_query in enumerate(recent_queries, 1):
            old_query_lower = old_query.lower().strip()
            old_query_words = set(old_query_lower.split())

            # Calculate Jaccard similarity (intersection over union)
            intersection = new_query_words.intersection(old_query_words)
            union = new_query_words.union(old_query_words)
            similarity = len(intersection) / len(union) if union else 0

            cprint(f"  Query #{i} - Similarity: {similarity:.2%}", "yellow")

            # If more than 70% similar, it's a duplicate
            if similarity > 0.70:
                cprint(f"\n⚠️ DUPLICATE DETECTED! (Similarity: {similarity:.2%})", "white", "on_red")
                cprint(f"  Old Query: {old_query}", "red")
                cprint(f"  New Query: {new_query}", "red")
                return True

        print("─" * min(70, TERM_WIDTH))
        cprint("✅ No duplicates found - query is unique!", "white", "on_green")
        print("=" * min(70, TERM_WIDTH))
        return False

    except Exception as e:
        cprint(f"⚠️ Error checking duplicates: {str(e)}", "yellow")
        cprint("Proceeding with query anyway...", "yellow")
        return False

def fetch_webpage_content(url: str) -> Optional[Dict]:
    """
    Fetch content from a URL using requests and BeautifulSoup
    Returns dict with title and content, or None if failed
    """
    try:
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint(f"🌐 FETCHING: {url[:60]}...", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))

        cprint("⏳ Sending request...", "cyan")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }

        response = requests.get(url, headers=headers, timeout=15)

        cprint(f"✅ Response: {response.status_code}", "green" if response.status_code == 200 else "red")

        if response.status_code != 200:
            cprint(f"❌ Failed to fetch {url}", "red")
            return None

        # Parse with BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()

        # Get title
        title = soup.title.string if soup.title else "No Title"
        title = title.strip()

        # Get text content
        text = soup.get_text()

        # Clean up text
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)

        cprint(f"📄 Title: {title}", "cyan")
        cprint(f"📏 Content Length: {len(text)} characters", "yellow")
        cprint(f"📝 First 200 chars: {text[:200]}...", "white")

        return {
            'url': url,
            'title': title,
            'content': text,
            'length': len(text)
        }

    except requests.exceptions.Timeout:
        cprint(f"⏰ Timeout fetching {url}", "red")
        return None
    except requests.exceptions.RequestException as e:
        cprint(f"❌ Request error for {url}: {str(e)}", "red")
        return None
    except Exception as e:
        cprint(f"❌ Error parsing {url}: {str(e)}", "red")
        return None

def save_strategy_to_file(strategy_data: Dict, search_query: str) -> Optional[str]:
    """
    Save strategy content to a markdown file
    Returns the filename if successful, None otherwise
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create safe filename from URL
        url_hash = hash(strategy_data['url']) % 100000
        filename = f"strategy_{timestamp}_{url_hash:05d}.md"
        filepath = STRATEGIES_DIR / filename

        cprint(f"\n💾 Saving to: {filename}", "white", "on_blue")

        # Create markdown content with metadata
        content = f"""# {strategy_data['title']}

**🌙 Found by Moon Dev's Web Search Agent 🌙**

---

## Metadata

- **Source URL**: {strategy_data['url']}
- **Search Query**: {search_query}
- **Timestamp**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Content Length**: {strategy_data['length']} characters

---

## Strategy Content

{strategy_data['content']}

---

*Generated by Moon Dev's Web Search Research Agent*
*Ready for backtesting with RBI Agent*
"""

        # Write to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        cprint(f"✅ Saved! ({strategy_data['length']} chars)", "white", "on_green")

        # Log to strategies index
        with open(STRATEGIES_INDEX_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                search_query,
                strategy_data['url'],
                strategy_data['title'],
                filename,
                strategy_data['length']
            ])

        return filename

    except Exception as e:
        cprint(f"❌ Error saving strategy: {str(e)}", "red")
        return None

def process_and_save_strategies(citations: List[Dict], search_query: str):
    """
    Process all URLs from search results, fetch content, and save to files
    Returns list of saved files with their URLs for extraction
    """
    if not citations:
        cprint("\n⚠️ No URLs to process", "yellow")
        return []

    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint(" 🌐 FETCHING AND SAVING STRATEGIES 🌐 ", "white", "on_magenta")
    print("=" * min(70, TERM_WIDTH))

    cprint(f"\n📊 Processing {len(citations)} URLs...", "cyan")
    time.sleep(1)

    successful = 0
    failed = 0
    saved_files = []  # Track saved files for extraction

    for i, citation in enumerate(citations, 1):
        url = citation.get('url', '')

        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint(f"📄 STRATEGY {i}/{len(citations)}", "white", "on_blue")
        print("=" * min(70, TERM_WIDTH))

        # Fetch webpage content
        strategy_data = fetch_webpage_content(url)

        if not strategy_data:
            cprint(f"❌ Failed to fetch strategy {i}", "red")
            failed += 1
            continue

        # Save to markdown file
        filename = save_strategy_to_file(strategy_data, search_query)

        if filename:
            successful += 1
            cprint(f"✅ Strategy {i} saved successfully!", "green")
            # Track saved file for extraction
            saved_files.append({
                'filename': filename,
                'url': url,
                'filepath': STRATEGIES_DIR / filename
            })
        else:
            failed += 1
            cprint(f"❌ Failed to save strategy {i}", "red")

        # Small delay between requests to be polite
        if i < len(citations):
            time.sleep(2)

    # Summary
    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint("📊 PROCESSING COMPLETE!", "white", "on_green")
    print("=" * min(70, TERM_WIDTH))
    cprint(f"  ✅ Successful: {successful}", "green")
    cprint(f"  ❌ Failed: {failed}", "red")
    cprint(f"  📁 Files saved to: {STRATEGIES_DIR}", "cyan")
    print("=" * min(70, TERM_WIDTH))

    return saved_files

def generate_search_query_with_glm():
    """Generate a search query using OpenRouter's GLM model"""
    try:
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint(" 🧙‍♂️ GENERATING SEARCH QUERY 🧙‍♂️ ", "white", "on_magenta")
        print("=" * min(70, TERM_WIDTH))

        cprint("\n🔮 Connecting to OpenRouter API...", "cyan")
        time.sleep(0.5)

        # Print API configuration
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📡 API CONFIGURATION:", "white", "on_blue")
        cprint("  Provider: OpenRouter", "yellow")
        cprint(f"  Model: {GLM_MODEL}", "yellow")
        cprint(f"  API Key: {'✓ Configured' if OPENROUTER_API_KEY else '✗ Missing'}", "green" if OPENROUTER_API_KEY else "red")
        print("─" * min(70, TERM_WIDTH))

        if not OPENROUTER_API_KEY:
            cprint("❌ OPENROUTER_API_KEY not found in environment!", "white", "on_red")
            return None

        # Print the prompt we're sending
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📤 PROMPT BEING SENT:", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        wrapped_prompt = textwrap.fill(SEARCH_QUERY_GENERATION_PROMPT, width=min(70, TERM_WIDTH))
        cprint(wrapped_prompt, "cyan")
        print("─" * min(70, TERM_WIDTH))

        animate_loading(2, "Sending prompt", "🔮")

        # Prepare the API request
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/moon-dev-ai",
            "X-Title": "Moon Dev Web Search Research Agent"
        }

        payload = {
            "model": GLM_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": SEARCH_QUERY_GENERATION_PROMPT
                },
                {
                    "role": "user",
                    "content": "Generate one highly effective search query to find a unique trading strategy."
                }
            ],
            "temperature": 0.7,
            "max_tokens": 200
        }

        # Print the request details
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📋 REQUEST DETAILS:", "white", "on_magenta")
        cprint(f"  URL: {url}", "yellow")
        cprint(f"  Model: {payload['model']}", "yellow")
        cprint(f"  Temperature: {payload['temperature']}", "yellow")
        cprint(f"  Max Tokens: {payload['max_tokens']}", "yellow")
        print("─" * min(70, TERM_WIDTH))

        cprint("\n⏳ Waiting for AI response...", "white", "on_blue")
        animate_loading(1, "AI is thinking", "🧠")

        # Make the API request
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        # Print response status
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📥 API RESPONSE STATUS:", "white", "on_green" if response.status_code == 200 else "on_red")
        cprint(f"  Status Code: {response.status_code}", "green" if response.status_code == 200 else "red")
        cprint(f"  Response Time: {response.elapsed.total_seconds():.2f}s", "yellow")
        print("─" * min(70, TERM_WIDTH))

        if response.status_code != 200:
            cprint(f"❌ API Error: {response.status_code}", "white", "on_red")
            cprint(f"Response: {response.text}", "red")
            return None

        # Parse the response
        response_json = response.json()

        # Print the full raw response
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📦 RAW API RESPONSE (JSON):", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        print(json.dumps(response_json, indent=2))
        print("─" * min(70, TERM_WIDTH))

        # Extract the search query
        search_query = response_json['choices'][0]['message']['content'].strip()

        # 🌙 Moon Dev: Fallback to reasoning field if content is empty (GLM models do this)
        if not search_query or len(search_query) < 5:
            reasoning = response_json['choices'][0]['message'].get('reasoning', '').strip()
            if reasoning:
                cprint("\n🔄 Content field empty, extracting from reasoning field...", "yellow")
                # Try to extract a search query from the reasoning (look for quoted text or last line)
                import re
                # Look for text in quotes
                quoted = re.findall(r'"([^"]+)"', reasoning)
                if quoted:
                    search_query = quoted[-1]  # Use last quoted string
                else:
                    # Use the last substantial line
                    lines = [line.strip() for line in reasoning.split('\n') if line.strip()]
                    search_query = lines[-1] if lines else ""

        # Clean up the search query (remove any thinking tags or extra formatting)
        if "<think>" in search_query and "</think>" in search_query:
            cprint("\n🧠 Detected thinking tags, extracting query...", "yellow")
            import re
            # Extract content after thinking tags
            match = re.search(r'</think>\s*(.+)', search_query, re.DOTALL)
            if match:
                search_query = match.group(1).strip()

        # Remove any markdown formatting
        search_query = search_query.replace('```', '').replace('**', '').replace('"', '')
        search_query = ' '.join(search_query.split())  # Clean whitespace

        # Check if search query is empty or just whitespace
        if not search_query or len(search_query) < 5:
            cprint("\n⚠️ AI returned empty or invalid response!", "white", "on_red")
            cprint("This can happen if the model responds in non-English or with reasoning only.", "yellow")
            cprint("Try switching to a different model in the configuration.", "cyan")
            return None

        # Display the generated search query
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint("✨ GENERATED SEARCH QUERY:", "white", "on_green")
        print("=" * min(70, TERM_WIDTH))
        animate_text(search_query, "yellow", "on_blue", delay=0.02)
        print("=" * min(70, TERM_WIDTH))

        # Add some celebratory emojis
        for _ in range(3):
            position = random.randint(0, min(40, TERM_WIDTH-5))
            emoji = random.choice(["🔍", "🌐", "✨", "💫"])
            print(" " * position + emoji)
            time.sleep(0.2)

        return search_query

    except requests.exceptions.Timeout:
        cprint("❌ Request timed out after 30 seconds", "white", "on_red")
        return None
    except requests.exceptions.RequestException as e:
        cprint(f"❌ Request error: {str(e)}", "white", "on_red")
        return None
    except Exception as e:
        cprint(f"❌ Error generating search query: {str(e)}", "white", "on_red")
        import traceback
        cprint(traceback.format_exc(), "red")
        return None

def search_web_with_openai(search_query: str):
    """Search the web using OpenAI's Chat Completions API with specialized search models"""
    try:
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint(" 🌐 SEARCHING THE WEB WITH OPENAI 🔍 ", "white", "on_magenta")
        print("=" * min(70, TERM_WIDTH))

        cprint("\n🔮 Connecting to OpenAI API...", "cyan")
        time.sleep(0.5)

        # Print API configuration
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📡 API CONFIGURATION:", "white", "on_blue")
        cprint("  Provider: OpenAI", "yellow")
        cprint("  Endpoint: /v1/chat/completions", "yellow")
        cprint(f"  Model: {OPENAI_WEB_SEARCH_MODEL}", "yellow")
        cprint(f"  API Key: {'✓ Configured' if OPENAI_API_KEY else '✗ Missing'}", "green" if OPENAI_API_KEY else "red")
        print("─" * min(70, TERM_WIDTH))

        if not OPENAI_API_KEY:
            cprint("❌ OPENAI_KEY not found in environment!", "white", "on_red")
            return None

        # Print the search query
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("🔍 SEARCH QUERY:", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        cprint(f"  {search_query}", "cyan")
        print("─" * min(70, TERM_WIDTH))

        animate_loading(2, "Preparing web search request", "🌐")

        # Prepare the API request for OpenAI Chat Completions API with search model
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        # Specialized search models have built-in web search - just use normal messages format
        user_message = f"Search the web for: {search_query}. Find detailed trading strategies with specific rules, parameters, and backtesting information. Provide comprehensive results including URLs and relevant content."

        payload = {
            "model": OPENAI_WEB_SEARCH_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        }

        # Print the request details
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📋 REQUEST DETAILS:", "white", "on_magenta")
        cprint(f"  URL: {url}", "yellow")
        cprint(f"  Model: {payload['model']}", "yellow")
        print("─" * min(70, TERM_WIDTH))

        # Print the message being sent
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📤 MESSAGE SENT TO OPENAI:", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        wrapped_message = textwrap.fill(user_message, width=min(70, TERM_WIDTH))
        cprint(wrapped_message, "cyan")
        print("─" * min(70, TERM_WIDTH))

        cprint("\n⏳ Executing web search (this may take 10-30 seconds)...", "white", "on_blue")
        animate_loading(3, "Searching the web", "🔍")

        # Make the API request
        response = requests.post(url, headers=headers, json=payload, timeout=60)

        # Print response status
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📥 API RESPONSE STATUS:", "white", "on_green" if response.status_code == 200 else "on_red")
        cprint(f"  Status Code: {response.status_code}", "green" if response.status_code == 200 else "red")
        cprint(f"  Response Time: {response.elapsed.total_seconds():.2f}s", "yellow")
        print("─" * min(70, TERM_WIDTH))

        if response.status_code != 200:
            cprint(f"❌ API Error: {response.status_code}", "white", "on_red")
            cprint(f"Response: {response.text}", "red")
            return None

        # Parse the response
        response_json = response.json()

        # Print the full raw response
        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📦 RAW WEB SEARCH RESPONSE (JSON):", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        print(json.dumps(response_json, indent=2))
        print("─" * min(70, TERM_WIDTH))

        # Extract search results from Chat Completions API format
        results = []
        content = ""

        if 'choices' in response_json and len(response_json['choices']) > 0:
            message = response_json['choices'][0].get('message', {})
            content = message.get('content', '')

            if content:
                results.append({
                    'type': 'text',
                    'content': content
                })

        # Display the search response
        if content:
            print("\n" + "─" * min(70, TERM_WIDTH))
            cprint("📚 WEB SEARCH RESPONSE:", "white", "on_green")
            print("─" * min(70, TERM_WIDTH))
            # Wrap long content for better display
            wrapped_content = textwrap.fill(content, width=min(70, TERM_WIDTH))
            cprint(wrapped_content, "cyan")
            print("─" * min(70, TERM_WIDTH))

        # Extract any URLs from the content
        citations = []
        import re
        urls = re.findall(r'https?://[^\s]+', content)
        for i, url in enumerate(urls, 1):
            citations.append({
                'title': f'Source {i}',
                'url': url.rstrip('.,;)'),  # Clean trailing punctuation
                'snippet': ''
            })

        if citations:
            print("\n" + "─" * min(70, TERM_WIDTH))
            cprint("🔗 URLS FOUND IN RESPONSE:", "white", "on_green")
            print("─" * min(70, TERM_WIDTH))
            for i, citation in enumerate(citations, 1):
                cprint(f"  [{i}] {citation.get('url', 'No URL')}", "cyan")
            print("─" * min(70, TERM_WIDTH))

        # Display results summary
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint("✅ WEB SEARCH COMPLETED!", "white", "on_green")
        print("=" * min(70, TERM_WIDTH))
        cprint(f"  Generated {len(results)} response(s)", "yellow")
        cprint(f"  Found {len(citations)} URL(s)", "yellow")
        print("=" * min(70, TERM_WIDTH))

        return {
            'results': results,
            'citations': citations,
            'raw_response': response_json
        }

    except requests.exceptions.Timeout:
        cprint("❌ Request timed out after 60 seconds", "white", "on_red")
        return None
    except requests.exceptions.RequestException as e:
        cprint(f"❌ Request error: {str(e)}", "white", "on_red")
        return None
    except Exception as e:
        cprint(f"❌ Error searching web: {str(e)}", "white", "on_red")
        import traceback
        cprint(traceback.format_exc(), "red")
        return None

def log_search_query(search_query: str):
    """Log the search query to CSV"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cprint("\n💾 Logging search query to CSV...", "white", "on_blue")

    with open(SEARCH_QUERIES_CSV, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, GLM_MODEL, search_query])

    cprint("✅ Search query logged!", "white", "on_green")

def log_search_results(search_query: str, search_data: Dict):
    """Log search results to CSV"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cprint("\n💾 Logging search results to CSV...", "white", "on_blue")

    # Log citations
    if search_data and 'citations' in search_data:
        for citation in search_data['citations']:
            with open(SEARCH_RESULTS_CSV, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    search_query,
                    citation.get('title', ''),
                    citation.get('url', ''),
                    citation.get('snippet', ''),
                    ''  # Full content would go here if available
                ])

    # Log text results
    if search_data and 'results' in search_data:
        for result in search_data['results']:
            if result['type'] == 'text':
                with open(SEARCH_RESULTS_CSV, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp,
                        search_query,
                        'Text Result',
                        '',
                        result['content'][:500],  # First 500 chars as snippet
                        result['content']
                    ])

    cprint("✅ Search results logged!", "white", "on_green")

def extract_strategies_with_glm(md_filepath: Path, source_url: str = "") -> Optional[Dict]:
    """
    Use GLM to extract individual strategies from a raw markdown file
    Returns JSON with strategies array
    """
    try:
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint(" 🧠 EXTRACTING STRATEGIES WITH GLM 🧠 ", "white", "on_magenta")
        print("=" * min(70, TERM_WIDTH))

        cprint(f"\n📄 Reading file: {md_filepath.name}", "cyan")

        # Read the markdown file
        with open(md_filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        cprint(f"📏 File length: {len(content)} characters", "yellow")

        # Prepare API request
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/moon-dev-ai",
            "X-Title": "Moon Dev Strategy Extraction Agent"
        }

        payload = {
            "model": GLM_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": STRATEGY_EXTRACTION_PROMPT
                },
                {
                    "role": "user",
                    "content": f"Extract all trading strategies from this content:\n\n{content}"
                }
            ],
            "temperature": 0.5,
            "max_tokens": 4000
        }

        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📡 API CONFIGURATION:", "white", "on_blue")
        cprint(f"  Model: {GLM_MODEL}", "yellow")
        cprint("  Temperature: 0.5", "yellow")
        cprint("  Max Tokens: 4000", "yellow")
        print("─" * min(70, TERM_WIDTH))

        cprint("\n⏳ Sending to GLM for extraction...", "cyan")
        animate_loading(2, "Extracting strategies", "🧠")

        # Make API request
        response = requests.post(url, headers=headers, json=payload, timeout=60)

        cprint(f"\n✅ Response: {response.status_code} ({response.elapsed.total_seconds():.2f}s)",
               "green" if response.status_code == 200 else "red")

        if response.status_code != 200:
            cprint(f"❌ API Error: {response.text}", "red")
            return None

        # Parse response
        response_json = response.json()
        raw_content = response_json['choices'][0]['message']['content'].strip()

        # 🌙 Moon Dev: Fallback to reasoning field if content is empty (GLM models do this)
        if not raw_content or len(raw_content) < 10:
            reasoning = response_json['choices'][0]['message'].get('reasoning', '').strip()
            if reasoning:
                cprint("\n🔄 Content field empty, using reasoning field instead...", "yellow")
                raw_content = reasoning

        print("\n" + "─" * min(70, TERM_WIDTH))
        cprint("📦 RAW GLM RESPONSE:", "white", "on_blue")
        print("─" * min(70, TERM_WIDTH))
        print(raw_content[:500] + "..." if len(raw_content) > 500 else raw_content)
        print("─" * min(70, TERM_WIDTH))

        # Try to parse JSON from the response
        # Sometimes GLM wraps JSON in markdown code blocks
        json_str = raw_content
        if "```json" in raw_content:
            json_str = raw_content.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_content:
            json_str = raw_content.split("```")[1].split("```")[0].strip()

        strategies_data = json.loads(json_str)

        # Validate structure
        if 'strategies' not in strategies_data:
            cprint("⚠️ Response missing 'strategies' key", "yellow")
            return None

        count = len(strategies_data['strategies'])
        strategies_data['strategies_count'] = count

        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint(f"✨ EXTRACTED {count} STRATEGIES! ✨", "white", "on_green")
        print("=" * min(70, TERM_WIDTH))

        for i, strat in enumerate(strategies_data['strategies'], 1):
            cprint(f"  [{i}] {strat.get('title', 'Untitled')}", "cyan")

        return strategies_data

    except json.JSONDecodeError as e:
        cprint(f"❌ Failed to parse JSON: {str(e)}", "red")
        cprint(f"Raw response: {raw_content[:300]}...", "red")
        return None
    except Exception as e:
        cprint(f"❌ Error extracting strategies: {str(e)}", "red")
        import traceback
        cprint(traceback.format_exc(), "red")
        return None

def save_extracted_strategy(strategy_data: Dict, original_filename: str, strategy_number: int, source_url: str = "") -> Optional[str]:
    """
    Save an individual extracted strategy to a markdown file in final_strategies/
    Returns the filename if successful
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create slug from title
        title = strategy_data.get('title', f'Strategy {strategy_number}')
        slug = "".join(c if c.isalnum() else "_" for c in title.lower())[:40]

        filename = f"extracted_{timestamp}_{strategy_number:03d}_{slug}.md"
        filepath = FINAL_STRATEGIES_DIR / filename

        cprint(f"\n💾 Saving: {filename}", "white", "on_blue")

        # Create markdown content - just strategy name and description
        content = f"""# {title}

{strategy_data.get('description', 'No description provided')}
"""

        # Write to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        cprint("✅ Saved to final_strategies/", "green")

        # Log to index
        with open(FINAL_STRATEGIES_INDEX_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                original_filename,
                strategy_number,
                title,
                filename,
                source_url
            ])

        return filename

    except Exception as e:
        cprint(f"❌ Error saving strategy: {str(e)}", "red")
        return None

def process_raw_file_for_extraction(raw_filepath: Path, source_url: str = ""):
    """
    Process a raw scraped markdown file to extract individual strategies
    """
    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint(" 🔬 PROCESSING FILE FOR STRATEGY EXTRACTION 🔬 ", "white", "on_cyan")
    print("=" * min(70, TERM_WIDTH))

    cprint(f"\n📂 File: {raw_filepath.name}", "yellow")
    cprint(f"🌐 Source: {source_url[:60]}...", "cyan" if source_url else "yellow")

    # Extract strategies with GLM
    strategies_data = extract_strategies_with_glm(raw_filepath, source_url)

    if not strategies_data:
        cprint("\n❌ Failed to extract strategies from file", "red")
        return False

    strategies = strategies_data.get('strategies', [])

    if not strategies:
        cprint("\n⚠️ No strategies found in file", "yellow")
        return False

    # Save each strategy
    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint(f" 💾 SAVING {len(strategies)} STRATEGIES 💾 ", "white", "on_green")
    print("=" * min(70, TERM_WIDTH))

    successful = 0
    failed = 0

    for i, strategy in enumerate(strategies, 1):
        filename = save_extracted_strategy(
            strategy,
            raw_filepath.name,
            i,
            source_url
        )

        if filename:
            successful += 1
        else:
            failed += 1

    # Summary
    print("\n" + "=" * min(70, TERM_WIDTH))
    cprint("📊 EXTRACTION COMPLETE!", "white", "on_green")
    print("=" * min(70, TERM_WIDTH))
    cprint(f"  ✅ Successfully saved: {successful}", "green")
    cprint(f"  ❌ Failed: {failed}", "red")
    cprint(f"  📁 Location: {FINAL_STRATEGIES_DIR}", "cyan")
    print("=" * min(70, TERM_WIDTH))

    return True

def run_search_cycle():
    """Run one complete search cycle: generate query -> check duplicates -> search web -> fetch content -> save strategies"""
    setup_files()

    try:
        # Step 1: Generate search query with GLM
        search_query = generate_search_query_with_glm()

        if not search_query:
            cprint("\n❌ Failed to generate search query. Aborting cycle.", "white", "on_red")
            return False

        # Step 1.5: Check for duplicate queries
        is_duplicate = check_duplicate_query(search_query)

        if is_duplicate:
            cprint("\n⚠️ Duplicate query detected - trying to regenerate...", "white", "on_yellow")
            time.sleep(2)

            # Try once more to generate a different query
            search_query = generate_search_query_with_glm()

            if not search_query:
                cprint("\n❌ Failed to generate alternate search query. Aborting cycle.", "white", "on_red")
                return False

            # Check again
            is_duplicate_again = check_duplicate_query(search_query)

            if is_duplicate_again:
                cprint("\n⚠️ Still a duplicate after retry. Skipping this cycle.", "white", "on_yellow")
                return False
            else:
                cprint("\n✅ New unique query generated on retry!", "white", "on_green")

        # Log the search query
        log_search_query(search_query)

        # Step 2: Search the web with OpenAI
        search_results = search_web_with_openai(search_query)

        if not search_results:
            cprint("\n❌ Failed to search the web. Aborting cycle.", "white", "on_red")
            return False

        # Log the search results
        log_search_results(search_query, search_results)

        # Step 3: Fetch webpage content and save strategies to markdown files
        citations = search_results.get('citations', [])

        saved_files = []
        if citations:
            saved_files = process_and_save_strategies(citations, search_query)
        else:
            cprint("\n⚠️ No URLs found in search results to fetch", "yellow")

        # Step 4: Extract individual strategies from saved files with GLM
        if saved_files:
            print("\n" + "=" * min(70, TERM_WIDTH))
            cprint(f" 🧠 EXTRACTING STRATEGIES FROM {len(saved_files)} FILES 🧠 ", "white", "on_cyan")
            print("=" * min(70, TERM_WIDTH))

            for file_data in saved_files:
                process_raw_file_for_extraction(
                    file_data['filepath'],
                    file_data['url']
                )
                # Small delay between extractions
                time.sleep(2)

        # Success animation
        print("\n" + "=" * min(70, TERM_WIDTH))
        cprint("🎉 SEARCH CYCLE COMPLETED SUCCESSFULLY! 🎉", "white", "on_green")
        print("=" * min(70, TERM_WIDTH))

        # Show some celebratory emojis
        for _ in range(5):
            position = random.randint(0, min(50, TERM_WIDTH-5))
            emoji = random.choice(EMOJIS)
            print(" " * position + emoji)
            time.sleep(0.2)

        return True

    except KeyboardInterrupt:
        cprint("\n👋 Search interrupted by user", "white", "on_yellow")
        return False
    except Exception as e:
        cprint(f"\n❌ ERROR DURING SEARCH CYCLE: {str(e)}", "white", "on_red")
        import traceback
        cprint(traceback.format_exc(), "red")
        return False

def run_continuous_search():
    """Run continuous search cycles with SLEEP_BETWEEN_SEARCHES interval"""
    setup_files()

    # Fun startup animation
    animate_moon_dev()
    time.sleep(0.5)
    cprint("\n🌟 MOON DEV'S WEB SEARCH RESEARCH AGENT - CONTINUOUS MODE 🌟", "white", "on_magenta")
    time.sleep(0.5)
    cprint(f"🔄 Will search every {SLEEP_BETWEEN_SEARCHES} seconds", "cyan")
    time.sleep(1)

    cycle_count = 0

    try:
        while True:
            cycle_count += 1

            print("\n" + "=" * min(70, TERM_WIDTH))
            cprint(f"🔄 STARTING SEARCH CYCLE #{cycle_count}", "white", "on_blue")
            print("=" * min(70, TERM_WIDTH))

            # Run search cycle
            success = run_search_cycle()

            if success:
                cprint(f"\n✅ Cycle #{cycle_count} completed successfully!", "white", "on_green")
            else:
                cprint(f"\n⚠️ Cycle #{cycle_count} had errors", "white", "on_yellow")

            # Cooldown animation
            cprint(f"\n⏱️ COOLDOWN PERIOD ({SLEEP_BETWEEN_SEARCHES}s)", "white", "on_blue")

            moon_emojis = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
            bg_colors = ["on_blue", "on_magenta", "on_cyan", "on_green"]

            for i in range(SLEEP_BETWEEN_SEARCHES):
                emoji = moon_emojis[i % len(moon_emojis)]
                bg = bg_colors[i % len(bg_colors)]
                remaining = SLEEP_BETWEEN_SEARCHES - i
                clear_line()
                print(f"\r{colored(f' {emoji} Next search in: {remaining} seconds ', 'white', bg)}", end="", flush=True)
                time.sleep(1)

            clear_line()
            print()

    except KeyboardInterrupt:
        cprint("\n👋 MOON DEV'S WEB SEARCH RESEARCH AGENT SHUTTING DOWN...", "white", "on_yellow")
        cprint(f"Completed {cycle_count} search cycles", "cyan")
        cprint("\n🌙 Thank you for using Moon Dev's Web Search Research Agent! 🌙", "white", "on_magenta")
    except Exception as e:
        cprint(f"\n❌ FATAL ERROR: {str(e)}", "white", "on_red")
        import traceback
        cprint(traceback.format_exc(), "red")

def main():
    """Main function to run the web search research agent"""
    # 🌙 Moon Dev: Run continuous search loop by default
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Run single search cycle if --once flag provided
        run_search_cycle()
    else:
        # Default: Run continuous loop with SLEEP_BETWEEN_SEARCHES interval
        run_continuous_search()

if __name__ == "__main__":
    main()
