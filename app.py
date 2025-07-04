import os
import json
import logging
import secrets
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit

from stats import get_user_stats, update_user_stats, get_match_stats_html
from market import MarketManager, assign_market_farmers_to_roles, run_market_matchday
from trading import TradingManager

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "fallback_secret_key_for_development")

# Add nl2br filter for templates
@app.template_filter('nl2br')
def nl2br_filter(text):
    """Convert newlines to HTML line breaks"""
    if text is None:
        return ''
    return text.replace('\n', '<br>\n')

# Initialize managers
market_manager = MarketManager()
trading_manager = TradingManager()

# Load farmer pool
def load_farmer_pool():
    try:
        with open("farmer_pool.json", "r") as f:
            farmers = json.load(f)

        # Load crop preferences
        try:
            with open("farmer_crop_preferences.json", "r") as f:
                crop_preferences = json.load(f)
        except FileNotFoundError:
            crop_preferences = {}

        # Add crop preferences to each farmer
        for farmer in farmers:
            farmer['crop_preferences'] = crop_preferences.get(farmer['name'], {})

        return farmers
    except FileNotFoundError:
        return []

FARMER_POOL = load_farmer_pool()

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
                # Get opponent for this cycle using the matchup schedule
                opponent = None
                if "matchup_schedule" in league and player in league["matchup_schedule"]:
                    schedule = league["matchup_schedule"][player]
                    if current_cycle < len(schedule):
                        opponent = schedule[current_cycle]

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
            # Playoff system: winner has most wins
            playoff_records = league.get("playoff_records", {})
            if playoff_records and any(record["wins"] > 0 or record["losses"] > 0 or record["ties"] > 0 for record in playoff_records.values()):
                winner = max(playoff_records.keys(), key=lambda x: (
                    playoff_records[x]["wins"], 
                    league_stats.get(x, 0)  # Tiebreaker: total points
                ))
                # Sort by wins for final standings
                final_standings = []
                for player in league["players"]:
                    player_record = playoff_records.get(player, {"wins": 0, "losses": 0, "ties": 0})
                    final_standings.append((
                        player, 
                        league_stats.get(player, 0), 
                        player_record
                    ))
                final_standings.sort(key=lambda x: (x[2]["wins"], x[1]), reverse=True)
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
            league["archived_teams"][player] = {
                "team": user_data.get("drafted_team", {}),
                "final_points": league_stats[player],
                "matchdays_played": user_data.get("matchday", 0)
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
                if user_data.get("drafted_team") and len(user_data["drafted_team"]) >= 3:
                    try:
                        # Set user's matchday to global matchday before running
                        user_data["matchday"] = global_matchday
                        update_user_stats(username, user_data)
                        
                        subprocess.run(["python", "core.py", username], check=True)
                        logging.info(f"Completed matchday for {username}")
                        players_processed.add(username)

                    except Exception as e:
                        logging.error(f"Error running matchday for {username}: {e}")

        # Increment global matchday
        set_global_matchday(global_matchday + 1)
        
        # Check playoff records and league completion after all players have completed the matchday
        for league_code, league in active_leagues.items():
            if league.get("use_playoffs", True):
                update_playoff_records(league_code)
            check_and_finish_league(league_code)

        logging.info(f"Automated matchday completed - Global matchday is now {global_matchday + 1}")
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

        user_entry = {
            "username": user, 
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

    # Sort league leaderboard by wins if playoff system, otherwise by points
    if current_league and current_league.get("use_playoffs", True):
        league_leaderboard.sort(key=lambda x: (x.get("wins", 0), x["total_points"]), reverse=True)
    else:
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
                        farmer_summary[name] = {
                            "name": name,
                            "owner": other_user,
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
                user_same_role_pts = current_user_totals.get(f["role"])
                if user_same_role_pts is not None and f["owner"] != username:
                    f["vs_your_role_diff"] = f["total_points"] - user_same_role_pts
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
        roles=roles
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
            season = request.form.get("season", "summer")
            matchdays = int(request.form.get("matchdays", 30))
            league_system = request.form.get("league_system", "playoff")
            use_playoffs = league_system == "playoff"
            playoff_cutoff = int(request.form.get("playoff_cutoff", 6)) if use_playoffs else 6

            code = secrets.token_hex(4).upper()
            leagues = load_leagues()

            leagues[code] = {
                "name": league_name,
                "code": code,
                "host": username,
                "players": [username],
                "season": season,
                "matchdays": matchdays,
                "use_playoffs": use_playoffs,
                "playoff_cutoff": playoff_cutoff,
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

        elif action == "delete":
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                del leagues[current_league["code"]]
                save_leagues(leagues)
                flash("League deleted successfully.", "info")

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



    return redirect(url_for("index", tab="leagues"))

@app.route("/start_league", methods=["POST"])
def start_league():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    current_league = get_user_league(username)

    if current_league and current_league["host"] == username and not current_league.get("draft_time"):
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

        flash("League started! Draft begins in 1 minute.", "success")

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
            if user_data.get("drafted_team") and len(user_data["drafted_team"]) >= 3:
                try:
                    # Set user's matchday to global matchday before running
                    user_data["matchday"] = global_matchday
                    update_user_stats(username, user_data)
                    
                    subprocess.run(["python", "core.py", player], check=True)
                    matchdays_run += 1
                    logging.info(f"Completed matchday for {player}")
                except Exception as e:
                    logging.error(f"Error running matchday for {player}: {e}")
                    flash(f"Error running matchday for {player}: {str(e)}", "warning")

        # Increment global matchday
        set_global_matchday(global_matchday + 1)

        # Update playoff records if it's a playoff league
        if current_league.get("use_playoffs", True):
            update_playoff_records(current_league["code"])

        # Check if this completes the league's season
        check_and_finish_league(current_league["code"])

        if matchdays_run > 0:
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
    current_league = get_user_league(username)

    if not current_league or not current_league.get("draft_time"):
        flash("No active league or draft not scheduled.", "warning")
        return redirect(url_for("index", tab="leagues"))

    return render_template("waitingroom.html",
        league_code=current_league["code"],
        draft_time=current_league["draft_time"],
        snake_order=current_league.get("snake_order", []),
        farmer_pool=FARMER_POOL,
        viewers=current_league["players"]
    )

@app.route("/draftroom")
def draftroom():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    current_league = get_user_league(username)

    if not current_league:
        return redirect(url_for("index", tab="leagues"))

    # Check if draft time has passed
    if current_league.get("draft_time"):
        draft_time = datetime.fromisoformat(current_league["draft_time"])
        if datetime.now() < draft_time:
            return render_template("draft_locked.html")

    # Get draft state
    leagues = load_leagues()
    league = leagues[current_league["code"]]

    picks_made = league.get("picks_made", 0)
    snake_order = league.get("snake_order", [])

    if picks_made >= len(snake_order):
        # Draft complete
        league["draft_complete"] = True

        # Initialize the market for the league upon draft completion
        if not league.get("market_initialized"):
            initialize_league_market(current_league["code"])
            league["market_initialized"] = True  # Ensure market is not re-initialized
            save_leagues(leagues)

        save_leagues(leagues)
        flash("Draft completed!", "success")
        return redirect(url_for("index", tab="draft"))

    current_user_turn = snake_order[picks_made] if picks_made < len(snake_order) else None

    # Get picked farmers
    picked_farmers = league.get("picked_farmers", [])
    picked_farmer_names = [f["name"] for f in picked_farmers]

    # Get user's current draft
    user_draft = league.get("user_drafts", {}).get(username, {})

    # Available roles for current pick
    roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]
    available_roles = [role for role in roles if role not in user_draft]

    # Check if user draft is complete
    user_draft_complete = len(user_draft) >= 5

    # Get pick start time
    pick_start_time = league.get("pick_start_time", datetime.now().isoformat())

    return render_template("draftroom.html",
        username=username,
        league_code=current_league["code"],
        farmer_pool=FARMER_POOL,
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

    # Validate farmer not picked
    picked_farmers = league.get("picked_farmers", [])
    farmer = FARMER_POOL[farmer_index]

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

    # Get drafted farmers in the current league
    drafted_farmers = set()
    for player in current_league["players"]:
        user_data = get_user_stats(player)
        for farmer_data in user_data.get("drafted_team", {}).values():
            if isinstance(farmer_data, dict):
                drafted_farmers.add(farmer_data["name"])

    available_farmers = []
    all_avg_points = []

    for farmer in FARMER_POOL:
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
                "image": f"https://ui-avatars.com/api/?name={farmer['name'].replace(' ', '+')}&background=28a745&color=fff&size=200"
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
    return "OK"

@app.route("/check_draft_ready")
def check_draft_ready():
    return jsonify({"ready": False})

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

    # Format team data for easier use in frontend
    team_farmers = []
    for role, farmer in current_team.items():
        if farmer:
            team_farmers.append({
                "name": farmer["name"],
                "role": role,
                "stats": f"STR: {farmer['strength']}, HANDY: {farmer['handy']}, STA: {farmer['stamina']}, PHYS: {farmer['physical']}"
            })

    return jsonify(team_farmers)

@app.route("/api/matchup_points/<username>")
def api_matchup_points(username):
    if "user" not in session:
        return jsonify({"points": 0}), 401

    user_data = get_user_stats(username)
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

    return jsonify({"points": total_points})

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

    # Get team stats for both users
    user_data = get_user_stats(username)
    opponent_data = get_user_stats(current_matchup)
    
    def calculate_team_stats(user_stats):
        team = user_stats.get("drafted_team", {})
        total_stats = 0
        
        # Only count starting positions (Fix Meiser, Speed Runner, Lift Tender)
        starting_roles = ["Fix Meiser", "Speed Runner", "Lift Tender"]
        for role in starting_roles:
            if role in team and team[role]:
                farmer = team[role]
                # Sum all 4 stat categories for each starting farmer
                total_stats += farmer.get("strength", 0)
                total_stats += farmer.get("handy", 0)
                total_stats += farmer.get("stamina", 0)
                total_stats += farmer.get("physical", 0)
        
        return total_stats
    
    user_total_stats = calculate_team_stats(user_data)
    opponent_total_stats = calculate_team_stats(opponent_data)
    
    return jsonify({
        "user_total_stats": user_total_stats,
        "opponent_total_stats": opponent_total_stats
    })

@app.route("/swap_farmer", methods=["POST"])
def swap_farmer():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
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

    # Get the market farmer data from the farmer pool
    market_farmer = next((f for f in FARMER_POOL if f["name"] == market_farmer_name), None)
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

if __name__ == '__main__':
    app.run(debug=False, use_reloader=False)