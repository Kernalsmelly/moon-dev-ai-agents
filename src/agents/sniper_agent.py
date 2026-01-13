'''
🌙 Moon Dev's Token Scanner - Built with love by Moon Dev 🚀
Watches for new Solana token launches and displays them with fun animations!


- it would be dope to have agents analuzing the launches of each day continously tho, so its like looking at the top 20 to see if they change
'''

import os
import sys
from pathlib import Path

# 🌙 Moon Dev: Calculate project root dynamically
PROJECT_ROOT = Path(__file__).parent.parent.parent
GITHUB_DIR = PROJECT_ROOT.parent  # Parent folder containing all repos

# Add src directory to Python path
src_path = str(Path(__file__).parent.parent)
if src_path not in sys.path:
    sys.path.append(src_path)

import time
import random
import requests
from termcolor import colored
from src.nice_funcs import is_momentum_reject, send_discord_message
import pandas as pd
from pathlib import Path
import logging
from rich.console import Console
from playsound import playsound

# Suppress INFO logs
logging.getLogger().setLevel(logging.WARNING)

# Initialize Rich console
console = Console()

# Constants
PAST_TOKENS_TO_SHOW = 40  # Number of past token launches to display
CHECK_INTERVAL = 10  # Seconds between each check for new launches
DISPLAY_DELAY = 0.5  # Seconds between displaying each token
ANIMATION_DURATION = 10  # Seconds to show attention-grabbing animation
AUTO_OPEN_BROWSER = True  # Set to True to automatically open new tokens in browser
USE_DEXSCREENER = True  # Set to True to use DexScreener instead of Birdeye
EXCLUDE_PATTERNS = ['So11111111111111111111111111111111111111112']  # Exclude the SOLE token pattern
BASE_URL = "http://api.moondev.com:8000"
SOUND_ENABLED = True  # Set to True to enable sound effects, False to disable them
DATA_FOLDER = Path(__file__).parent.parent / "data" / "sniper_agent"  # Folder for token data
# Freshness: only treat tokens younger than this (seconds) as "fresh" in the live monitor
MAX_FRESH_AGE_SECONDS = 60

# Animation sequences
ATTENTION_EMOJIS = [
    "🚨", "💫", "⚡", "🔥", "✨", "💥",  # Attention-grabbing
    "🎯", "🎪", "🎢", "🎡", "🎠",       # Fun & festive
    "🌈", "🦄", "🌟", "💎", "🚀"        # Moon Dev specials
]

# Background colors for token announcements
BACKGROUND_COLORS = [
    'on_blue', 'on_magenta', 'on_cyan', 'on_red', 'on_green', 
    'on_yellow', 'on_grey', 'on_white'
]

LAUNCH_EMOJIS = [
    "🚀", "💎", "🌙", "⭐", "🔥", "💫", "✨", "🌟", "💰", "🎯",  # Classic crypto
    "🎆", "🌠", "⚡", "🌈", "🎨", "🎪", "🎭", "🎡", "🎢", "🎠",  # Fun & festive
    "🦁", "🐉", "🦊", "🦄", "🐋", "🦈", "🦅", "🦚", "🦜", "🦋",  # Cool animals
    "🏆", "🎮", "🎲", "🎱", "🎳", "🎪", "🎨", "🎭", "🎪", "🎢",  # Games & fun
    "🌍", "🌎", "🌏", "🌕", "🌖", "🌗", "🌘", "🌑", "🌒", "🌓",  # Space & moon phases
    "💥", "🌪️", "⚡", "☄️", "🌠", "🎇", "🎆", "✨", "💫", "⭐",  # Energy & explosions
]

# Sound effects paths - 🌙 Moon Dev: External repo (sibling to this repo)
SOUND_EFFECTS = [
    str(GITHUB_DIR / "Untitled" / "sounds" / "pownew.MP3"),
    str(GITHUB_DIR / "Untitled" / "sounds" / "Shining.wav"),
    str(GITHUB_DIR / "Untitled" / "sounds" / "final_fant1.MP3"),
    str(GITHUB_DIR / "Untitled" / "sounds" / "final_fant2.MP3")
]

class TokenScanner:
    def __init__(self):
        """🌙 Moon Dev's Token Scanner - Built with love by Moon Dev 🚀"""
        self.base_dir = Path(__file__).parent / "api_data"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = DATA_FOLDER
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.seen_tokens = set()
        self.last_check_time = None
        self.sound_enabled = SOUND_ENABLED
        
        # Only check sound files if sound is enabled
        if self.sound_enabled:
            for sound_file in SOUND_EFFECTS:
                        if not os.path.exists(sound_file):
                            print(f"⚠️ Warning: Sound file not found: {sound_file}")
                            self.sound_enabled = False
                            break
                    
    def attention_animation(self):
        """Run an attention-grabbing animation"""
        start_time = time.time()
        position = 0
        direction = 1
        width = 40  # Animation width
        
        try:
            # Start on current line
            while time.time() - start_time < ANIMATION_DURATION:
                # Create animation frame
                emojis = random.sample(ATTENTION_EMOJIS, 3)  # Pick 3 random emojis
                spaces = ' ' * position
                color = random.choice(BACKGROUND_COLORS)
                
                # Print the animation frame with random colors
                print(f'\r{spaces}{colored("".join(emojis), "white", color)}' + ' ' * (width - position), end='', flush=True)
                
                # Update position for bouncing effect
                position += direction
                if position >= width or position <= 0:
                    direction *= -1
                
                time.sleep(0.1)
            
            # Clear the animation line completely
            print('\r' + ' ' * (width + 20), end='\r', flush=True)
            
        except KeyboardInterrupt:
            # Clear line if interrupted
            print('\r' + ' ' * (width + 20), end='\r', flush=True)
            
    def get_token_addresses(self):
        """Fetch token data silently"""
        try:
            url = f'{BASE_URL}/files/new_token_addresses.csv'
            response = requests.get(url)
            response.raise_for_status()
            
            # Save to cache
            save_path = self.base_dir / "new_token_addresses.csv"
            with open(save_path, 'wb') as f:
                f.write(response.content)
            
            df = pd.read_csv(save_path)
            return df
                
        except Exception:
            return None
        
    def filter_tokens(self, df):
        """Filter out unwanted tokens and sort by timestamp"""
        if df is None or df.empty:
            return pd.DataFrame()
            
        # Filter out tokens containing excluded patterns
        for pattern in EXCLUDE_PATTERNS:
            df = df[~df['Token Address'].str.contains(pattern, case=True)]
            
        # Sort by time found, oldest first
        return df.sort_values('Epoch Time', ascending=True)
        
    def get_display_link(self, birdeye_link):
        """Convert Birdeye link to DexScreener link if enabled"""
        if not USE_DEXSCREENER:
            return birdeye_link
            
        try:
            # Extract contract address from Birdeye link
            # Format: https://birdeye.so/token/CONTRACT_ADDRESS?chain=solana
            contract_address = birdeye_link.split('/token/')[1].split('?')[0]
            return f"https://dexscreener.com/solana/{contract_address}"
        except Exception:
            return birdeye_link
            
    def display_past_token(self, address, time_found, birdeye_link):
        """Display a past token without animation"""
        try:
            time_obj = pd.to_datetime(time_found)
            time_str = time_obj.strftime("%m-%d %H:%M")
        except Exception as e:
            print(f'❌ ERROR IN src/agents/sniper_agent.py: {e}')
            time_str = time_found
            
        random_emoji = random.choice(LAUNCH_EMOJIS)
        random_bg = random.choice(BACKGROUND_COLORS)
        
        display_link = self.get_display_link(birdeye_link)
            
        print(f"\n{colored(f'{random_emoji} NEW TOKEN FOUND', 'white', random_bg)} {time_str}")
        print(f"{display_link}")
        
        # Auto-open in browser if enabled
        if AUTO_OPEN_BROWSER:
            try:
                import webbrowser
                webbrowser.open(display_link)
            except Exception:
                pass
                
        time.sleep(DISPLAY_DELAY)
        
    def save_tokens_for_analysis(self, df):
        """Save tokens to CSV for analysis"""
        try:
            # Add timestamp column for when we saved this data
            df['saved_at'] = pd.Timestamp.now()
            
            # Save to CSV
            save_path = self.data_dir / "recent_tokens.csv"
            df.to_csv(save_path, index=False)
        except Exception as e:
            print(f'❌ ERROR IN src/agents/sniper_agent.py: {e}')
            pass
            
    def show_past_tokens(self):
        """Display past token launches"""
        df = self.get_token_addresses()
        df = self.filter_tokens(df)
        
        if df.empty:
            return
            
        # Get the most recent tokens (from the end since we're sorted ascending)
        recent_tokens = df.tail(PAST_TOKENS_TO_SHOW)
        
        # Store seen tokens and last check time
        self.seen_tokens = set(recent_tokens['Token Address'])
        self.last_check_time = pd.to_datetime(recent_tokens.iloc[-1]['Time Found'])
        
        # Save tokens for analysis
        self.save_tokens_for_analysis(recent_tokens)
        
        print("\n🔍 Recent Token Launches:")
        for _, row in recent_tokens.iterrows():
            self.display_past_token(
                row['Token Address'],
                row['Time Found'],
                row['Birdeye Link']
            )
            
    def play_sound(self):
        """Play a random sound effect safely"""
        if not self.sound_enabled:
            return
            
        try:
            sound_file = random.choice(SOUND_EFFECTS)
            playsound(sound_file, block=False)
        except Exception:
            pass
            
    def display_token(self, address, time_found, birdeye_link, count=None):
        """Display a new token with animation"""
        try:
            time_obj = pd.to_datetime(time_found)
            time_str = time_obj.strftime("%m-%d %H:%M")
        except Exception as e:
            print(f'❌ ERROR IN src/agents/sniper_agent.py: {e}')
            time_str = time_found
            
        random_emoji = random.choice(LAUNCH_EMOJIS)
        random_bg = random.choice(BACKGROUND_COLORS)
        
        display_link = self.get_display_link(birdeye_link)
            
        print(f"\n{colored(f'{random_emoji} NEW TOKEN FOUND', 'white', random_bg)} {time_str}")
        print(f"{display_link}")
        
        # Auto-open in browser if enabled
        if AUTO_OPEN_BROWSER:
            try:
                import webbrowser
                webbrowser.open(display_link)
            except Exception as e:
                print(f'❌ ERROR IN src/agents/sniper_agent.py: {e}')
                pass
        
        # Play sound first, then do animation
        self.play_sound()
        self.attention_animation()
        
    def monitor_new_launches(self):
        """Monitor for new token launches silently"""
        while True:
            try:
                df = self.get_token_addresses()
                if df is None:
                    time.sleep(CHECK_INTERVAL)
                    continue
                    
                df = self.filter_tokens(df)
                
                if df.empty:
                    time.sleep(CHECK_INTERVAL)
                    continue
                
                # Get the newest token's time
                current_time = pd.to_datetime(df.iloc[-1]['Time Found'])
                
                if self.last_check_time and current_time > self.last_check_time:
                    # Get only tokens newer than our last check
                    new_df = df[pd.to_datetime(df['Time Found']) > self.last_check_time]

                    # Apply an explicit freshness filter so we only treat very recent launches
                    try:
                        freshness_threshold = pd.Timestamp.now() - pd.Timedelta(seconds=MAX_FRESH_AGE_SECONDS)
                        new_df = new_df[pd.to_datetime(new_df['Time Found']) >= freshness_threshold]
                    except Exception:
                        # If anything goes wrong parsing timestamps, fall back to unfiltered new_df
                        pass
                    new_tokens = set(new_df['Token Address']) - self.seen_tokens
                    
                    if new_tokens:
                        # Get all new tokens in chronological order
                        new_token_rows = new_df[new_df['Token Address'].isin(new_tokens)]
                        
                        # Save updated tokens list for analysis
                        all_recent = pd.concat([
                            pd.read_csv(self.data_dir / "recent_tokens.csv"),
                            new_token_rows
                        ]).tail(PAST_TOKENS_TO_SHOW)
                        self.save_tokens_for_analysis(all_recent)
                        
                        # Display new tokens
                        for _, row in new_token_rows.iterrows():
                            try:
                                # Check Birdeye token_security for rug/momentum rejects
                                try:
                                    is_reject, token_name, details = is_momentum_reject(row['Token Address'])
                                except Exception:
                                    is_reject, token_name, details = (False, row['Token Address'], {})

                                if is_reject:
                                    # Notify Discord (if configured) and skip display
                                    msg = f"⛔️ RUG DETECTED: {token_name} ({row['Token Address']})"
                                    send_discord_message(msg)
                                    print(colored(msg, 'white', 'on_red'))
                                    # Mark seen so we don't repeatedly notify
                                    self.seen_tokens.add(row['Token Address'])
                                    continue

                                # Safe to display
                                self.display_token(
                                    row['Token Address'],
                                    row['Time Found'],
                                    row['Birdeye Link']
                                )
                                self.seen_tokens.add(row['Token Address'])
                            except Exception as e:
                                print(f'❌ ERROR IN src/agents/sniper_agent.py: {e}')
                                pass
                        
                        self.last_check_time = current_time
                
            except Exception:
                pass
                
            time.sleep(CHECK_INTERVAL)

def main():
    """Main entry point"""
    scanner = TokenScanner()
    scanner.show_past_tokens()
    scanner.monitor_new_launches()

if __name__ == "__main__":
    main() 