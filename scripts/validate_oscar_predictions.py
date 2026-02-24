"""
Oscar Predictions Validator

Compares our Oscar prediction model against Gold Derby consensus.
Oscar nominations announce January 23, 2025 (~65 hours from now).

This validates whether our edge calculations are accurate before risking money.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from bs4 import BeautifulSoup
from termcolor import cprint
from datetime import datetime

# Our model's predictions (from entertainment_source.py)
OUR_PREDICTIONS = {
    'best_picture': {
        'strong_contenders': ['anora', 'the brutalist', 'conclave', 'emilia perez', 'wicked', 'dune: part two', 'a real pain', 'september 5'],
        'likely_nominees': ['anora', 'the brutalist', 'conclave', 'emilia perez', 'wicked'],
    },
    'best_actor': {
        'strong_contenders': ['adrien brody', 'timothee chalamet', 'ralph fiennes', 'colman domingo', 'sebastian stan'],
        'likely_nominees': ['adrien brody', 'timothee chalamet', 'ralph fiennes'],
    },
    'best_actress': {
        'strong_contenders': ['demi moore', 'mikey madison', 'fernanda torres', 'cynthia erivo', 'karla sofia gascon'],
        'likely_nominees': ['demi moore', 'mikey madison', 'fernanda torres'],
    },
    'best_director': {
        'strong_contenders': ['brady corbet', 'jacques audiard', 'sean baker', 'coralie fargeat', 'denis villeneuve'],
        'likely_nominees': ['brady corbet', 'jacques audiard', 'sean baker'],
    },
}

# Gold Derby URLs for scraping
GOLD_DERBY_URLS = {
    'best_picture': 'https://www.goldderby.com/odds/combined-odds/oscars-nominations-2025/best-picture-nominations/',
    'best_director': 'https://www.goldderby.com/odds/combined-odds/oscars-nominations-2025/best-director-nominations/',
    'best_actor': 'https://www.goldderby.com/odds/combined-odds/oscars-nominations-2025/best-actor-nominations/',
    'best_actress': 'https://www.goldderby.com/odds/combined-odds/oscars-nominations-2025/best-actress-nominations/',
}


def fetch_gold_derby_predictions(category: str) -> list:
    """Scrape Gold Derby for their current predictions."""
    if category not in GOLD_DERBY_URLS:
        return []

    try:
        url = GOLD_DERBY_URLS[category]
        client = httpx.Client(timeout=15.0, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

        resp = client.get(url)
        if resp.status_code != 200:
            cprint(f"Failed to fetch Gold Derby ({resp.status_code})", "red")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Try to find prediction entries
        predictions = []

        # Look for common Gold Derby patterns
        entries = soup.select('.steep-entry, .odds-entry, tr.contestant, .prediction-item')

        for i, entry in enumerate(entries[:15], 1):
            name_el = entry.select_one('.steep-entry-name, .name, td:first-child, .title')
            if name_el:
                name = name_el.get_text(strip=True).lower()
                predictions.append({'rank': i, 'name': name})

        # Fallback: just get text and look for names
        if not predictions:
            text = soup.get_text()
            # This is a simplified fallback
            cprint(f"Could not parse Gold Derby structure for {category}", "yellow")

        client.close()
        return predictions

    except Exception as e:
        cprint(f"Error fetching Gold Derby: {e}", "red")
        return []


def compare_predictions(category: str, gold_derby: list, our_model: dict):
    """Compare our predictions vs Gold Derby."""
    cprint(f"\n{'='*60}", "cyan")
    cprint(f"Category: {category.upper().replace('_', ' ')}", "cyan", attrs=['bold'])
    cprint(f"{'='*60}", "cyan")

    our_contenders = set(our_model.get('strong_contenders', []))
    our_likely = set(our_model.get('likely_nominees', []))

    if gold_derby:
        gd_top5 = set(p['name'] for p in gold_derby[:5])
        gd_top10 = set(p['name'] for p in gold_derby[:10])

        cprint("\nGold Derby Top 5:", "yellow")
        for p in gold_derby[:5]:
            name = p['name']
            in_ours = "✓" if any(c in name or name in c for c in our_contenders) else "✗"
            color = "green" if in_ours == "✓" else "red"
            cprint(f"  {p['rank']}. {name} [{in_ours}]", color)

        cprint("\nOur Strong Contenders:", "yellow")
        for name in our_contenders:
            in_gd = "✓" if any(name in g['name'] or g['name'] in name for g in gold_derby[:10]) else "?"
            color = "green" if in_gd == "✓" else "yellow"
            cprint(f"  - {name} [{in_gd}]", color)

        # Calculate overlap
        overlap_top5 = sum(1 for c in our_contenders if any(c in g['name'] or g['name'] in c for g in gold_derby[:5]))
        overlap_top10 = sum(1 for c in our_contenders if any(c in g['name'] or g['name'] in c for g in gold_derby[:10]))

        cprint(f"\nOverlap with Gold Derby Top 5: {overlap_top5}/{len(our_contenders)}", "white")
        cprint(f"Overlap with Gold Derby Top 10: {overlap_top10}/{len(our_contenders)}", "white")

    else:
        cprint("\nCould not fetch Gold Derby data - showing our predictions only:", "yellow")
        for name in our_contenders:
            cprint(f"  - {name}", "white")


def main():
    cprint("\n" + "="*60, "magenta")
    cprint("OSCAR PREDICTIONS VALIDATOR", "magenta", attrs=['bold'])
    cprint(f"Nominations announce: January 23, 2025", "magenta")
    cprint(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "magenta")
    cprint("="*60, "magenta")

    # Validate each category
    for category in ['best_picture', 'best_director', 'best_actor', 'best_actress']:
        gold_derby = fetch_gold_derby_predictions(category)
        our_model = OUR_PREDICTIONS.get(category, {})
        compare_predictions(category, gold_derby, our_model)

    cprint("\n" + "="*60, "green")
    cprint("VALIDATION SUMMARY", "green", attrs=['bold'])
    cprint("="*60, "green")
    cprint("""
Key insights:
- If our contenders match Gold Derby top 5-10, our model is calibrated
- Mismatches = potential edge OR model error
- Oscar nominations in ~65 hours will reveal truth

Recommendation:
- High overlap = trust the model for similar categories
- Low overlap = investigate before betting
""", "white")


if __name__ == "__main__":
    main()
