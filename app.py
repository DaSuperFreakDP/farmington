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

# Scheduler for automated matchdays
scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def check_and_finish_league(league_code):
    """Check if a league should be finished and handle completion"""
    leagues = load_leagues()
    if league_code not in leagues:
        return

    league = leagues[league_code]
    matchdays_limit = league.get("matchdays", 30)

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
        # Find the winner
        winner = max(league_stats.keys(), key=lambda x: league_stats[x])

        # Save final standings
        league["status"] = "finished"
        league["final_standings"] = sorted(league_stats.items(), key=lambda x: x[1], reverse=True)
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

def run_automated_matchday():
    """Run matchday for all users with complete teams"""
    try:
        logging.info("Running automated matchday...")

        # Run market farmers first
        assign_market_farmers_to_roles()
        run_market_matchday()

        # Run regular user matchdays
        from stats import load_stats
        stats = load_stats()

        for username in stats["users"]:
            user_data = get_user_stats(username)
            if user_data.get("drafted_team") and len(user_data["drafted_team"]) >= 3:
                # Check if user is in a league and if the league has reached its matchday limit
                user_league = get_user_league(username)
                if user_league and user_league.get("status") == "finished":
                    continue  # Skip users in finished leagues

                try:
                    subprocess.run(["python", "core.py", username], check=True)
                    logging.info(f"Completed matchday for {username}")

                    # Check if this completes the league's season
                    if user_league:
                        check_and_finish_league(user_league["code"])

                except Exception as e:
                    logging.error(f"Error running matchday for {username}: {e}")

        logging.info("Automated matchday completed")
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
            league_leaderboard.append(user_entry)

    global_leaderboard.sort(key=lambda x: x["total_points"], reverse=True)
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

    # Fill empty roles
    for role in roles:
        if role not in current_team:
            current_team[role] = type("Farmer", (), {
                "name": "Empty",
                "strength": 0,
                "handy": 0,
                "stamina": 0,
                "physical": 0,
                "image": "static/images/empty_farmer.svg"
            })()

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
        required_roles = ["Fix Meiser", "Speed Runner", "Lift Tender"]

        # Start with the current assignments
        current_assignments = user_data.get("drafted_team", {})
        drafted_farmers = list(current_assignments.values())

        # Create a new team assignment preserving unchanged ones
        updated_assignments = {}

        for role in roles:
            selected_name = request.form.get(role)
            if selected_name:
                # Find the farmer by name in current drafted pool
                for farmer in drafted_farmers:
                    if farmer["name"] == selected_name:
                        updated_assignments[role] = farmer
                        break

        # Fill in any unedited roles from the current assignment
        for role in roles:
            if role not in updated_assignments and role in current_assignments:
                updated_assignments[role] = current_assignments[role]

        # Final validation
        if all(role in updated_assignments for role in required_roles):
            user_data["drafted_team"] = updated_assignments
            update_user_stats(username, user_data)
            flash("Team assignments saved successfully!", "success")
        else:
            flash("You must assign all required roles: Fix Meiser, Speed Runner, Lift Tender", "danger")

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
            matchdays = int(request.form.get("matchdays", 30))

            code = secrets.token_hex(4).upper()
            leagues = load_leagues()

            leagues[code] = {
                "name": league_name,
                "code": code,
                "host": username,
                "players": [username],
                "matchdays": matchdays,
                "draft_time": None,
                "draft_complete": False,
                "snake_order": []
            }

            save_leagues(leagues)
            flash(f"League '{league_name}' created with code: {code}", "success")

        elif action == "join":
            join_code = request.form.get("join_code", "").strip()
            if join_code:
                join_code = join_code.upper()
            leagues = load_leagues()

            if join_code in leagues:
                if username not in leagues[join_code]["players"]:
                    leagues[join_code]["players"].append(username)
                    save_leagues(leagues)
                    flash(f"Joined league: {leagues[join_code]['name']}", "success")
                else:
                    flash("You're already in this league.", "warning")
            else:
                flash("Invalid league code.", "danger")

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
                save_leagues(leagues)
                flash(f"Season length updated to {matchdays} matchdays.", "success")

        elif action == "delete":
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                del leagues[current_league["code"]]
                save_leagues(leagues)
                flash("League deleted.", "info")

        elif action == "set_matchdays":
            matchdays = int(request.form.get("matchdays", 30))
            leagues = load_leagues()
            current_league = get_user_league(username)

            if current_league and current_league["host"] == username:
                current_league["matchdays"] = matchdays
                save_leagues(leagues)
                flash(f"Matchdays updated to {matchdays}.", "success")

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

    # Get market data
    market_stats = market_manager.get_market_stats()

    # Get market assignments to show suggested roles
    try:
        with open("market_assignments.json", "r") as f:
            market_assignments = json.load(f)
    except FileNotFoundError:
        market_assignments = {}

    # Get available farmers (not drafted by any user)
    from stats import load_stats
    all_stats = load_stats()

    drafted_farmers = set()
    for user_data in all_stats["users"].values():
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
    offered_role = request.form["offered_role"]
    requested_role = request.form["requested_role"]
    message = request.form.get("message", "")

    success = trading_manager.propose_trade(
        from_user=username,
        to_user=target_user,
        offered_role=offered_role,
        requested_role=requested_role,
        message=message
    )

    if success:
        flash("Trade proposal sent!", "success")
    else:
        flash("Error sending trade proposal.", "danger")

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

    if current_farmer_role not in current_team:
        flash("Selected role not found in your team.", "danger")
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
    old_farmer = current_team[current_farmer_role]

    # Replace the farmer in the user's team
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

    flash(f"Successfully swapped {old_farmer['name']} for {market_farmer['name']} in the {current_farmer_role} role!", "success")
    return redirect(url_for("market"))

if __name__ == '__main__':
    app.run(debug=False, use_reloader=False)