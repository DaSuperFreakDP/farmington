import os
import json
import logging
import secrets
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit

from stats import get_user_stats, update_user_stats, get_match_stats_html
from market import MarketManager, assign_market_farmers_to_roles, run_market_matchday
from trading import TradingManager
from chat import ChatManager

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "fallback_secret_key_for_development")

# Configure upload settings
UPLOAD_FOLDER = 'static/images/profile_pics'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Add nl2br filter for templates
@app.template_filter('nl2br')
def nl2br_filter(text):
    """Convert newlines to HTML line breaks"""
    if text is None:
        return ''
    return text.replace('\n', '<br>\n')

# Make get_user_profile available in templates
@app.context_processor
def inject_user_profile():
    return dict(get_user_profile=get_user_profile)

# Initialize managers
market_manager = MarketManager()
trading_manager = TradingManager()
chat_manager = ChatManager()

# Load farmer pool
def load_farmer_pool(league_code=None):
    """Load farmer pool, optionally league-specific"""
    if league_code:
        # Try to load league-specific farmer pool first
        league_pool_file = f"farmer_pool_{league_code}.json"
        try:
            with open(league_pool_file, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            pass  # Fall back to default pool
    
    # Load default farmer pool
    try:
        with open("farmer_pool.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def load_farmer_pool_with_prev_stats(league_code):
    """Load farmer pool and attach previous season stats if available"""
    # Use league-specific farmer pool if available, otherwise default
    farmers = load_farmer_pool(league_code)

    # Try to load previous season stats for this league
    prev_stats_file = f"previous_szn_stats_{league_code}.json"
    prev_stats = {}

    try:
        with open(prev_stats_file, "r") as f:
            prev_stats = json.load(f)
    except FileNotFoundError:
        pass

    # Attach previous season stats to each farmer
    for farmer in farmers:
        farmer_name = farmer["name"]
        if farmer_name in prev_stats:
            stats = prev_stats[farmer_name]
            farmer["prev_season_stats"] = {
                "total_points": stats.get("total_points", 0),
                "total_injuries": stats.get("total_injuries", 0),
                "games_played": stats.get("games_played", 0),
                "was_drafted": stats.get("was_drafted", False)
            }
        else:
            farmer["prev_season_stats"] = None

    return farmers

FARMER_POOL = load_farmer_pool()  # Default pool for general use

# User management
USERS_FILE = "users.json"

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def get_user_profile(username):
    """Get user profile information including team name, profile picture, and team chant"""
    users = load_users()
    user_data = users.get(username, {})
    return {
        "team_name": user_data.get("team_name", username),
        "profile_pic": user_data.get("profile_pic", None),
        "team_chant": user_data.get("team_chant", None)
    }

def update_user_profile(username, team_name=None, profile_pic=None, team_chant=None):
    """Update user profile information"""
    users = load_users()
    if username not in users:
        return False

    if team_name is not None:
        users[username]["team_name"] = team_name
    if profile_pic is not None:
        users[username]["profile_pic"] = profile_pic
    if team_chant is not None:
        users[username]["team_chant"] = team_chant

    save_users(users)
    return True

# League management
LEAGUES_FILE = "leagues.json"

def load_leagues():
    if not os.path.exists(LEAGUES_FILE):
        return {}
    with open(LEAGUES_FILE, "r") as f:
        return json.load(f)

def save_leagues(leagues):
    with open(LEAGUES_FILE, "w") as f:
        json.dump(leagues, f, indent=4)

def get_user_league(username):
    leagues = load_leagues()
    for code, league in leagues.items():
        if username in league["players"]:
            return league
    return None

# Market management (league-specific)
def initialize_league_market(league_code):
    """Initialize the market for a specific league."""
    market_file = f"market_{league_code}.json"
    if not os.path.exists(market_file):
        # Create an empty market file for the league
        with open(market_file, "w") as f:
            json.dump([], f)

def get_league_market_data(league_code):
    """Load market data for a specific league."""
    market_file = f"market_{league_code}.json"
    try:
        with open(market_file, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_league_market_data(league_code, data):
    """Save market data for a specific league."""
    market_file = f"market_{league_code}.json"
    with open(market_file, "w") as f:
        json.dump(data, f, indent=4)

def reset_league_market(league_code):
    """Reset the market for a specific league (empty the file)."""
    market_file = f"market_{league_code}.json"
    if os.path.exists(market_file):
        os.remove(market_file)
        logging.info(f"Market reset for league {league_code}")

# Scheduler for automated matchdays
scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def get_current_matchup(username, league):
    """Get the current opponent for a user in a playoff league"""
    if not league.get("use_playoffs", True):
        return None

    players = league.get("players", [])
    if len(players) < 2:
        return None

    # Use global matchday to determine which 3-game cycle we're in
    global_matchday = get_global_matchday()

    # Each matchup lasts 3 matchdays
    cycle = global_matchday // 3

    if username not in players:
        return None

    matchdays_limit = league.get("matchdays", 30)
    bracket_creation_point = matchdays_limit // 2

    # Check if we're in the bracket phase
    if global_matchday >= bracket_creation_point and league.get("brackets_created", False):
        # Use bracket schedules
        brackets = league.get("playoff_brackets", {})
        bracket_schedules = league.get("bracket_schedules", {})

        # Find which bracket the player is in
        player_bracket = None
        if username in brackets.get("winners", []):
            player_bracket = "winners"
        elif username in brackets.get("losers", []):
            player_bracket = "losers"

        if player_bracket and player_bracket in bracket_schedules:
            bracket_schedule = bracket_schedules[player_bracket]
            if username in bracket_schedule:
                # Adjust cycle index for bracket phase
                bracket_cycle = cycle - (bracket_creation_point // 3)
                if bracket_cycle >= 0 and bracket_cycle < len(bracket_schedule[username]):
                    return bracket_schedule[username][bracket_cycle]
    else:
        # Use regular pre-bracket schedule
        # Get or initialize matchup schedule for this league
        if "matchup_schedule" not in league:
            league["matchup_schedule"] = generate_matchup_schedule(league)
            # Save the updated league data
            leagues = load_leagues()
            leagues[league["code"]] = league
            save_leagues(leagues)

        # Find the opponent for this user and cycle
        schedule = league["matchup_schedule"]
        if username in schedule and cycle < len(schedule[username]):
            return schedule[username][cycle]

    return None

def generate_matchup_schedule(league):
    """Generate a round-robin matchup schedule ensuring proper rotation and bye weeks"""
    players = league.get("players", [])
    matchdays_limit = league.get("matchdays", 30)
    total_cycles = matchdays_limit // 3

    if len(players) < 2:
        return {players[0]: [None] * total_cycles} if players else {}

    schedule = {}
    for player in players:
        schedule[player] = []

    # Create round-robin rotation
    import random
    random.seed(hash(league["code"]))  # Consistent seeding based on league

    # If odd number of players, one player gets a bye each cycle
    has_bye = len(players) % 2 == 1

    for cycle in range(total_cycles):
        # Create matchups for this cycle
        available_players = players.copy()
        cycle_matchups = []

        # If odd number of players, one gets bye
        if has_bye:
            bye_player = available_players[cycle % len(available_players)]
            available_players.remove(bye_player)
            schedule[bye_player].append(None)  # Bye week

        # Pair up remaining players
        while len(available_players) >= 2:
            # For round-robin, use deterministic pairing based on cycle
            if cycle == 0:
                # First cycle: pair in order
                player1 = available_players.pop(0)
                player2 = available_players.pop(0)
            else:
                # Subsequent cycles: rotate pairings
                player1 = available_players.pop(0)
                # Pick opponent based on cycle to ensure variety
                opponent_index = cycle % len(available_players) if available_players else 0
                if opponent_index >= len(available_players):
                    opponent_index = 0
                player2 = available_players.pop(opponent_index)

            # Add mutual matchup
            cycle_matchups.append((player1, player2))

        # Assign the matchups
        for player1, player2 in cycle_matchups:
            schedule[player1].append(player2)
            schedule[player2].append(player1)

    return schedule

def get_matchup_progress(username, league):
    """Get progress in current 3-game matchup"""
    if not league.get("use_playoffs", True):
        return None

    # Use global matchday to determine progress
    global_matchday = get_global_matchday()

    # Determine progress in current 3-game cycle
    games_in_cycle = global_matchday % 3
    return {
        "games_played": games_in_cycle,
        "games_remaining": 3 - games_in_cycle if games_in_cycle > 0 else 3
    }

def create_playoff_brackets(league_code):
    """Create playoff brackets after half the matchdays are completed"""
    leagues = load_leagues()
    if league_code not in leagues:
        return

    league = leagues[league_code]
    if not league.get("use_playoffs", True):
        return

    players = league.get("players", [])
    matchdays_limit = league.get("matchdays", 30)
    global_matchday = get_global_matchday()

    # Create brackets after half the season
    bracket_creation_point = matchdays_limit // 2

    if global_matchday >= bracket_creation_point and not league.get("brackets_created", False):
        playoff_records = league.get("playoff_records", {})

        # Sort players by wins (descending), then by total points as tiebreaker
        def get_total_points(username):
            user_data = get_user_stats(username)
            total_points = sum(
                sum(farmer["points_after_catastrophe"] for farmer in day["farmers"])
                for day in user_data.get("data", [])
            )
            return total_points

        sorted_players = sorted(players, key=lambda p: (
            playoff_records.get(p, {"wins": 0})["wins"],
            get_total_points(p)
        ), reverse=True)

        # Split into brackets
        mid_point = len(sorted_players) // 2
        winners_bracket = sorted_players[:mid_point]
        losers_bracket = sorted_players[mid_point:]

        league["playoff_brackets"] = {
            "winners": winners_bracket,
            "losers": losers_bracket
        }
        league["brackets_created"] = True

        # Generate new round-robin schedules for each bracket
        league["bracket_schedules"] = {
            "winners": generate_bracket_schedule(winners_bracket, matchdays_limit - bracket_creation_point),
            "losers": generate_bracket_schedule(losers_bracket, matchdays_limit - bracket_creation_point)
        }

        leagues[league_code] = league
        save_leagues(leagues)

        logging.info(f"Playoff brackets created for league {league_code}")
        logging.info(f"Winners bracket: {winners_bracket}")
        logging.info(f"Losers bracket: {losers_bracket}")

def generate_bracket_schedule(players, remaining_matchdays):
    """Generate round-robin schedule for a bracket"""
    if len(players) < 2:
        return {players[0]: [None] * (remaining_matchdays // 3)} if players else {}

    schedule = {}
    for player in players:
        schedule[player] = []

    total_cycles = remaining_matchdays // 3
    has_bye = len(players) % 2 == 1

    for cycle in range(total_cycles):
        available_players = players.copy()

        # Handle bye week for odd number of players
        if has_bye:
            bye_player = available_players[cycle % len(available_players)]
            available_players.remove(bye_player)
            schedule[bye_player].append(None)

        # Create round-robin pairings
        while len(available_players) >= 2:
            if cycle == 0:
                player1 = available_players.pop(0)
                player2 = available_players.pop(0)
            else:
                player1 = available_players.pop(0)
                opponent_index = cycle % len(available_players) if available_players else 0
                if opponent_index >= len(available_players):
                    opponent_index = 0
                player2 = available_players.pop(opponent_index)

            schedule[player1].append(player2)
            schedule[player2].append(player1)

    return schedule

def update_playoff_records(league_code):
    """Update win/loss/tie records after completing a 3-game matchup"""
    leagues = load_leagues()
    if league_code not in leagues:
        return

    league = leagues[league_code]
    if not league.get("use_playoffs", True):
        return

    players = league.get("players", [])

    # Initialize playoff records for all players
    if "playoff_records" not in league:
        league["playoff_records"] = {}

    for player in players:
        if player not in league["playoff_records"]:
            league["playoff_records"][player] = {"wins": 0, "losses": 0, "ties": 0}

    # Track which matchups have been recorded to avoid duplicates
    if "recorded_matchups" not in league:
        league["recorded_matchups"] = []

    # Create brackets if needed
    create_playoff_brackets(league_code)

    # Reload league data after potential bracket creation
    leagues = load_leagues()
    league = leagues[league_code]

    # Use global matchday for consistency
    global_matchday = get_global_matchday()

    # Only process if we just completed a 3-game cycle
    if global_matchday > 0 and global_matchday % 3 == 0:
        current_cycle = global_matchday // 3 - 1  # The cycle that was just completed (0-indexed)

        # Calculate points for a specific 3-game cycle
        def get_cycle_points(username, cycle_num):
            try:
                user_data = get_user_stats(username)
                all_data = user_data.get("data", [])

                # Get the 3 games from this specific cycle
                start_idx = cycle_num * 3
                end_idx = start_idx + 3
                cycle_data = all_data[start_idx:end_idx] if start_idx < len(all_data) else []

                total_points = 0
                for day_data in cycle_data:
                    for farmer in day_data.get("farmers", []):
                        total_points += farmer.get("points_after_catastrophe", 0)
                return total_points
            except Exception as e:
                print(f"[ERROR] Error getting cycle points for {username}: {e}")
                return 0

        # Process each player for this completed cycle
        processed_matchups = set()

        for player in players:
            try:
                # Get opponent for this cycle using the appropriate schedule
                opponent = None
                matchdays_limit = league.get("matchdays", 30)
                bracket_creation_point = matchdays_limit // 2

                if global_matchday < bracket_creation_point:
                    # Use regular matchup schedule before brackets
                    if "matchup_schedule" in league and player in league["matchup_schedule"]:
                        schedule = league["matchup_schedule"][player]
                        if current_cycle < len(schedule):
                            opponent = schedule[current_cycle]
                else:
                    # Use bracket schedules after bracket creation
                    brackets = league.get("playoff_brackets", {})
                    bracket_schedules = league.get("bracket_schedules", {})

                    # Find which bracket the player is in
                    player_bracket = None
                    if player in brackets.get("winners", []):
                        player_bracket = "winners"
                    elif player in brackets.get("losers", []):
                        player_bracket = "losers"

                    if player_bracket and player_bracket in bracket_schedules:
                        bracket_schedule = bracket_schedules[player_bracket]
                        if player in bracket_schedule:
                            # Adjust cycle index for bracket phase
                            bracket_cycle = current_cycle - (bracket_creation_point // 3)
                            if bracket_cycle >= 0 and bracket_cycle < len(bracket_schedule[player]):
                                opponent = bracket_schedule[player][bracket_cycle]

                # Handle bye week (no opponent)
                if opponent is None:
                    bye_matchup_id = f"{player}_bye_cycle_{current_cycle}"
                    if bye_matchup_id not in league["recorded_matchups"]:
                        league["playoff_records"][player]["wins"] += 1
                        league["recorded_matchups"].append(bye_matchup_id)
                        print(f"[DEBUG] {player} gets bye week win for cycle {current_cycle}")
                    continue

                # Ensure opponent exists in playoff records
                if opponent not in league["playoff_records"]:
                    league["playoff_records"][opponent] = {"wins": 0, "losses": 0, "ties": 0}

                # Create consistent matchup ID (alphabetical order)
                matchup_players = sorted([player, opponent])
                matchup_id = f"{matchup_players[0]}_vs_{matchup_players[1]}_cycle_{current_cycle}"

                # Skip if already processed in this function call
                if matchup_id in processed_matchups:
                    continue

                # Skip if already recorded
                if matchup_id in league["recorded_matchups"]:
                    continue

                # Calculate points for both players
                p1_points = get_cycle_points(player, current_cycle)
                p2_points = get_cycle_points(opponent, current_cycle)

                print(f"[DEBUG] Cycle {current_cycle}: {player} ({p1_points}) vs {opponent} ({p2_points})")

                # Update records - only record once per matchup
                if p1_points > p2_points:
                    league["playoff_records"][player]["wins"] += 1
                    league["playoff_records"][opponent]["losses"] += 1
                    print(f"[DEBUG] {player} wins!")
                elif p2_points > p1_points:
                    league["playoff_records"][opponent]["wins"] += 1
                    league["playoff_records"][player]["losses"] += 1
                    print(f"[DEBUG] {opponent} wins!")
                else:
                    league["playoff_records"][player]["ties"] += 1
                    league["playoff_records"][opponent]["ties"] += 1
                    print(f"[DEBUG] Tie game!")

                # Mark this matchup as recorded
                league["recorded_matchups"].append(matchup_id)
                processed_matchups.add(matchup_id)

            except Exception as e:
                print(f"[ERROR] Error processing playoff records for {player}: {e}")
                continue

    leagues[league_code] = league
    save_leagues(leagues)

def check_and_finish_league(league_code):
    """Check if a league should be finished and handle completion"""
    leagues = load_leagues()
    if league_code not in leagues:
        return

    league = leagues[league_code]
    matchdays_limit = league.get("matchdays", 30)

    # Update playoff records first
    if league.get("use_playoffs", True):
        update_playoff_records(league_code)
        leagues = load_leagues()  # Reload after update
        league = leagues[league_code]

    # Get the highest matchday count from any player in the league
    max_matchday = 0
    league_stats = {}

    for player in league["players"]:
        user_data = get_user_stats(player)
        player_matchday = user_data.get("matchday", 0)
        max_matchday = max(max_matchday, player_matchday)

        # Calculate total points for leaderboard
        total_points = 0
        for entry in user_data.get("data", []):
            for farmer in entry["farmers"]:
                total_points += farmer["points_after_catastrophe"]
        league_stats[player] = total_points

    # Check if league should finish
    if max_matchday >= matchdays_limit:
        # Determine winner based on league system
        if league.get("use_playoffs", True):
            # Playoff system: winner has most wins FROM WINNERS BRACKET ONLY
            playoff_records = league.get("playoff_records", {})
            if playoff_records and any(record["wins"] > 0 or record["losses"] > 0 or record["ties"] > 0 for record in playoff_records.values()):
                # Check if brackets have been created
                brackets = league.get("playoff_brackets", {})
                winners_bracket = brackets.get("winners", [])

                if winners_bracket:
                    # Only consider players from the winners bracket for league victory
                    winner = max(winners_bracket, key=lambda x: (
                        playoff_records.get(x, {"wins": 0})["wins"], 
                        league_stats.get(x, 0)  # Tiebreaker: total points
                    ))
                else:
                    # If no brackets created yet, use all players (pre-bracket phase)
                    winner = max(playoff_records.keys(), key=lambda x: (
                        playoff_records[x]["wins"], 
                        league_stats.get(x, 0)  # Tiebreaker: total points
                    ))

                # Sort by wins for final standings with proper bracket priority
                final_standings = []
                brackets = league.get("playoff_brackets", {})
                winners_bracket = brackets.get("winners", [])
                losers_bracket = brackets.get("losers", [])

                # Champion goes first
                champion_entry = None
                for player in league["players"]:
                    player_record = playoff_records.get(player, {"wins": 0, "losses": 0, "ties": 0})
                    entry = (player, league_stats.get(player, 0), player_record)

                    if player == winner:
                        champion_entry = entry
                    else:
                        final_standings.append(entry)

                # Sort remaining players: winners bracket first (by wins desc), then losers bracket (by wins desc)
                def get_sort_key(x):
                    player, points, record = x
                    wins = record["wins"]

                    if player in winners_bracket:
                        # Winners bracket: higher priority (1), then by wins desc, then by points desc
                        return (1, wins, points)
                    elif player in losers_bracket:
                        # Losers bracket: lower priority (0), then by wins desc, then by points desc
                        return (0, wins, points)
                    else:
                        # No bracket (shouldn't happen but handle gracefully)
                        return (0, wins, points)

                final_standings.sort(key=get_sort_key, reverse=True)

                # Insert champion at the beginning
                if champion_entry:
                    final_standings.insert(0, champion_entry)
            else:
                # Fallback to points if no playoff records exist
                winner = max(league_stats.keys(), key=lambda x: league_stats[x]) if league_stats else league["players"][0]
                final_standings = sorted(league_stats.items(), key=lambda x: x[1], reverse=True)
        else:
            # Points system: winner has most points
            winner = max(league_stats.keys(), key=lambda x: league_stats[x]) if league_stats else league["players"][0]
            final_standings = sorted(league_stats.items(), key=lambda x: x[1], reverse=True)

        # Save final standings
        league["status"] = "finished"
        league["final_standings"] = final_standings
        league["winner"] = winner
        league["completion_date"] = datetime.now().isoformat()

        # Archive teams for viewing but reset user's active team
        league["archived_teams"] = {}
        for player in league["players"]:
            user_data = get_user_stats(player)
            user_profile = get_user_profile(player)

            # Archive complete team data with all farmer information
            archived_team = {}
            drafted_team = user_data.get("drafted_team", {})

            for role, farmer_data in drafted_team.items():
                if isinstance(farmer_data, dict):
                    archived_team[role] = {
                        "name": farmer_data.get("name", ""),
                        "strength": farmer_data.get("strength", 5),
                        "handy": farmer_data.get("handy", 5), 
                        "stamina": farmer_data.get("stamina", 5),
                        "physical": farmer_data.get("physical", 5),
                        "image": farmer_data.get("image", ""),
                        "crop_preferences": farmer_data.get("crop_preferences", {})
                    }

            league["archived_teams"][player] = {
                "team": archived_team,
                "final_points": league_stats[player],
                "matchdays_played": user_data.get("matchday", 0),
                "team_name": user_profile["team_name"],
                "profile_pic": user_profile["profile_pic"]
            }

            # Reset user's active team for new leagues
            user_data["drafted_team"] = {}
            user_data["matchday"] = 0
            user_data["data"] = []
            update_user_stats(player, user_data)

        leagues[league_code] = league
        save_leagues(leagues)
        logging.info(f"League {league_code} finished! Winner: {winner}")

        # Reset market for this league
        reset_league_market(league_code)

def get_global_matchday():
    """Get the current global matchday number"""
    try:
        with open("global_matchday.json", "r") as f:
            data = json.load(f)
            return data.get("current_matchday", 0)
    except FileNotFoundError:
        return 0

def set_global_matchday(matchday):
    """Set the current global matchday number"""
    with open("global_matchday.json", "w") as f:
        json.dump({"current_matchday": matchday}, f, indent=4)

def run_automated_matchday():
    """Run matchday for all users with complete teams"""
    try:
        logging.info("Running automated matchday...")

        # Get current global matchday
        global_matchday = get_global_matchday()

        # Run market farmers first
        assign_market_farmers_to_roles()
        run_market_matchday()

        # Get all active leagues
        leagues = load_leagues()
        active_leagues = {}

        for league_code, league in leagues.items():
            if not league.get("status") == "finished" and league.get("draft_complete"):
                active_leagues[league_code] = league

        # Run matchdays for all players in active leagues
        players_processed = set()

        for league_code, league in active_leagues.items():
            # Check if league has reached its matchday limit
            if global_matchday >= league.get("matchdays", 30):
                continue

            for username in league["players"]:
                if username in players_processed:
                    continue

                user_data = get_user_stats(username)
                drafted_team = user_data.get("drafted_team", {})
                
                # Check if all required roles are filled
                required_roles = {"Fix Meiser", "Speed Runner", "Lift Tender"}
                has_complete_team = all(
                    role in drafted_team and 
                    isinstance(drafted_team[role], dict) and 
                    drafted_team[role].get("name")
                    for role in required_roles
                )
                
                if drafted_team and has_complete_team:
                    try:
                        # Set user's matchday to global matchday before running
                        user_data["matchday"] = global_matchday
                        update_user_stats(username, user_data)

                        subprocess.run(["python", "core.py", username], check=True)
                        logging.info(f"Completed matchday for {username}")
                        players_processed.add(username)

                    except Exception as e:
                        logging.error(f"Error running matchday for {username}: {e}")

        # Only increment global matchday if players actually completed matchdays
        if players_processed:
            set_global_matchday(global_matchday + 1)
            logging.info(f"Automated matchday completed - Global matchday is now {global_matchday + 1}")

            # Check playoff records and league completion after all players have completed the matchday
            for league_code, league in active_leagues.items():
                if league.get("use_playoffs", True):
                    update_playoff_records(league_code)
                check_and_finish_league(league_code)
        else:
            logging.info("No players processed matchdays - global matchday unchanged")
    except Exception as e:
        logging.error(f"Error in automated matchday: {e}")

# Schedule matchday every 2 minutes
scheduler.add_job(
    func=run_automated_matchday,
    trigger=IntervalTrigger(seconds=120),
    id='automated_matchday',
    name='Run matchday every 2 minutes',
    replace_existing=True
)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        users = load_users()
        if username in users and check_password_hash(users[username]["password"], password):
            session["user"] = username
            flash("Login successful!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        users = load_users()
        if username in users:
            flash("Username already exists.", "danger")
        else:
            users[username] = {
                "password": generate_password_hash(password),
                "theme": "light"
            }
            save_users(users)
            session["user"] = username
            flash("Registration successful!", "success")
            return redirect(url_for("index"))

    return render_template("register.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    if request.method == "POST":
        team_name = request.form.get("team_name", "").strip()
        team_chant = request.form.get("team_chant", "").strip()

        # Handle profile picture upload
        profile_pic_filename = None
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{username}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                profile_pic_filename = filename

        # Update profile
        if team_name:
            update_user_profile(username, team_name=team_name)
        if team_chant or team_chant == "":  # Allow clearing the chant
            update_user_profile(username, team_chant=team_chant)
        if profile_pic_filename:
            update_user_profile(username, profile_pic=profile_pic_filename)

        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))

    user_profile = get_user_profile(username)
    return render_template("profile.html", username=username, user_profile=user_profile)

@app.route("/")
def index():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    tab = request.args.get("tab", "stats")

    # Get user stats
    user_data = get_user_stats(username)
    stats_html = get_match_stats_html(username)

    # Get story data and match history
    story_data = {}
    match_history = []
    try:
        with open("story.json", "r") as f:
            all_stories = json.load(f)
            story_data = all_stories.get(username, {})
    except:
        pass

    # Get match history from user stats
    try:
        match_history = user_data.get("data", [])[-10:]  # Last 10 matches
        match_history.reverse()  # Most recent first
    except:
        match_history = []

    # Get current league
    current_league = get_user_league(username)

    # Reload league data to ensure we have latest playoff records
    if current_league:
        leagues = load_leagues()
        current_league = leagues.get(current_league["code"], current_league)

    # Get leaderboard data
    from stats import load_stats
    all_stats = load_stats()

    # Create comprehensive leaderboard
    global_leaderboard = []
    league_leaderboard = []

    for user, data in all_stats["users"].items():
        total = sum(
            sum(farmer["points_after_catastrophe"] for farmer in day["farmers"])
            for day in data.get("data", [])
        )

        user_profile = get_user_profile(user)
        user_entry = {
            "username": user, 
            "team_name": user_profile["team_name"],
            "profile_pic": user_profile["profile_pic"],
            "total_points": total,
            "is_current_user": user == username
        }

        global_leaderboard.append(user_entry)

        # Add to league leaderboard if in same league
        if current_league and user in current_league.get("players", []):
            # Add playoff records if it's a playoff league
            if current_league.get("use_playoffs", True):
                # Reload current league to get latest playoff records
                leagues = load_leagues()
                updated_league = leagues.get(current_league["code"], current_league)
                playoff_records = updated_league.get("playoff_records", {})

                # Ensure playoff records exist for this user
                if user not in playoff_records:
                    playoff_records[user] = {"wins": 0, "losses": 0, "ties": 0}
                    # Update the league data
                    updated_league["playoff_records"] = playoff_records
                    leagues[current_league["code"]] = updated_league
                    save_leagues(leagues)

                user_record = playoff_records.get(user, {"wins": 0, "losses": 0, "ties": 0})
                user_entry.update({
                    "wins": user_record["wins"],
                    "losses": user_record["losses"],
                    "ties": user_record["ties"]
                })
            league_leaderboard.append(user_entry)

    global_leaderboard.sort(key=lambda x: x["total_points"], reverse=True)

    # Sort league leaderboard based on playoff phase and system
    if current_league and current_league.get('use_playoffs', True):
        if current_league.get('brackets_created', False):
            # Playoff season: bracket-based ranking
            brackets = current_league.get("playoff_brackets", {})
            winners_bracket = brackets.get("winners", [])
            losers_bracket = brackets.get("losers", [])

            def get_bracket_sort_key(x):
                # Winners bracket players get priority (higher tier), then sort by wins and points
                if x["username"] in winners_bracket:
                    return (2, x.get("wins", 0), x["total_points"])  # Tier 2 (highest)
                elif x["username"] in losers_bracket:
                    return (1, x.get("wins", 0), x["total_points"])  # Tier 1 (lower)
                else:
                    return (0, x.get("wins", 0), x["total_points"])  # Tier 0 (shouldn't happen)

            league_leaderboard.sort(key=get_bracket_sort_key, reverse=True)
        else:
            # Regular season: sort by wins then points
            league_leaderboard.sort(key=lambda x: (x.get("wins", 0), x["total_points"]), reverse=True)
    else:
        # Points system: sort by total points
        league_leaderboard.sort(key=lambda x: x["total_points"], reverse=True)

    # Get global farmer stats for farmer stats tab
    global_farmer_stats = []
    if tab == 'farmer_stats':
        farmers = []

        # Load crop preferences
        crop_preferences = {}
        try:
            with open("farmer_crop_preferences.json", "r") as f:
                crop_preferences = json.load(f)
        except FileNotFoundError:
            pass

        if os.path.exists("farm_stats.json"):
            with open("farm_stats.json") as f:
                stats = json.load(f)

            farmer_summary = {}
            current_user_team = stats["users"].get(username, {}).get("drafted_team", {})
            current_user_totals = {}

            # Calculate total points for each of your drafted farmers
            for role, info in current_user_team.items():
                if not info:
                    continue
                name = info["name"]
                total = 0
                for match in stats["users"].get(username, {}).get("data", []):
                    for f in match.get("farmers", []):
                        if f.get("name") == name:
                            total += f.get("points_after_catastrophe", 0)
                current_user_totals[role] = total

            for other_user, user_data in stats.get("users", {}).items():
                drafted = user_data.get("drafted_team", {})
                matchdays = user_data.get("data", [])

                for role, info in drafted.items():
                    if not info:
                        continue
                    name = info["name"]

                    if name not in farmer_summary:
                        owner_profile = get_user_profile(other_user)
                        farmer_summary[name] = {
                            "name": name,
                            "owner": other_user,
                            "owner_team_name": owner_profile["team_name"],
                            "role": role,
                            "total_points": 0,
                            "matchdays": 0,
                            "best": 0
                        }

                    for match in matchdays:
                        for f in match.get("farmers", []):
                            if f.get("name") == name:
                                pts = f.get("points_after_catastrophe", 0)
                                farmer_summary[name]["total_points"] += pts
                                farmer_summary[name]["matchdays"] += 1
                                if pts > farmer_summary[name]["best"]:
                                    farmer_summary[name]["best"] = pts

            for f in farmer_summary.values():
                f["average"] = round(f["total_points"] / f["matchdays"], 2) if f["matchdays"] else "-"

                # Calculate point difference vs current user's farmer in same role
                if f["owner"] != username:
                    # Find current user's farmer in the same role
                    user_farmer_points = None
                    for other_name, other_data in farmer_summary.items():
                        if other_data["owner"] == username and other_data["role"] == f["role"]:
                            user_farmer_points = other_data["total_points"]
                            break

                    if user_farmer_points is not None:
                        f["vs_your_role_diff"] = f["total_points"] - user_farmer_points
                    else:
                        f["vs_your_role_diff"] = None
                else:
                    f["vs_your_role_diff"] = None

                # Add crop preferences
                f["crop_preferences"] = crop_preferences.get(f["name"], {})
                farmers.append(f)

        # Add crop preferences to base farmer data for farmer stats
        all_farmers_with_prefs = []
        for farmer in FARMER_POOL:
            farmer_with_prefs = farmer.copy()
            farmer_with_prefs['crop_preferences'] = crop_preferences.get(farmer['name'], {})
            all_farmers_with_prefs.append(farmer_with_prefs)

        return render_template("index.html", tab=tab, username=username, farmers=farmers, all_farmers=all_farmers_with_prefs)

    # Get team data for draft tab
    team_data = None
    current_team = {}
    roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]

    if current_league and current_league.get("draft_complete"):
        # Load team from league data
        team_data = []
        for role, farmer_data in user_data.get("drafted_team", {}).items():
            if isinstance(farmer_data, dict):
                team_data.append(type("Farmer", (), farmer_data)())
                current_team[role] = type("Farmer", (), farmer_data)()

    # Fill empty roles with None instead of creating empty farmers
    for role in roles:
        if role not in current_team:
            current_team[role] = None

    # Get current matchup for user if in playoff league
    current_matchup = None
    matchup_progress = None
    global_matchday = get_global_matchday()

    if current_league and current_league.get("use_playoffs", True) and current_league.get("draft_complete"):
        current_matchup = get_current_matchup(username, current_league)
        matchup_progress = get_matchup_progress(username, current_league)

    # Get latest matchday data for catastrophe display
    latest_matchday_data = None
    if user_data.get("data"):
        latest_matchday_data = user_data["data"][-1]  # Most recent matchday

    return render_template("index.html",
        username=username,
        tab=tab,
        stats_html=stats_html,
        story_message=story_data.get("story_message", "No story available yet."),
        catastrophe_message=story_data.get("catastrophe_message", "No catastrophe reported."),
        miss_days=story_data.get("miss_days", {}),
        match_history=match_history,
        global_leaderboard=global_leaderboard,
        league_leaderboard=league_leaderboard,
        global_farmer_stats=global_farmer_stats,
        current_league=current_league,
        current_matchup=current_matchup,
        matchup_progress=matchup_progress,
        global_matchday=global_matchday,
        team=team_data,
        current_team=current_team,
        roles=roles,
        latest_matchday_data=latest_matchday_data
    )

@app.route("/results")
def results():
    return redirect(url_for("index", tab="results"))

@app.route("/draft", methods=["GET", "POST"])
def draft():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        username = session["user"]
        user_data = get_user_stats(username)
        current_league = get_user_league(username)

        if not current_league or not current_league.get("draft_complete"):
            flash("You must complete the league draft first.", "warning")
            return redirect(url_for("index", tab="leagues"))

        roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]

        # Get all farmers this user drafted
        current_assignments = user_data.get("drafted_team", {})
        all_drafted_farmers = list(current_assignments.values())

        # Create new assignments based on form input
        updated_assignments = {}
        assigned_farmer_names = set()

        for role in roles:
            selected_name = request.form.get(role)
            if selected_name and selected_name != "":  # Only assign if a farmer was actually selected
                # Find the farmer by name in drafted pool
                for farmer in all_drafted_farmers:
                    if farmer["name"] == selected_name:
                        updated_assignments[role] = farmer
                        assigned_farmer_names.add(selected_name)
                        break

        # Check if all drafted farmers are assigned to a role
        all_farmer_names = {farmer["name"] for farmer in all_drafted_farmers}
        unassigned_farmers = all_farmer_names - assigned_farmer_names

        if unassigned_farmers:
            flash(f"All drafted farmers must be assigned to a role. Unassigned: {', '.join(unassigned_farmers)}", "danger")
        else:
            user_data["drafted_team"] = updated_assignments
            update_user_stats(username, user_data)
            flash("Team assignments saved successfully!", "success")

    return redirect(url_for("index", tab="draft"))

@app.route("/leaderboard")
def leaderboard():
    return redirect(url_for("index", tab="leaderboard"))

@app.route("/leagues", methods=["GET", "POST"])
def leagues():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            league_name = request.form.get("league_name")

            code = secrets.token_hex(4).upper()
            leagues = load_leagues()

            leagues[code] = {
                "name": league_name,
                "code": code,
                "host": username,
                "players": [username],
                "season": "summer",  # Default settings
                "matchdays": 30,
                "use_playoffs": True,
                "playoff_cutoff": 6,
                "lock_market_in_playoffs": True,
                "draft_time": None,
                "draft_complete": False,
                "snake_order": [],
                "market_initialized": False,
                "playoff_records": {},
                "recorded_matchups": []
            }

            save_leagues(leagues)
            flash(f"League '{league_name}' created with code: {code}", "success")

        elif action == "join":
            code = request.form.get("league_code")
            leagues = load_leagues()

            if code in leagues:
                league = leagues[code]
                if username not in league["players"]:
                    league["players"].append(username)
                    # Regenerate matchup schedule when new player joins
                    league["matchup_schedule"] = generate_matchup_schedule(league)
                    save_leagues(leagues)
                    flash(f"Joined league: {league['name']}", "success")
                else:
                    flash("You are already in this league!", "warning")
            else:
                flash("Invalid league code!", "danger")

        elif action == "leave":
            leagues = load_leagues()
            for code, league in leagues.items():
                if username in league["players"] and username != league["host"]:
                    league["players"].remove(username)
                    save_leagues(leagues)
                    flash("Left the league.", "info")
                    break

        elif action == "kick":
            kick_user = request.form.get("kick_user")
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                if kick_user in current_league["players"]:
                    current_league["players"].remove(kick_user)
                    save_leagues(leagues)
                    flash(f"Kicked {kick_user} from the league.", "info")

        elif action == "update_settings":
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username and not current_league.get("draft_time"):
                season = request.form.get("season", "summer")
                matchdays = int(request.form.get("matchdays", 30))
                league_system = request.form.get("league_system", "playoff")
                use_playoffs = league_system == "playoff"
                lock_market_in_playoffs = request.form.get("lock_market_in_playoffs") == "on" if use_playoffs else False

                # Update league settings
                current_league["season"] = season
                current_league["matchdays"] = matchdays
                current_league["use_playoffs"] = use_playoffs
                current_league["lock_market_in_playoffs"] = lock_market_in_playoffs

                # Regenerate matchup schedule with new settings
                current_league["matchup_schedule"] = generate_matchup_schedule(current_league)

                leagues[current_league["code"]] = current_league
                save_leagues(leagues)
                flash("League settings updated successfully!", "success")

        elif action == "delete":
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                league_code = current_league["code"]
                players_in_league = current_league["players"]

                # Clean up story data for all players in the league
                try:
                    with open("story.json", "r") as f:
                        story_data = json.load(f)

                    for player in players_in_league:
                        if player in story_data:
                            del story_data[player]

                    with open("story.json", "w") as f:
                        json.dump(story_data, f, indent=4)
                except FileNotFoundError:
                    pass

                # Clean up market stats for all players in the league
                try:
                    with open("market_stats.json", "r") as f:
                        market_stats = json.load(f)

                    # Remove any market farmers that were drafted by players in this league
                    from stats import load_stats
                    all_stats = load_stats()

                    for player in players_in_league:
                        user_data = all_stats["users"].get(player, {})
                        for farmer_data in user_data.get("drafted_team", {}).values():
                            if isinstance(farmer_data, dict) and farmer_data.get("name") in market_stats:
                                del market_stats[farmer_data["name"]]

                    with open("market_stats.json", "w") as f:
                        json.dump(market_stats, f, indent=4)
                except FileNotFoundError:
                    pass

                # Clean up farm stats for all players in the league
                try:
                    from stats import load_stats, save_stats
                    all_stats = load_stats()

                    for player in players_in_league:
                        if player in all_stats["users"]:
                            # Reset player's stats
                            all_stats["users"][player] = {
                                "matchday": 0,
                                "drafted_team": {},
                                "data": []
                            }

                    save_stats(all_stats)
                except Exception as e:
                    logging.error(f"Error cleaning up farm stats: {e}")

                # Clean up league-specific market file
                reset_league_market(league_code)

                # Clean up trade history for all players in the league
                try:
                    with open("trades.json", "r") as f:
                        trades = json.load(f)

                    # Remove trades involving players from this league
                    filtered_trades = []
                    for trade in trades:
                        if trade["from_user"] not in players_in_league and trade["to_user"] not in players_in_league:
                            filtered_trades.append(trade)

                    with open("trades.json", "w") as f:
                        json.dump(filtered_trades, f, indent=4)
                except FileNotFoundError:
                    pass

                # Clean up league chat
                chat_manager.delete_league_chat(league_code)

                # Reset global matchday to 0 when league is deleted
                set_global_matchday(0)

                # Remove the league
                del leagues[league_code]
                save_leagues(leagues)
                flash("League and all associated data deleted successfully.", "info")

        elif action == "set_matchdays":
            matchdays = int(request.form.get("matchdays", 30))
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                current_league["matchdays"] = matchdays
                # Regenerate schedule with new matchday limit
                if "matchup_schedule" in current_league:
                    current_league["matchup_schedule"] = generate_matchup_schedule(current_league)
                save_leagues(leagues)
                flash(f"Season length updated to {matchdays} matchdays.", "success")

        elif action == "update_cutoff":
            cutoff = int(request.form.get("playoff_cutoff", 6))
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                current_league["playoff_cutoff"] = cutoff
                # Regenerate schedule with new cutoff
                current_league["matchup_schedule"] = generate_matchup_schedule(current_league)
                save_leagues(leagues)
                flash(f"Playoff cutoff updated to {cutoff} players.", "success")

        elif action == "play_again":
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username and current_league.get("status") == "finished":
                try:
                    import importlib
                    continue_module = importlib.import_module('continue')
                    success = continue_module.continue_league_new_season(current_league["code"])

                    if success:
                        flash("New season started! Farmer stats have evolved based on previous performance. Ready for a new draft!", "success")
                    else:
                        flash("Error starting new season. Please try again.", "danger")
                except Exception as e:
                    flash(f"Error starting new season: {str(e)}", "danger")
            else:
                flash("Only the league host can start a new season, and the league must be finished.", "danger")



    return redirect(url_for("index", tab="leagues"))

@app.route("/start_league", methods=["POST"])
def start_league():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    current_league = get_user_league(username)

    if current_league and current_league["host"] == username and not current_league.get("draft_time"):
        # Finalize league settings - no more changes allowed after this point
        current_league["settings_locked"] = True

        # Set draft time to 1 minute from now
        draft_time = datetime.now() + timedelta(minutes=1)
        current_league["draft_time"] = draft_time.isoformat()

        # Create snake draft order
        import random
        players = current_league["players"].copy()
        random.shuffle(players)

        # Generate snake pattern (1,2,3,2,1 for 3 players, 5 rounds)
        snake_order = []
        rounds = 5  # Each player picks 5 farmers

        for round_num in range(rounds):
            if round_num % 2 == 0:
                snake_order.extend(players)  # Forward
            else:
                snake_order.extend(reversed(players))  # Reverse

        current_league["snake_order"] = snake_order

        leagues = load_leagues()
        leagues[current_league["code"]] = current_league
        save_leagues(leagues)

        flash("League settings finalized! Draft begins in 1 minute.", "success")

    return redirect(url_for("index", tab="leagues"))

@app.route("/run_matchday", methods=["POST"])
def run_matchday():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league or current_league["host"] != username:
        flash("Only the league host can run matchdays.", "danger")
        return redirect(url_for("index", tab="leagues"))

    if not current_league.get("draft_complete"):
        flash("League draft must be completed before running matchdays.", "warning")
        return redirect(url_for("index", tab="leagues"))

    if current_league.get("status") == "finished":
        flash("This league has already finished.", "warning")
        return redirect(url_for("index", tab="leagues"))

    try:
        # Get current global matchday
        global_matchday = get_global_matchday()

        # Check if league has reached its matchday limit
        if global_matchday >= current_league.get("matchdays", 30):
            flash("This league has reached its matchday limit.", "warning")
            return redirect(url_for("index", tab="leagues"))

        # Run matchday for all players in the league
        matchdays_run = 0
        for player in current_league["players"]:
            user_data = get_user_stats(player)
            drafted_team = user_data.get("drafted_team", {})
            
            # Check if all required roles are filled
            required_roles = {"Fix Meiser", "Speed Runner", "Lift Tender"}
            has_complete_team = all(
                role in drafted_team and 
                isinstance(drafted_team[role], dict) and 
                drafted_team[role].get("name")
                for role in required_roles
            )
            
            if drafted_team and has_complete_team:
                try:
                    # Set user's matchday to global matchday before running
                    user_data["matchday"] = global_matchday
                    update_user_stats(player, user_data)

                    subprocess.run(["python", "core.py", player], check=True)
                    matchdays_run += 1
                    logging.info(f"Completed matchday for {player}")
                except Exception as e:
                    logging.error(f"Error running matchday for {player}: {e}")
                    flash(f"Error running matchday for {player}: {str(e)}", "warning")

        # Only increment global matchday if players actually completed matchdays
        if matchdays_run > 0:
            set_global_matchday(global_matchday + 1)

            # Update playoff records if it's a playoff league
            if current_league.get("use_playoffs", True):
                update_playoff_records(current_league["code"])

            # Check if this completes the league's season
            check_and_finish_league(current_league["code"])

            flash(f"Successfully ran matchday for {matchdays_run} players! Global matchday is now {global_matchday + 1}", "success")
        else:
            flash("No players were ready for matchday.", "warning")

    except Exception as e:
        logging.error(f"Error in manual matchday run: {e}")
        flash(f"Error running matchday: {str(e)}", "danger")

    return redirect(url_for("index", tab="leagues"))

@app.route("/waitingroom")
def waitingroom():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    
    # Get league code from query parameter or auto-detect user's current league
    league_code = request.args.get("league_code")
    
    if not league_code:
        # Auto-detect user's current league
        current_league = get_user_league(username)
        if not current_league:
            flash("You are not in any league!", "danger")
            return redirect(url_for("index", tab="leagues"))
        league_code = current_league["code"]
        league = current_league
    else:
        # Load leagues and check if user is in this league
        leagues = load_leagues()
        league = leagues.get(league_code)

        if not league:
            flash("League not found!", "danger")
            return redirect(url_for("index", tab="leagues"))

        if username not in league.get("players", []):
            flash("You are not in this league!", "danger")
            return redirect(url_for("index", tab="leagues"))

    # Check if draft time is set
    draft_time = league.get("draft_time")
    if not draft_time:
        flash("Draft time not set for this league!", "warning")
        return redirect(url_for("index", tab="leagues"))

    # Load farmer pool with previous season stats
    farmer_pool = load_farmer_pool_with_prev_stats(league_code)

    # Load crop preferences
    try:
        with open("farmer_crop_preferences.json", "r") as f:
            crop_preferences = json.load(f)
    except FileNotFoundError:
        crop_preferences = {}

    # Add crop preferences to farmer data
    for farmer in farmer_pool:
        farmer["crop_preferences"] = crop_preferences.get(farmer["name"], {})

    # Get snake order
    snake_order = league.get("snake_order", [])

    # Get list of users currently viewing (for now, just show all league players)
    viewers = league.get("players", [])

    return render_template("waitingroom.html", 
                         league_code=league_code,
                         draft_time=draft_time,
                         farmer_pool=farmer_pool,
                         snake_order=snake_order,
                         viewers=viewers)

@app.route("/draftroom")
def draftroom():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    
    # Get league code from query parameter or auto-detect user's current league
    league_code = request.args.get("league_code")
    
    if not league_code:
        # Auto-detect user's current league
        current_league = get_user_league(username)
        if not current_league:
            flash("You are not in any league!", "danger")
            return redirect(url_for("index", tab="leagues"))
        league_code = current_league["code"]
        league = current_league
    else:
        # Load leagues and check if user is in this league
        leagues = load_leagues()
        league = leagues.get(league_code)

        if not league:
            flash("League not found!", "danger")
            return redirect(url_for("index", tab="leagues"))

        if username not in league.get("players", []):
            flash("You are not in this league!", "danger")
            return redirect(url_for("index", tab="leagues"))
        
        # Set current_league for use in template rendering
        current_league = league

    # Check if draft timer has finished
    draft_time = league.get("draft_time")
    if not draft_time:
        flash("Draft time not set!", "warning")
        return redirect(url_for("waitingroom", league_code=league_code))
    
    # Convert draft time to datetime and check if it has passed
    from datetime import datetime
    draft_datetime = datetime.fromisoformat(draft_time)
    current_time = datetime.now()
    
    if current_time < draft_datetime:
        flash("Draft hasn't started yet! Please wait for the timer to finish.", "warning")
        return redirect(url_for("waitingroom", league_code=league_code))
    
    # Check if draft is complete
    if league.get("draft_complete", False):
        flash("Draft already completed!", "info")
        return redirect(url_for("index", tab="draft"))

    # Load farmer pool with previous season stats
    farmer_pool = load_farmer_pool_with_prev_stats(league_code)

    # Load crop preferences
    try:
        with open("farmer_crop_preferences.json", "r") as f:
            crop_preferences = json.load(f)
    except FileNotFoundError:
        crop_preferences = {}

    # Add crop preferences to farmer data
    for farmer in farmer_pool:
        farmer["crop_preferences"] = crop_preferences.get(farmer["name"], {})

    # Get draft state
    leagues = load_leagues()
    league = leagues[league_code]

    picks_made = league.get("picks_made", 0)
    snake_order = league.get("snake_order", [])

    if picks_made >= len(snake_order):
        # Draft complete
        league["draft_complete"] = True

        # Initialize the market for the league upon draft completion
        if not league.get("market_initialized"):
            initialize_league_market(league_code)
            league["market_initialized"] = True  # Ensure market is not re-initialized
            save_leagues(leagues)

        save_leagues(leagues)
        flash("Draft completed!", "success")
        return redirect(url_for("index", tab="draft"))

    current_user_turn = snake_order[picks_made] if picks_made < len(snake_order) else None

    # Get picked farmers
    picked_farmers = league.get("picked_farmers", [])
# Add previous season stats to farmer data
    for farmer in farmer_pool:
        farmer["prev_season_stats"] = load_previous_season_stats(league_code, farmer["name"])

    # Available roles for current pick
    roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]

    # Get picked farmers
    picked_farmers = league.get("picked_farmers", [])
    picked_farmer_names = [f["name"] for f in picked_farmers]

    # Get user's current draft
    user_draft = league.get("user_drafts", {}).get(username, {})

    # Available roles for current pick
    available_roles = [role for role in roles if role not in user_draft]

    # Check if user draft is complete
    user_draft_complete = len(user_draft) >= 5

    # Get pick start time
    pick_start_time = league.get("pick_start_time", datetime.now().isoformat())

    # Use league-specific farmer pool if available
    league_farmer_pool = load_farmer_pool(league_code)
    
    # Add previous season stats to farmer data
    for farmer in league_farmer_pool:
        farmer["prev_season_stats"] = load_previous_season_stats(league_code, farmer["name"])

    return render_template("draftroom.html",
        username=username,
        league_code=current_league["code"],
        farmer_pool=league_farmer_pool,
        picked_farmer_names=picked_farmer_names,
        current_user_turn=current_user_turn,
        snake_order=snake_order,
        picks_made=picks_made,
        drafted=user_draft,
        available_roles=available_roles,
        user_draft_complete=user_draft_complete,
        pick_start_time=pick_start_time,
        last_pick_message=league.get("last_pick_message", "")
    )

def load_previous_season_stats(league_code, farmer_name):
    """Load previous season stats for a farmer if available"""
    prev_stats_file = f"previous_szn_stats_{league_code}.json"
    try:
        with open(prev_stats_file, "r") as f:
            prev_stats = json.load(f)
            if farmer_name in prev_stats:
                return prev_stats[farmer_name]
    except FileNotFoundError:
        pass  # No previous season stats file

    return None

@app.route("/submit_pick", methods=["POST"])
def submit_pick():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    farmer_index = int(request.form["farmer_index"])
    league_code = request.form["league_code"]
    selected_role = request.form["selected_role"]

    leagues = load_leagues()
    league = leagues[league_code]

    # Validate it's user's turn
    picks_made = league.get("picks_made", 0)
    snake_order = league.get("snake_order", [])

    if picks_made >= len(snake_order) or snake_order[picks_made] != username:
        flash("It's not your turn!", "danger")
        return redirect(url_for("draftroom"))

    # Use league-specific farmer pool if available
    league_farmer_pool = load_farmer_pool(league_code)

    # Validate farmer not picked
    picked_farmers = league.get("picked_farmers", [])
    farmer = league_farmer_pool[farmer_index]

    if any(f["name"] == farmer["name"] for f in picked_farmers):
        flash("Farmer already picked!", "danger")
        return redirect(url_for("draftroom"))

    # Validate role selection
    user_drafts = league.get("user_drafts", {})
    if username not in user_drafts:
        user_drafts[username] = {}

    if selected_role in user_drafts[username]:
        flash("Role already filled!", "danger")
        return redirect(url_for("draftroom"))

    # Make the pick
    picked_farmers.append(farmer)
    user_drafts[username][selected_role] = farmer
    league["picked_farmers"] = picked_farmers
    league["user_drafts"] = user_drafts
    league["picks_made"] = picks_made + 1
    
    # Reset timer for next player's turn
    league["pick_start_time"] = datetime.now().isoformat()
    league["last_pick_message"] = f"{username} selected {farmer['name']} as {selected_role}"

    # Update user stats with drafted team
    user_data = get_user_stats(username)
    user_data["drafted_team"] = user_drafts[username]
    update_user_stats(username, user_data)

    save_leagues(leagues)

    flash(f"Successfully picked {farmer['name']} as {selected_role}!", "success")
    return redirect(url_for("draftroom"))

@app.route("/skip_turn", methods=["POST"])
def skip_turn():
    if "user" not in session:
        return "Not logged in", 401

    username = session["user"]
    data = request.get_json()
    league_code = data["league_code"]

    leagues = load_leagues()
    league = leagues[league_code]

    # Validate it's user's turn
    picks_made = league.get("picks_made", 0)
    snake_order = league.get("snake_order", [])

    if picks_made >= len(snake_order) or snake_order[picks_made] != username:
        return "Not your turn", 400

    # Skip turn
    league["picks_made"] = picks_made + 1
    
    # Reset timer for next player's turn
    league["pick_start_time"] = datetime.now().isoformat()
    league["last_pick_message"] = f"{username} was skipped for taking too long"

    save_leagues(leagues)
    return "Turn skipped"

@app.route("/market")
def market():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    # Check if user is in a league
    current_league = get_user_league(username)
    if not current_league:
        flash("You must be in an active league to access the Farmers Market.", "warning")
        return redirect(url_for("index", tab="leagues"))

    # Check if draft is complete and market initialized
    if not current_league.get("draft_complete") or not current_league.get("market_initialized"):
        flash("The market is not yet available for this league. Draft must be completed.", "warning")
        return redirect(url_for("index", tab="leagues"))

    # Check if league is in playoff season and market is locked (if setting is enabled)
    if current_league.get("brackets_created", False) and current_league.get("lock_market_in_playoffs", True):
        flash("The Farmers Market is locked during playoff season. No swaps are allowed.", "warning")
        return redirect(url_for("index", tab="leagues"))

    league_code = current_league["code"]

    # Get market data
    market_stats = market_manager.get_market_stats()

    # Get market assignments to show suggested roles
    try:
        with open("market_assignments.json", "r") as f:
            market_assignments = json.load(f)
    except FileNotFoundError:
        market_assignments = {}

    # Get available farmers (not drafted by any user IN THIS LEAGUE)
    from stats import load_stats
    all_stats = load_stats()

    # Use league-specific farmer pool if available
    league_farmer_pool = load_farmer_pool(league_code)

    # Get drafted farmers in the current league
    drafted_farmers = set()
    for player in current_league["players"]:
        user_data = get_user_stats(player)
        for farmer_data in user_data.get("drafted_team", {}).values():
            if isinstance(farmer_data, dict):
                drafted_farmers.add(farmer_data["name"])

    available_farmers = []
    all_avg_points = []

    for farmer in league_farmer_pool:
        if farmer["name"] not in drafted_farmers:
            farmer_stats = market_stats.get(farmer["name"], {})

            # Get suggested role from assignments
            suggested_role = "Unknown"
            if farmer["name"] in market_assignments:
                suggested_role = market_assignments[farmer["name"]]["role"]
            else:
                # Calculate suggested role based on best stat
                stats = {
                    "Fix Meiser": farmer["handy"],
                    "Speed Runner": farmer["stamina"], 
                    "Lift Tender": farmer["strength"]
                }
                suggested_role = max(stats.keys(), key=lambda x: stats[x])

            farmer_with_stats = farmer.copy()
            farmer_with_stats.update({
                "total_points": farmer_stats.get("total_points", 0),
                "matchdays_played": farmer_stats.get("matchdays_played", 0),
                "avg_points": farmer_stats.get("avg_points", 0.0),
                "recent_form": farmer_stats.get("recent_form", []),
                "suggested_role": suggested_role,
                "image": farmer["image"]  # Use actual image from farmer pool
            })

            # Check for flame indicator (5 consecutive performances with points)
            recent_form = farmer_stats.get("recent_form", [])
            farmer_with_stats["is_hot"] = (len(recent_form) == 5 and all(p > 0 for p in recent_form))

            # Calculate trend indicator
            if len(recent_form) >= 3:
                first_half = sum(recent_form[:2]) / 2 if len(recent_form) >= 2 else 0
                second_half = sum(recent_form[-2:]) / 2 if len(recent_form) >= 2 else 0

                if second_half > first_half + 1:
                    farmer_with_stats["trend"] = "hot_streak"
                elif first_half > second_half + 1:
                    farmer_with_stats["trend"] = "cold_streak"
                elif len(recent_form) >= 4 and max(recent_form) - min(recent_form) <= 1:
                    farmer_with_stats["trend"] = "consistent"
                else:
                    farmer_with_stats["trend"] = "volatile"
            else:
                farmer_with_stats["trend"] = "unknown"

            available_farmers.append(farmer_with_stats)
            if farmer_with_stats["avg_points"] > 0:
                all_avg_points.append(farmer_with_stats["avg_points"])

    # Calculate relative performance rating (0-100 based on ranking)
    if all_avg_points:
        max_avg = max(all_avg_points)
        min_avg = min(all_avg_points)
        avg_range = max_avg - min_avg if max_avg > min_avg else 1


        for farmer in available_farmers:
            if farmer["avg_points"] > 0:
                # Relative performance based on position in the pack
                farmer["performance_rating"] = int(((farmer["avg_points"] - min_avg) / avg_range) * 100)
            else:
                farmer["performance_rating"] = 0
    else:
        for farmer in available_farmers:
            farmer["performance_rating"] = 0

    # Sort by recent performance
    available_farmers.sort(key=lambda x: x["avg_points"], reverse=True)

    # Get user's current team for swap functionality
    user_data = get_user_stats(username)
    current_team = user_data.get("drafted_team", {})

    return render_template("market.html",
        username=username,
        available_farmers=available_farmers,
        current_team=current_team
    )

@app.route("/trading")
def trading():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    # Check if user is in a league
    current_league = get_user_league(username)
    if not current_league:
        flash("You must be in an active league to access the Trading Hub.", "warning")
        return redirect(url_for("index", tab="leagues"))

    # Get all users for trading
    from stats import load_stats
    all_stats = load_stats()
    users = list(all_stats["users"].keys())
    users = [u for u in users if u != username]  # Remove current user

    # Get current user's team
    user_data = get_user_stats(username)
    user_team = user_data.get("drafted_team", {})

    # Get trade requests
    incoming_trades = trading_manager.get_incoming_trades(username)
    outgoing_trades = trading_manager.get_outgoing_trades(username)

    return render_template("trading.html",
        username=username,
        users=users,
        user_team=user_team,
        incoming_trades=incoming_trades,
        outgoing_trades=outgoing_trades
    )

@app.route("/propose_trade", methods=["POST"])
def propose_trade():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    target_user = request.form["target_user"]
    offered_farmer_name = request.form["offered_farmer_name"]
    requested_farmer_name = request.form["requested_farmer_name"]
    message = request.form.get("message", "")

    success = trading_manager.propose_trade(
        from_user=username,
        to_user=target_user,
        offered_farmer_name=offered_farmer_name,
        requested_farmer_name=requested_farmer_name,
        message=message
    )

    if success:
        flash("Trade proposal sent!", "success")
    else:
        flash("Error sending trade proposal. Make sure both farmers are available.", "danger")

    return redirect(url_for("trading"))

@app.route("/respond_trade", methods=["POST"])
def respond_trade():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    trade_id = request.form["trade_id"]
    action = request.form["action"]  # "accept" or "reject"

    if action == "accept":
        success = trading_manager.accept_trade(trade_id, username)
        if success:
            flash("Trade accepted and completed!", "success")
        else:
            flash("Error completing trade.", "danger")
    else:
        trading_manager.reject_trade(trade_id)
        flash("Trade rejected.", "info")

    return redirect(url_for("trading"))

@app.route("/view_user_team/<username>")
def view_user_team(username):
    user_data = get_user_stats(username)
    team = user_data.get("drafted_team", {})

    return render_template("user_team.html", username=username, team=team)

@app.route("/view_archived_user_team/<league_code>/<username>")
def view_archived_user_team(league_code, username):
    if "user" not in session:
        return redirect(url_for("login"))

    leagues = load_leagues()
    if league_code not in leagues:
        flash("League not found.", "danger")
        return redirect(url_for("index", tab="leagues"))

    league = leagues[league_code]
    archived_teams = league.get("archived_teams", {})

    if username not in archived_teams:
        flash("Archived team not found.", "danger")
        return redirect(url_for("index", tab="leagues"))

    archived_data = archived_teams[username]
    team = archived_data.get("team", {})

    return render_template("archived_user_team.html", 
                         username=username, 
                         team=team,
                         league=league,
                         archived_data=archived_data)

@app.route("/farmer_profile/<farmer_name>")
def view_farmer_profile(farmer_name):
    if "user" not in session:
        return redirect(url_for("login"))

    # Load crop preferences
    crop_preferences = {}
    try:
        with open("farmer_crop_preferences.json", "r") as f:
            crop_preferences = json.load(f)
    except FileNotFoundError:
        pass

    # Find farmer in the farmer pool
    farmer = None
    for f in FARMER_POOL:
        if f["name"] == farmer_name:
            farmer = f.copy()
            farmer['crop_preferences'] = crop_preferences.get(farmer_name, {})
            break

    if not farmer:
        flash("Farmer not found.", "danger")
        return redirect(url_for("index", tab="farmer_stats"))

    # Get farmer's current stats if they're playing
    farmer_stats = None
    if os.path.exists("farm_stats.json"):
        with open("farm_stats.json") as f:
            stats = json.load(f)

        # Find current owner and role
        for username, user_data in stats.get("users", {}).items():
            drafted = user_data.get("drafted_team", {})
            for role, info in drafted.items():
                if info and info.get("name") == farmer_name:
                    # Calculate performance stats
                    total_points = 0
                    matchdays_played = 0
                    best_performance = 0

                    for match in user_data.get("data", []):
                        for farmer_match in match.get("farmers", []):
                            if farmer_match.get("name") == farmer_name:
                                points = farmer_match.get("points_after_catastrophe", 0)
                                total_points += points
                                matchdays_played += 1
                                if points > best_performance:
                                    best_performance = points

                    # Get user profile for team name
                    user_profile = get_user_profile(username)

                    # Calculate comparison vs user's current farmer in same role
                    current_user = session.get("user")
                    vs_your_role_diff = None
                    if current_user and current_user in stats.get("users", {}):
                        current_user_data = stats["users"][current_user]
                        current_team = current_user_data.get("drafted_team", {})
                        if role in current_team and current_team[role]:
                            # Calculate current user's farmer points in same role
                            user_farmer_points = 0
                            for match in current_user_data.get("data", []):
                                for farmer_match in match.get("farmers", []):
                                    if farmer_match.get("name") == current_team[role]["name"]:
                                        user_farmer_points += farmer_match.get("points_after_catastrophe", 0)
                            vs_your_role_diff = total_points - user_farmer_points

                    farmer_stats = {
                        "owner": username,
                        "owner_team_name": user_profile["team_name"],
                        "role": role,
                        "total_points": total_points,
                        "matchdays": matchdays_played,
                        "average": round(total_points / matchdays_played, 2) if matchdays_played > 0 else 0,
                        "best": best_performance,
                        "vs_your_role_diff": vs_your_role_diff
                    }
                    break
            if farmer_stats:
                break

    return render_template("farmer_profile.html", 
                         farmer=farmer, 
                         farmer_stats=farmer_stats)

@app.route("/farmerstats")
def farmer_stats():
    if not os.path.exists("farm_stats.json"):
        return "No farm_stats.json found."

    with open("farm_stats.json") as f:
        stats = json.load(f)

    farmer_summary = {}

    for username, user_data in stats.get("users", {}).items():
        drafted = user_data.get("drafted_team", {})
        matchdays = user_data.get("data", [])

        # Create mapping of drafted farmer name -> role (we only care about name and owner)
        owned_farmers = {info['name']: username for role, info in drafted.items() if info}

        for match in matchdays:
            for f in match.get("farmers", []):
                name = f.get("name")
                points = f.get("points_after_catastrophe", 0)

                if name not in owned_farmers:
                    continue  # skip farmers that weren't drafted

                if name not in farmer_summary:
                    farmer_summary[name] = {
                        "name": name,
                        "owner": owned_farmers[name],
                        "total_points": 0,
                        "matchdays": 0,
                        "best": 0
                    }

                farmer_summary[name]["total_points"] += points
                farmer_summary[name]["matchdays"] += 1
                if points > farmer_summary[name]["best"]:
                    farmer_summary[name]["best"] = points

    # Compute averages and prepare final list
    farmers = []
    for f in farmer_summary.values():
        if f["matchdays"] > 0:
            f["average"] = round(f["total_points"] / f["matchdays"], 2)
        else:
            f["average"] = "-"
        farmers.append(f)

    return render_template("index.html", tab="farmer_stats", farmers=farmer_stats, current_user=session.get("user"))


@app.route("/get_theme")
def get_theme():
    if "user" not in session:
        return jsonify({"theme": "light"})

    username = session["user"]
    users = load_users()
    theme = users.get(username, {}).get("theme", "light")

    return jsonify({"theme": theme})

@app.route("/finalize_draft_unlock", methods=["POST"])
def finalize_draft_unlock():
    if "user" not in session:
        return "Unauthorized", 401
    
    username = session["user"]
    current_league = get_user_league(username)
    
    if not current_league:
        return "League not found", 404
    
    # Mark draft as ready to start
    leagues = load_leagues()
    league_code = current_league["code"]
    leagues[league_code]["draft_ready"] = True
    save_leagues(leagues)
    
    return "OK"

@app.route("/check_draft_ready")
def check_draft_ready():
    if "user" not in session:
        return jsonify({"ready": False})
    
    username = session["user"]
    current_league = get_user_league(username)
    
    if not current_league:
        return jsonify({"ready": False})
    
    # Check if draft time is set and has passed
    draft_time = current_league.get("draft_time")
    if not draft_time:
        return jsonify({"ready": False})
    
    # Convert draft time to datetime and check if it has passed
    from datetime import datetime
    draft_datetime = datetime.fromisoformat(draft_time)
    current_time = datetime.now()
    
    # Draft is ready if the timer has finished
    ready = current_time >= draft_datetime
    
    return jsonify({"ready": ready})

@app.route("/api/current_team")
def api_current_team():
    if "user" not in session:
        return jsonify({}), 401

    username = session["user"]
    user_data = get_user_stats(username)
    current_team = user_data.get("drafted_team", {})

    return jsonify(current_team)

@app.route("/api/user_team/<username>")
def api_user_team(username):
    if "user" not in session:
        return jsonify({}), 401

    user_data = get_user_stats(username)
    current_team = user_data.get("drafted_team", {})
    user_profile = get_user_profile(username)

    # Format team data for easier use in frontend
    team_farmers = []
    for role, farmer in current_team.items():
        if farmer:
            team_farmers.append({
                "name": farmer["name"],
                "role": role,
                "stats": f"STR: {farmer['strength']}, HANDY: {farmer['handy']}, STA: {farmer['stamina']}, PHYS: {farmer['physical']}"
            })

    return jsonify({
        "farmers": team_farmers,
        "team_name": user_profile["team_name"],
        "profile_pic": user_profile["profile_pic"],
        "username": username
    })

@app.route("/api/matchup_points/<username>")
def api_matchup_points(username):
    if "user" not in session:
        return jsonify({"points": 0}), 401

    user_data = get_user_stats(username)
    user_profile = get_user_profile(username)
    global_matchday = get_global_matchday()

    # Calculate which 3-game cycle we're currently in
    current_cycle = global_matchday // 3

    # Get the points for the current 3-game cycle
    all_data = user_data.get("data", [])

    # Calculate start and end indices for current cycle
    cycle_start = current_cycle * 3
    cycle_end = min(cycle_start + 3, len(all_data))

    total_points = 0

    # Sum points from the current 3-game cycle
    for i in range(cycle_start, cycle_end):
        if i < len(all_data):
            day_data = all_data[i]
            for farmer in day_data.get("farmers", []):
                total_points += farmer.get("points_after_catastrophe", 0)

    return jsonify({
        "points": total_points,
        "team_name": user_profile["team_name"],
        "username": username
    })

@app.route("/api/matchup_farmer_breakdown/<username>")
def api_matchup_farmer_breakdown(username):
    if "user" not in session:
        return jsonify({"farmers": []}), 401

    user_data = get_user_stats(username)
    global_matchday = get_global_matchday()

    # Calculate which 3-game cycle we're currently in
    current_cycle = global_matchday // 3

    # Get the data for the current 3-game cycle
    all_data = user_data.get("data", [])

    # Calculate start and end indices for current cycle
    cycle_start = current_cycle * 3
    cycle_end = min(cycle_start + 3, len(all_data))

    farmer_points = {}

    # Sum points by farmer from the current 3-game cycle
    for i in range(cycle_start, cycle_end):
        if i < len(all_data):
            day_data = all_data[i]
            for farmer in day_data.get("farmers", []):
                name = farmer.get("name")
                points = farmer.get("points_after_catastrophe", 0)
                farmer_points[name] = farmer_points.get(name, 0) + points

    # Convert to list format for easier frontend handling
    farmers = [{"name": name, "points": points} for name, points in farmer_points.items()]

    # Sort by points descending
    farmers.sort(key=lambda x: x["points"], reverse=True)

    return jsonify({"farmers": farmers})

@app.route("/api/total_season_points/<username>")
def api_total_season_points(username):
    if "user" not in session:
        return jsonify({"total_points": 0}), 401

    user_data = get_user_stats(username)

    # Calculate total points across all matchdays
    total_points = 0
    all_data = user_data.get("data", [])

    for day_data in all_data:
        for farmer in day_data.get("farmers", []):
            total_points += farmer.get("points_after_catastrophe", 0)

    return jsonify({"total_points": total_points})

@app.route("/api/team_stats_comparison")
def api_team_stats_comparison():
    if "user" not in session:
        return jsonify({}), 401

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league:
        return jsonify({})

    current_matchup = get_current_matchup(username, current_league)

    if not current_matchup:
        return jsonify({})

    # Get team data for both users
    user_data = get_user_stats(username)
    opponent_data = get_user_stats(current_matchup)

    def format_team_data(user_stats):
        team = user_stats.get("drafted_team", {})
        formatted_team = []

        # Only include starting positions
        starting_roles = ["Fix Meiser", "Speed Runner", "Lift Tender"]
        for role in starting_roles:
            if role in team and team[role]:
                farmer = team[role]
                formatted_team.append({
                    "name": farmer.get("name", "Unknown"),
                    "role": role,
                    "strength": farmer.get("strength", 5),
                    "handy": farmer.get("handy", 5),
                    "stamina": farmer.get("stamina", 5),
                    "physical": farmer.get("physical", 5)
                })

        return formatted_team

    user_team = format_team_data(user_data)
    opponent_team = format_team_data(opponent_data)

    return jsonify({
        "user_team": user_team,
        "opponent_team": opponent_team
    })

@app.route("/api/matchup_cycle_progress")
def api_matchup_cycle_progress():
    if "user" not in session:
        return jsonify({}), 401

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league:
        return jsonify({})

    global_matchday = get_global_matchday()

    # Calculate which day of the 3-day cycle we're currently on
    cycle_day = (global_matchday % 3) + 1  # Day 1, 2, or 3 of current cycle

    return jsonify({
        "current_day": cycle_day,
        "global_matchday": global_matchday
    })

@app.route("/api/previous_matchup_results/<username>/<int:cycle>")
def api_previous_matchup_results(username, cycle):
    if "user" not in session:
        return jsonify({}), 401

    current_league = get_user_league(username)
    if not current_league:
        return jsonify({})

    # Get the opponent for the specified cycle
    opponent = None
    matchdays_limit = current_league.get("matchdays", 30)
    bracket_creation_point = matchdays_limit // 2

    # Calculate the global matchday for the start of this cycle
    cycle_start_matchday = cycle * 3

    if cycle_start_matchday < bracket_creation_point:
        # Use regular matchup schedule before brackets
        if "matchup_schedule" in current_league and username in current_league["matchup_schedule"]:
            schedule = current_league["matchup_schedule"][username]
            if cycle < len(schedule):
                opponent = schedule[cycle]
    else:
        # Use bracket schedules after bracket creation
        brackets = current_league.get("playoff_brackets", {})
        bracket_schedules = current_league.get("bracket_schedules", {})

        # Find which bracket the player is in
        player_bracket = None
        if username in brackets.get("winners", []):
            player_bracket = "winners"
        elif username in brackets.get("losers", []):
            player_bracket = "losers"

        if player_bracket and player_bracket in bracket_schedules:
            bracket_schedule = bracket_schedules[player_bracket]
            if username in bracket_schedule:
                # Adjust cycle index for bracket phase
                bracket_cycle = cycle - (bracket_creation_point // 3)
                if bracket_cycle >= 0 and bracket_cycle < len(bracket_schedule[username]):
                    opponent = bracket_schedule[username][bracket_cycle]

    if not opponent:
        return jsonify({"opponent": None})

    # Get team chants for both players
    user_profile = get_user_profile(username)
    opponent_profile = get_user_profile(opponent)

    # Calculate points for both players for the specified cycle
    def get_cycle_points_breakdown(target_username, target_cycle):
        try:
            user_data = get_user_stats(target_username)
            all_data = user_data.get("data", [])

            # Get the 3 games from this specific cycle
            start_idx = target_cycle * 3
            end_idx = start_idx + 3
            cycle_data = all_data[start_idx:end_idx] if start_idx < len(all_data) else []

            total_points = 0
            farmer_breakdown = []

            # Track points by farmer across the 3 days
            farmer_totals = {}

            for day_data in cycle_data:
                for farmer in day_data.get("farmers", []):
                    farmer_name = farmer.get("name")
                    farmer_points = farmer.get("points_after_catastrophe", 0)

                    if farmer_name not in farmer_totals:
                        farmer_totals[farmer_name] = 0
                    farmer_totals[farmer_name] += farmer_points
                    total_points += farmer_points

            # Convert to breakdown format
            for farmer_name, points in farmer_totals.items():
                farmer_breakdown.append({
                    "name": farmer_name,
                    "points": points
                })

            return total_points, farmer_breakdown
        except Exception as e:
            print(f"[ERROR] Error getting cycle points breakdown for {target_username}: {e}")
            return 0, []

    user_points, user_farmers = get_cycle_points_breakdown(username, cycle)
    opponent_points, opponent_farmers = get_cycle_points_breakdown(opponent, cycle)

    return jsonify({
        "opponent": opponent,
        "userPoints": user_points,
        "opponentPoints": opponent_points,
        "userFarmers": user_farmers,
        "opponentFarmers": opponent_farmers,
        "userTeamName": user_profile["team_name"],
        "opponentTeamName": opponent_profile["team_name"],
        "userChant": user_profile["team_chant"],
        "opponentChant": opponent_profile["team_chant"]
    })

@app.route("/swap_farmer", methods=["POST"])
def swap_farmer():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    # Check if user is in a league and if playoffs have started (and market lock is enabled)
    current_league = get_user_league(username)
    if current_league and current_league.get("brackets_created", False) and current_league.get("lock_market_in_playoffs", True):
        flash("The Farmers Market is locked during playoff season. No swaps are allowed.", "warning")
        return redirect(url_for("index", tab="leagues"))

    market_farmer_name = request.form.get("market_farmer")
    current_farmer_role = request.form.get("current_farmer_role")

    if not market_farmer_name or not current_farmer_role:
        flash("Invalid swap request. Please try again.", "danger")
        return redirect(url_for("market"))

    # Get user's current team
    user_data = get_user_stats(username)
    current_team = user_data.get("drafted_team", {})

    # Valid roles for any user team
    valid_roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]
    if current_farmer_role not in valid_roles:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("market"))

    # Get league-specific farmer pool
    current_league = get_user_league(username)
    league_farmer_pool = load_farmer_pool(current_league["code"] if current_league else None)

    # Get the market farmer data from the farmer pool
    market_farmer = next((f for f in league_farmer_pool if f["name"] == market_farmer_name), None)
    if not market_farmer:
        flash("Market farmer not found.", "danger")
        return redirect(url_for("market"))

    # Check if market farmer is actually available (not drafted)
    from stats import load_stats
    all_stats = load_stats()

    drafted_farmers = set()
    for user_stats in all_stats["users"].values():
        for farmer_data in user_stats.get("drafted_team", {}).values():
            if isinstance(farmer_data, dict):
                drafted_farmers.add(farmer_data["name"])

    if market_farmer_name in drafted_farmers:
        flash("This farmer is no longer available.", "danger")
        return redirect(url_for("market"))

    # Perform the swap
    old_farmer = current_team.get(current_farmer_role)

    # Replace/assign the farmer in the user's team
    current_team[current_farmer_role] = {
        "name": market_farmer["name"],
        "strength": market_farmer["strength"],
        "handy": market_farmer["handy"],
        "stamina": market_farmer["stamina"],
        "physical": market_farmer["physical"]
    }

    # Update user stats
    user_data["drafted_team"] = current_team
    update_user_stats(username, user_data)

    # Clear any market stats for the acquired farmer (they're no longer in market)
    market_stats = market_manager.get_market_stats()
    if market_farmer_name in market_stats:
        del market_stats[market_farmer_name]
        market_manager.save_market_stats(market_stats)

    if old_farmer and old_farmer.get('name'):
        flash(f"Successfully swapped {old_farmer['name']} for {market_farmer['name']} in the {current_farmer_role} role!", "success")
    else:
        flash(f"Successfully assigned {market_farmer['name']} to the {current_farmer_role} role!", "success")
    return redirect(url_for("market"))

@app.route("/league_chat")
def league_chat():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league:
        flash("You must be in a league to access the chat.", "warning")
        return redirect(url_for("index", tab="leagues"))

    # Get chat messages
    messages = chat_manager.get_recent_messages(current_league["code"])

    # Get user profiles for message display
    user_profiles = {}
    for player in current_league["players"]:
        user_profiles[player] = get_user_profile(player)

    return render_template("league_chat.html",
        username=username,
        current_league=current_league,
        messages=messages,
        user_profiles=user_profiles
    )

@app.route("/send_chat_message", methods=["POST"])
def send_chat_message():
    if "user" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league:
        return jsonify({"success": False, "error": "Not in a league"}), 400

    message = request.form.get("message", "").strip()
    if not message:
        return jsonify({"success": False, "error": "Empty message"}), 400

    # Add message to chat
    new_message = chat_manager.add_message(current_league["code"], username, message)

    # Get user profile for response
    user_profile = get_user_profile(username)

    return jsonify({
        "success": True,
        "message": {
            "id": new_message["id"],
            "username": username,
            "message": message,
            "timestamp": new_message["timestamp"],
            "user_profile": user_profile
        }
    })

@app.route("/api/chat_messages/<league_code>")
def api_chat_messages(league_code):
    if "user" not in session:
        return jsonify([]), 401

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league or current_league["code"] != league_code:
        return jsonify([]), 403

    messages = chat_manager.get_recent_messages(league_code)

    # Add user profiles to messages
    for message in messages:
        message["user_profile"] = get_user_profile(message["username"])

    return jsonify(messages)

@app.route("/almanac")
def almanac():
    if "user" not in session:
        return redirect(url_for("login"))

    return render_template("almanac.html")

if __name__ == '__main__':
    app.run(debug=False, use_reloader=False)