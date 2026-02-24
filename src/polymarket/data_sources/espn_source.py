"""
ESPN Sports Data Source (Free, No API Key Required)

Fallback for when The Odds API is exhausted.
Uses ESPN's public API endpoints to get:
- Game schedules and scores
- Team records and rankings
- Win probability estimates

ESPN API endpoints (no key needed):
- /site/v2/sports/{sport}/{league}/scoreboard
- /site/v2/sports/{sport}/{league}/teams

Note: ESPN doesn't provide betting odds directly, but we can estimate
probabilities from team records, home/away status, and ESPN's own predictions.
"""

import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN sport/league paths
ESPN_SPORTS = {
    'nba': 'basketball/nba',
    'nfl': 'football/nfl',
    'nhl': 'hockey/nhl',
    'mlb': 'baseball/mlb',
    'ufc': 'mma/ufc',
    'mma': 'mma/ufc',
    'ncaaf': 'football/college-football',
    'ncaab': 'basketball/mens-college-basketball',
    'soccer': 'soccer/eng.1',  # Premier League
    'golf': 'golf/pga',
    'tennis': 'tennis/atp',
}

# Team name variations for matching
TEAM_ALIASES = {
    # NBA - All 30 teams
    'lakers': ['los angeles lakers', 'la lakers', 'lakers'],
    'celtics': ['boston celtics', 'celtics'],
    'warriors': ['golden state warriors', 'warriors', 'golden state'],
    'heat': ['miami heat', 'heat'],
    'bucks': ['milwaukee bucks', 'bucks'],
    'nuggets': ['denver nuggets', 'nuggets'],
    'suns': ['phoenix suns', 'suns'],
    'sixers': ['philadelphia 76ers', '76ers', 'sixers', 'philadelphia'],
    'nets': ['brooklyn nets', 'nets'],
    'clippers': ['la clippers', 'los angeles clippers', 'clippers'],
    'mavericks': ['dallas mavericks', 'mavs', 'mavericks'],
    'thunder': ['oklahoma city thunder', 'okc', 'thunder', 'oklahoma city'],
    'timberwolves': ['minnesota timberwolves', 'wolves', 'timberwolves', 'minnesota'],
    'knicks': ['new york knicks', 'knicks'],
    'cavaliers': ['cleveland cavaliers', 'cavs', 'cavaliers', 'cleveland'],
    'jazz': ['utah jazz', 'jazz', 'utah'],
    'hawks': ['atlanta hawks', 'hawks', 'atlanta'],
    'raptors': ['toronto raptors', 'raptors', 'toronto'],
    'bulls': ['chicago bulls', 'bulls', 'chicago'],
    'pistons': ['detroit pistons', 'pistons'],
    'pacers': ['indiana pacers', 'pacers', 'indiana'],
    'magic': ['orlando magic', 'magic', 'orlando'],
    'hornets': ['charlotte hornets', 'hornets', 'charlotte'],
    'wizards': ['washington wizards', 'wizards', 'washington'],
    'spurs': ['san antonio spurs', 'spurs', 'san antonio'],
    'rockets': ['houston rockets', 'rockets', 'houston'],
    'pelicans': ['new orleans pelicans', 'pelicans', 'new orleans'],
    'grizzlies': ['memphis grizzlies', 'grizzlies', 'memphis'],
    'trail blazers': ['portland trail blazers', 'blazers', 'trail blazers', 'portland'],
    'kings': ['sacramento kings', 'kings', 'sacramento'],

    # NFL - All 32 teams
    'chiefs': ['kansas city chiefs', 'chiefs', 'kc', 'kansas city'],
    'eagles': ['philadelphia eagles', 'eagles', 'philly'],
    'bills': ['buffalo bills', 'bills', 'buffalo'],
    '49ers': ['san francisco 49ers', '49ers', 'niners', 'san francisco'],
    'cowboys': ['dallas cowboys', 'cowboys', 'dallas'],
    'ravens': ['baltimore ravens', 'ravens', 'baltimore'],
    'lions': ['detroit lions', 'lions'],
    'dolphins': ['miami dolphins', 'dolphins'],
    'packers': ['green bay packers', 'packers', 'green bay'],
    'rams': ['los angeles rams', 'la rams', 'rams'],
    'broncos': ['denver broncos', 'broncos'],
    'seahawks': ['seattle seahawks', 'seahawks', 'seattle'],
    'chargers': ['los angeles chargers', 'la chargers', 'chargers'],
    'raiders': ['las vegas raiders', 'raiders', 'vegas raiders'],
    'patriots': ['new england patriots', 'patriots', 'pats'],
    'jets': ['new york jets', 'jets'],
    'giants': ['new york giants', 'giants'],
    'commanders': ['washington commanders', 'commanders'],
    'bears': ['chicago bears', 'bears'],
    'vikings': ['minnesota vikings', 'vikings'],
    'saints': ['new orleans saints', 'saints'],
    'buccaneers': ['tampa bay buccaneers', 'bucs', 'buccaneers', 'tampa bay'],
    'falcons': ['atlanta falcons', 'falcons'],
    'panthers_nfl': ['carolina panthers', 'carolina'],
    'bengals': ['cincinnati bengals', 'bengals', 'cincinnati'],
    'browns': ['cleveland browns', 'browns'],
    'steelers': ['pittsburgh steelers', 'steelers', 'pittsburgh'],
    'colts': ['indianapolis colts', 'colts', 'indy'],
    'texans': ['houston texans', 'texans'],
    'jaguars': ['jacksonville jaguars', 'jaguars', 'jacksonville'],
    'titans': ['tennessee titans', 'titans', 'tennessee'],
    'cardinals': ['arizona cardinals', 'cardinals', 'arizona'],

    # NHL - Major teams
    'golden knights': ['vegas golden knights', 'golden knights', 'vegas'],
    'oilers': ['edmonton oilers', 'oilers', 'edmonton'],
    'panthers_nhl': ['florida panthers', 'florida'],
    'hurricanes': ['carolina hurricanes', 'canes', 'hurricanes'],
    'stars': ['dallas stars', 'stars'],
    'avalanche': ['colorado avalanche', 'avs', 'avalanche', 'colorado'],
    'bruins': ['boston bruins', 'bruins'],
    'rangers': ['new york rangers', 'rangers'],
    'maple leafs': ['toronto maple leafs', 'maple leafs', 'leafs'],
    'lightning': ['tampa bay lightning', 'lightning', 'bolts'],
    'canadiens': ['montreal canadiens', 'canadiens', 'habs'],
    'penguins': ['pittsburgh penguins', 'pens', 'penguins'],
    'capitals': ['washington capitals', 'caps', 'capitals'],
    'red wings': ['detroit red wings', 'red wings', 'wings'],
    'blackhawks': ['chicago blackhawks', 'blackhawks'],
    'flyers': ['philadelphia flyers', 'flyers'],
    'flames': ['calgary flames', 'flames', 'calgary'],
    'canucks': ['vancouver canucks', 'canucks', 'vancouver'],
    'kraken': ['seattle kraken', 'kraken'],
    'wild': ['minnesota wild', 'wild'],
    'blues': ['st louis blues', 'blues', 'st. louis blues'],
    'predators': ['nashville predators', 'preds', 'predators', 'nashville'],
    'jets_nhl': ['winnipeg jets', 'winnipeg'],
    'senators': ['ottawa senators', 'sens', 'senators', 'ottawa'],
    'sabres': ['buffalo sabres', 'sabres'],
    'islanders': ['new york islanders', 'islanders', 'isles'],
    'devils': ['new jersey devils', 'devils'],
    'blue jackets': ['columbus blue jackets', 'blue jackets', 'jackets', 'columbus'],
    'coyotes': ['arizona coyotes', 'coyotes'],
    'ducks': ['anaheim ducks', 'ducks', 'anaheim'],
    'sharks': ['san jose sharks', 'sharks', 'san jose'],
}


class ESPNSource:
    """
    ESPN data source for sports probability estimation.

    Free API with no rate limits or API key required.
    Provides game schedules, team records, and win predictions.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}
        self.cache_ttl = 1800  # 30 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability estimate for a sports market."""
        question = (market.get('question') or '').lower()

        # Detect sport and team
        sport = self._detect_sport(question)
        if not sport:
            return None

        team = self._extract_team(question)
        if not team:
            return None

        # Get team data from ESPN
        team_data = self._get_team_data(sport, team)
        if not team_data:
            return None

        # Get upcoming game if asking about specific matchup
        game = self._get_upcoming_game(sport, team)

        # Calculate probability estimate
        prob = self._estimate_probability(team_data, game, question)
        if not prob:
            return None

        return prob

    def _detect_sport(self, question: str) -> Optional[str]:
        """Detect sport from question text."""
        q = question.lower()

        # Direct sport mentions
        sport_keywords = {
            'nba': ['nba', 'basketball'],
            'nfl': ['nfl', 'football', 'super bowl'],
            'nhl': ['nhl', 'hockey', 'stanley cup'],
            'mlb': ['mlb', 'baseball', 'world series'],
            'ufc': ['ufc', 'mma', 'fight'],
            'ncaaf': ['college football', 'ncaaf', 'cfp'],
            'ncaab': ['college basketball', 'ncaab', 'march madness'],
            'soccer': ['premier league', 'epl', 'soccer', 'football club'],
            'golf': ['golf', 'pga', 'masters', 'open championship'],
            'tennis': ['tennis', 'atp', 'wta', 'wimbledon', 'us open'],
        }

        for sport, keywords in sport_keywords.items():
            if any(kw in q for kw in keywords):
                return sport

        # Detect from team names
        nba_teams = ['lakers', 'celtics', 'warriors', 'heat', 'bucks', 'nuggets',
                     'suns', 'sixers', 'nets', 'clippers', 'mavericks', 'thunder',
                     'timberwolves', 'knicks', 'cavaliers', 'jazz', 'hawks', 'raptors',
                     'bulls', 'pistons', 'pacers', 'magic', 'hornets', 'wizards',
                     'spurs', 'rockets', 'pelicans', 'grizzlies', 'trail blazers',
                     'kings']
        nfl_teams = ['chiefs', 'eagles', 'bills', '49ers', 'cowboys', 'ravens',
                     'lions', 'dolphins', 'packers', 'rams', 'broncos', 'seahawks',
                     'chargers', 'raiders', 'patriots', 'jets', 'giants', 'commanders',
                     'bears', 'vikings', 'saints', 'buccaneers', 'falcons', 'panthers_nfl',
                     'bengals', 'browns', 'steelers', 'colts', 'texans', 'jaguars',
                     'titans', 'cardinals']
        nhl_teams = ['golden knights', 'oilers', 'panthers_nhl', 'hurricanes',
                     'stars', 'avalanche', 'bruins', 'rangers', 'maple leafs',
                     'lightning', 'canadiens', 'penguins', 'capitals', 'red wings',
                     'blackhawks', 'flyers', 'flames', 'canucks', 'kraken', 'wild',
                     'blues', 'predators', 'jets_nhl', 'senators', 'sabres',
                     'islanders', 'devils', 'blue jackets', 'coyotes', 'ducks', 'sharks']

        for team, aliases in TEAM_ALIASES.items():
            if any(alias in q for alias in aliases):
                # Infer sport from team
                if team in nba_teams:
                    return 'nba'
                elif team in nfl_teams:
                    return 'nfl'
                elif team in nhl_teams:
                    return 'nhl'

        return None

    def _extract_team(self, question: str) -> Optional[str]:
        """Extract team name from question."""
        q = question.lower()

        # Check aliases
        for team, aliases in TEAM_ALIASES.items():
            if any(alias in q for alias in aliases):
                return team

        # Generic extraction - look for capitalized team names
        words = question.split()
        for i, word in enumerate(words):
            if word[0].isupper() and len(word) > 3:
                potential_team = word.lower()
                if potential_team not in ['will', 'the', 'win', 'beat']:
                    return potential_team

        return None

    def _get_team_data(self, sport: str, team: str) -> Optional[Dict]:
        """Get team data from ESPN standings (includes records)."""
        cache_key = f"team_{sport}_{team}"

        # Check cache
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        # First try standings (has record data)
        result = self._get_team_from_standings(sport, team)
        if result:
            self.cache[cache_key] = {'data': result, 'time': datetime.now(timezone.utc)}
            return result

        # Fallback to teams endpoint (less data but wider coverage)
        result = self._get_team_from_teams_api(sport, team)
        if result:
            self.cache[cache_key] = {'data': result, 'time': datetime.now(timezone.utc)}
            return result

        return None

    def _get_team_from_standings(self, sport: str, team: str) -> Optional[Dict]:
        """Get team data from standings API (includes records)."""
        try:
            sport_path = ESPN_SPORTS.get(sport, sport)
            url = f"https://site.api.espn.com/apis/v2/sports/{sport_path}/standings"

            response = self.client.get(url)
            if response.status_code != 200:
                logger.debug(f"ESPN standings API error: {response.status_code}")
                return None

            data = response.json()
            team_lower = team.lower()

            # Search through all conference/division groups
            for group in data.get('children', []):
                standings = group.get('standings', {})
                for entry in standings.get('entries', []):
                    team_info = entry.get('team', {})
                    name = team_info.get('displayName', '').lower()
                    short = team_info.get('shortDisplayName', '').lower()
                    abbr = team_info.get('abbreviation', '').lower()

                    if team_lower in name or team_lower in short or team_lower == abbr:
                        # Parse stats
                        stats = {s.get('name'): s for s in entry.get('stats', [])}

                        wins = int(float(stats.get('wins', {}).get('value', 0)))
                        losses = int(float(stats.get('losses', {}).get('value', 0)))
                        win_pct = float(stats.get('winPercent', {}).get('value', 0.5))

                        return {
                            'id': team_info.get('id'),
                            'name': team_info.get('displayName'),
                            'abbreviation': team_info.get('abbreviation'),
                            'record': {
                                'wins': wins,
                                'losses': losses,
                                'win_pct': win_pct,
                                'summary': f"{wins}-{losses}"
                            },
                            'rank': None,  # Will get from standings position
                            'logo': team_info.get('logos', [{}])[0].get('href', ''),
                        }

            return None

        except Exception as e:
            logger.debug(f"ESPN standings lookup error: {e}")
            return None

    def _get_team_from_teams_api(self, sport: str, team: str) -> Optional[Dict]:
        """Fallback: Get team data from teams API (no records)."""
        try:
            sport_path = ESPN_SPORTS.get(sport, sport)
            url = f"{ESPN_API_BASE}/{sport_path}/teams"

            response = self.client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            teams = data.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
            team_lower = team.lower()

            for t in teams:
                team_info = t.get('team', {})
                name = team_info.get('displayName', '').lower()
                short = team_info.get('shortDisplayName', '').lower()
                abbr = team_info.get('abbreviation', '').lower()

                if team_lower in name or team_lower in short or team_lower == abbr:
                    return {
                        'id': team_info.get('id'),
                        'name': team_info.get('displayName'),
                        'abbreviation': team_info.get('abbreviation'),
                        'record': None,  # Not available from this endpoint
                        'rank': team_info.get('rank'),
                        'logo': team_info.get('logos', [{}])[0].get('href', ''),
                    }

            return None

        except Exception as e:
            logger.debug(f"ESPN teams lookup error: {e}")
            return None

    def _get_upcoming_game(self, sport: str, team: str) -> Optional[Dict]:
        """Get upcoming game for a team."""
        cache_key = f"scoreboard_{sport}"

        # Check cache
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                games = cached['data']
            else:
                games = self._fetch_scoreboard(sport)
                if games:
                    self.cache[cache_key] = {'data': games, 'time': datetime.now(timezone.utc)}
        else:
            games = self._fetch_scoreboard(sport)
            if games:
                self.cache[cache_key] = {'data': games, 'time': datetime.now(timezone.utc)}

        if not games:
            return None

        # Find game with this team
        team_lower = team.lower()
        for game in games:
            competitors = game.get('competitions', [{}])[0].get('competitors', [])
            for comp in competitors:
                comp_team = comp.get('team', {})
                name = comp_team.get('displayName', '').lower()
                short = comp_team.get('shortDisplayName', '').lower()
                abbr = comp_team.get('abbreviation', '').lower()

                if team_lower in name or team_lower in short or team_lower == abbr:
                    return self._parse_game(game, team_lower)

        return None

    def _fetch_scoreboard(self, sport: str) -> List[Dict]:
        """Fetch current scoreboard from ESPN."""
        try:
            sport_path = ESPN_SPORTS.get(sport, sport)
            url = f"{ESPN_API_BASE}/{sport_path}/scoreboard"

            response = self.client.get(url)
            if response.status_code == 200:
                data = response.json()
                return data.get('events', [])
        except Exception as e:
            logger.debug(f"ESPN scoreboard error: {e}")

        return []

    def _parse_game(self, game: Dict, team_name: str) -> Dict:
        """Parse game data from ESPN."""
        competitions = game.get('competitions', [{}])
        if not competitions:
            return {}

        comp = competitions[0]
        competitors = comp.get('competitors', [])

        result = {
            'date': game.get('date'),
            'status': game.get('status', {}).get('type', {}).get('description'),
            'home_team': None,
            'away_team': None,
            'our_team_home': False,
            'espn_win_probability': None,
        }

        for c in competitors:
            team = c.get('team', {})
            team_data = {
                'name': team.get('displayName'),
                'abbreviation': team.get('abbreviation'),
                'record': c.get('records', [{}])[0].get('summary', 'N/A'),
                'score': c.get('score', '0'),
            }

            is_home = c.get('homeAway') == 'home'
            if is_home:
                result['home_team'] = team_data
            else:
                result['away_team'] = team_data

            # Check if this is our team
            name_lower = team.get('displayName', '').lower()
            if team_name in name_lower:
                result['our_team_home'] = is_home

        # Get ESPN's win probability if available
        predictor = comp.get('predictor', {})
        if predictor:
            home_prob = predictor.get('homeTeam', {}).get('gameProjection')
            away_prob = predictor.get('awayTeam', {}).get('gameProjection')
            if home_prob and result['our_team_home']:
                result['espn_win_probability'] = float(home_prob) / 100
            elif away_prob and not result['our_team_home']:
                result['espn_win_probability'] = float(away_prob) / 100

        return result

    def _estimate_probability(self, team_data: Dict, game: Optional[Dict], question: str) -> Optional[Dict]:
        """Estimate win probability from team data."""
        q = question.lower()

        # Championship/season-long questions
        if any(w in q for w in ['championship', 'win the', 'super bowl', 'stanley cup', 'world series', 'title']):
            return self._estimate_championship_probability(team_data, q)

        # Game-specific questions
        if game:
            return self._estimate_game_probability(team_data, game)

        # Default: use team record
        record = team_data.get('record')
        if record:
            win_pct = record.get('win_pct', 0.5)
            return {
                'source': 'ESPN Team Records',
                'probability': round(win_pct, 3),
                'confidence': 0.5,
                'reasoning': f"{team_data.get('name')} record: {record.get('summary', 'N/A')} ({win_pct:.1%} win rate)",
            }

        return None

    def _estimate_game_probability(self, team_data: Dict, game: Dict) -> Optional[Dict]:
        """Estimate probability for a specific game."""
        # If ESPN provides a win probability, use it
        espn_prob = game.get('espn_win_probability')
        if espn_prob:
            return {
                'source': 'ESPN Game Predictor',
                'probability': round(espn_prob, 3),
                'confidence': 0.75,
                'reasoning': f"ESPN's win probability for {team_data.get('name')}: {espn_prob:.1%}",
            }

        # Otherwise estimate from records
        record = team_data.get('record')
        if not record:
            return None

        base_prob = record.get('win_pct', 0.5)

        # Home advantage adjustment (+3-5%)
        if game.get('our_team_home'):
            base_prob = min(0.95, base_prob + 0.04)
            home_status = "home"
        else:
            base_prob = max(0.05, base_prob - 0.02)
            home_status = "away"

        opponent = game.get('away_team') if game.get('our_team_home') else game.get('home_team')
        opponent_name = opponent.get('name', 'Unknown') if opponent else 'Unknown'

        return {
            'source': 'ESPN Records + Home Advantage',
            'probability': round(base_prob, 3),
            'confidence': 0.55,
            'reasoning': f"{team_data.get('name')} ({home_status}) vs {opponent_name} - record-based estimate: {base_prob:.1%}",
        }

    def _estimate_championship_probability(self, team_data: Dict, question: str) -> Optional[Dict]:
        """Estimate championship probability."""
        record = team_data.get('record')
        rank = team_data.get('rank')

        # Base probability from record
        if record:
            win_pct = record.get('win_pct', 0.5)
        else:
            win_pct = 0.5

        # Championship probabilities are much lower than game probabilities
        # Top team might have 15-20% chance, average team 2-5%
        if rank:
            try:
                rank_num = int(rank)
                if rank_num <= 5:
                    base_prob = 0.15 - (rank_num - 1) * 0.02
                elif rank_num <= 10:
                    base_prob = 0.08 - (rank_num - 5) * 0.01
                else:
                    base_prob = max(0.01, 0.03 - (rank_num - 10) * 0.002)
            except:
                base_prob = win_pct * 0.10
        else:
            # No rank available - estimate from win percentage
            # Good teams (>65%) -> 5-15%, Average (45-65%) -> 2-5%, Bad (<45%) -> 1-2%
            if win_pct >= 0.65:
                base_prob = 0.05 + (win_pct - 0.65) * 0.30
            elif win_pct >= 0.45:
                base_prob = 0.02 + (win_pct - 0.45) * 0.15
            else:
                base_prob = 0.01 + win_pct * 0.02

        return {
            'source': 'ESPN Rankings + Records',
            'probability': round(min(0.30, base_prob), 3),
            'confidence': 0.45,  # Lower confidence for season-long predictions
            'reasoning': f"{team_data.get('name')} championship estimate based on record {record.get('summary', 'N/A') if record else 'N/A'}"
                        + (f" (Rank #{rank})" if rank else ""),
        }

    def get_league_standings(self, sport: str) -> List[Dict]:
        """Get league standings."""
        try:
            sport_path = ESPN_SPORTS.get(sport, sport)
            url = f"{ESPN_API_BASE}/{sport_path}/standings"

            response = self.client.get(url)
            if response.status_code == 200:
                data = response.json()
                standings = []
                for group in data.get('children', []):
                    for entry in group.get('standings', {}).get('entries', []):
                        team = entry.get('team', {})
                        stats = {s.get('name'): s.get('value') for s in entry.get('stats', [])}
                        standings.append({
                            'team': team.get('displayName'),
                            'wins': stats.get('wins', 0),
                            'losses': stats.get('losses', 0),
                            'win_pct': stats.get('winPercent', 0.5),
                        })
                return standings
        except Exception as e:
            logger.debug(f"ESPN standings error: {e}")

        return []

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[ESPNSource] = None


def get_espn_source() -> ESPNSource:
    """Get singleton ESPN source."""
    global _source
    if _source is None:
        _source = ESPNSource()
    return _source
