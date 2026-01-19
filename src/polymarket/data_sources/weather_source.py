"""
Weather Data Source

Uses NOAA and OpenWeatherMap APIs to get weather predictions
for comparison with Polymarket weather markets.

Free APIs:
- NOAA: Free, US-focused
- OpenWeatherMap: Free tier (1000 calls/day)
"""

import os
import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# API endpoints
NOAA_API_BASE = "https://api.weather.gov"
OPENWEATHER_API_BASE = "https://api.openweathermap.org/data/2.5"

# Get API key from environment
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")


class WeatherSource:
    """
    Weather prediction data source.

    Matches markets about:
    - Temperature records
    - Hurricane/storm predictions
    - Rainfall amounts
    - Snow totals
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}  # Simple cache to avoid repeated API calls

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate for a weather market.
        """
        question = (market.get('question') or '').lower()

        # Try to match market type
        if 'hurricane' in question or 'tropical storm' in question:
            return self._get_hurricane_probability(market)
        elif 'temperature' in question or 'record' in question:
            return self._get_temperature_probability(market)
        elif 'rain' in question or 'precipitation' in question:
            return self._get_precipitation_probability(market)

        return None

    def _get_hurricane_probability(self, market: Dict) -> Optional[Dict]:
        """Get hurricane/storm probability from NOAA."""
        try:
            # NOAA Hurricane Center API
            url = f"{NOAA_API_BASE}/alerts/active"
            params = {'event': 'Hurricane,Tropical Storm'}

            response = self.client.get(url, params=params)
            if response.status_code != 200:
                return None

            data = response.json()
            alerts = data.get('features', [])

            # Check if there are active hurricane warnings
            has_active_hurricane = len(alerts) > 0

            return {
                'source': 'NOAA',
                'probability': 0.8 if has_active_hurricane else 0.2,
                'confidence': 0.7,
                'reasoning': f"NOAA shows {len(alerts)} active hurricane/storm alerts",
                'last_updated': datetime.now(timezone.utc),
            }

        except Exception as e:
            logger.error(f"NOAA API error: {e}")
            return None

    def _get_temperature_probability(self, market: Dict) -> Optional[Dict]:
        """Get temperature prediction probability."""
        if not OPENWEATHER_API_KEY:
            logger.warning("OPENWEATHER_API_KEY not set")
            return None

        question = (market.get('question') or '').lower()

        # Try to extract city and temperature threshold
        city = self._extract_city(question)
        threshold = self._extract_temperature(question)

        if not city or threshold is None:
            return None

        try:
            # Get forecast
            url = f"{OPENWEATHER_API_BASE}/forecast"
            params = {
                'q': city,
                'appid': OPENWEATHER_API_KEY,
                'units': 'imperial',  # Fahrenheit
            }

            response = self.client.get(url, params=params)
            if response.status_code != 200:
                return None

            data = response.json()
            forecasts = data.get('list', [])

            # Check if any forecast exceeds threshold
            max_temp = max(f['main']['temp_max'] for f in forecasts) if forecasts else 0
            min_temp = min(f['main']['temp_min'] for f in forecasts) if forecasts else 0

            # Determine probability based on forecast vs threshold
            if 'above' in question or 'over' in question:
                prob = 0.9 if max_temp > threshold else 0.2
            elif 'below' in question or 'under' in question:
                prob = 0.9 if min_temp < threshold else 0.2
            else:
                prob = 0.5

            return {
                'source': 'OpenWeatherMap',
                'probability': prob,
                'confidence': 0.7,
                'reasoning': f"Forecast: max {max_temp:.0f}F, min {min_temp:.0f}F vs threshold {threshold}F",
                'last_updated': datetime.now(timezone.utc),
            }

        except Exception as e:
            logger.error(f"OpenWeather API error: {e}")
            return None

    def _get_precipitation_probability(self, market: Dict) -> Optional[Dict]:
        """Get precipitation prediction probability."""
        # Similar to temperature, would query weather API
        # Placeholder for now
        return None

    def _extract_city(self, question: str) -> Optional[str]:
        """Extract city name from question."""
        # Common cities in weather markets
        cities = [
            'new york', 'los angeles', 'chicago', 'houston', 'phoenix',
            'miami', 'dallas', 'denver', 'seattle', 'boston',
            'san francisco', 'atlanta', 'las vegas', 'orlando',
        ]

        for city in cities:
            if city in question:
                return city.title()

        return None

    def _extract_temperature(self, question: str) -> Optional[float]:
        """Extract temperature threshold from question."""
        # Match patterns like "100 degrees", "100F", "100°"
        patterns = [
            r'(\d+)\s*(?:degrees?|°|f)',
            r'above\s*(\d+)',
            r'below\s*(\d+)',
            r'over\s*(\d+)',
            r'under\s*(\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                return float(match.group(1))

        return None

    def close(self):
        """Close HTTP client."""
        self.client.close()
