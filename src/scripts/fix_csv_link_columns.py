"""
🌙 Moon Dev - Fix CSV Link Columns Script
Moves the 'link' or 'market_link' column to be the LAST column in CSVs
This makes links clickable when opening in Excel/Numbers/etc.
"""

import pandas as pd
import os
from pathlib import Path
from termcolor import cprint

# Paths to CSVs
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONSENSUS_PICKS_CSV = os.path.join(PROJECT_ROOT, "src/data/polymarket/consensus_picks.csv")
PREDICTIONS_CSV = os.path.join(PROJECT_ROOT, "src/data/polymarket/predictions.csv")

def fix_csv_link_column(csv_path, link_column_name):
    """
    🌙 Moon Dev - Move link column to the end of CSV

    Args:
        csv_path: Path to CSV file
        link_column_name: Name of the link column to move
    """
    try:
        if not os.path.exists(csv_path):
            cprint(f"⚠️ CSV not found: {csv_path}", "yellow")
            return

        cprint(f"\n📝 Processing: {os.path.basename(csv_path)}", "cyan", attrs=['bold'])

        # Load CSV
        df = pd.read_csv(csv_path)
        cprint(f"   ├─ Loaded {len(df)} rows", "cyan")
        cprint(f"   ├─ Current columns: {list(df.columns)}", "white")

        # Check if link column exists
        if link_column_name not in df.columns:
            cprint(f"   ⚠️ Column '{link_column_name}' not found - skipping", "yellow")
            return

        # Get current position
        current_position = df.columns.tolist().index(link_column_name)
        cprint(f"   ├─ Current position of '{link_column_name}': {current_position}", "yellow")

        # Move link column to end
        cols = df.columns.tolist()
        cols.remove(link_column_name)
        cols.append(link_column_name)
        df = df[cols]

        cprint(f"   ├─ New columns order: {list(df.columns)}", "green")
        cprint(f"   ├─ '{link_column_name}' is now at position: {len(cols) - 1} (last)", "green")

        # Create backup
        backup_path = csv_path + ".backup"
        if os.path.exists(csv_path):
            import shutil
            shutil.copy2(csv_path, backup_path)
            cprint(f"   ├─ Backup created: {os.path.basename(backup_path)}", "cyan")

        # Save fixed CSV
        df.to_csv(csv_path, index=False)
        cprint("   └─ ✅ Saved fixed CSV!", "green", attrs=['bold'])

    except Exception as e:
        cprint(f"❌ Error fixing {os.path.basename(csv_path)}: {e}", "red")
        import traceback
        traceback.print_exc()

def main():
    """🌙 Moon Dev - Main script"""
    cprint("\n" + "="*80, "cyan")
    cprint("🌙 Moon Dev - Fix CSV Link Columns", "cyan", attrs=['bold'])
    cprint("="*80 + "\n", "cyan")

    cprint("This script moves 'link' columns to the END of CSVs", "white")
    cprint("This makes links clickable when opening in Excel/Numbers/etc.\n", "white")

    # Fix consensus_picks.csv (link column)
    fix_csv_link_column(CONSENSUS_PICKS_CSV, 'link')

    # Fix predictions.csv (market_link column)
    fix_csv_link_column(PREDICTIONS_CSV, 'market_link')

    cprint("\n" + "="*80, "green")
    cprint("✅ Done! Your CSV link columns are now at the end.", "green", attrs=['bold'])
    cprint("="*80 + "\n", "green")

    cprint("Backup files created with .backup extension", "cyan")
    cprint("Original CSVs have been updated with link columns at the end\n", "cyan")

if __name__ == "__main__":
    main()
