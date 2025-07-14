from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from stats import get_match_stats_html, get_user_stats, update_user_stats, load_stats, save_stats
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR
import subprocess
import time
from datetime import datetime, timedelta
import sys
import json
import pathlib
import os
import atexit
import string
import random
import traceback

app = Flask(__name__)
app.secret_key = "farmington_secret_key"

USERS_FILE = "users.json"
STORY_FILE = "story.json"
LEAGUES_FILE = "leagues.json"
FARMER_POOL_FILE = "farmer_pool.json"
VIEWERS_FILE = "viewer_log.json"

# ─── User Management ─────────────────────────────────────────────────────────
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def load_leagues():
    if not os.path.exists(LEAGUES_FILE):
        return {}
    with open(LEAGUES_FILE, "r") as f:
        return json.load(f)

def save_leagues(data):
    with open(LEAGUES_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_user_league_and_code(leagues, username):
    for code, league in leagues.items():
        if username in league.get("players", []):
            league_copy = dict(league)
            league_copy["code"] = code
            return code, league_copy
    return None, None

def get_latest_story_data(username):
    try:
        with open(STORY_FILE, "r") as f:
            all_stories = json.load(f)
            return all_stories.get(username, {
                "story_message": "No story found.",
                "catastrophe_message": "",
                "miss_days": {}
            })
    except Exception as e:
        print("Error loading story.json:", e)
        return {
            "story_message": "No story found.",
            "catastrophe_message": "",
            "miss_days": {}
        }

# ─── Matchday Core Execution ─────────────────────────────────────────────────

def run_core_script(username):
    try:
        print(f"🔁 Running matchday for {username}")
        print("[DEBUG] run_core_script() called for user:", username)
        result = subprocess.run(
            [sys.executable, "core.py", username],
            check=True,
            capture_output=True,
            text=True
        )
        print("[OUTPUT]\n", result.stdout)
    except subprocess.CalledProcessError as e:
        print("[ERROR] Exception occurred while running core.py for", username)
        print("---- STDOUT ----")
        print(e.stdout)
        print("---- STDERR ----")
        print(e.stderr)


def scheduled_matchday():
    print("⏰ Global Scheduled: running core.py for all users…")
    try:
        with open("farm_stats.json", "r") as f:
            data = json.load(f)
            usernames = list(data.get("users", {}).keys())
    except Exception as e:
        print("⚠️ Could not read farm_stats.json:", e)
        usernames = []

    for username in usernames:
        print(f"🔁 Running matchday for {username}")
        run_core_script(username)

def job_error_listener(event):
    print("⚠️ APScheduler caught an error in a job.")
    if event.exception:
        print(f"Exception: {event.exception}")

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_matchday, "interval", minutes=1)
scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))

# ─── Draft Pool ──────────────────────────────────────────────────────────────
draft_pool = [
    {"name": "Josh", "strength": 1, "handy": 7, "stamina": 6, "physical": 6},
    {"name": "Zach", "strength": 5, "handy": 4, "stamina": 8, "physical": 8},
    {"name": "Tyler", "strength": 10, "handy": 3, "stamina": 8, "physical": 10},
    {"name": "Tyrone", "strength": 4, "handy": 7, "stamina": 7, "physical": 9},
    {"name": "Luke", "strength": 9, "handy": 3, "stamina": 6, "physical": 7},
    {"name": "Vrock", "strength": 6, "handy": 5, "stamina": 5, "physical": 6},
    {"name": "Jared", "strength": 5, "handy": 8, "stamina": 9, "physical": 8},
    {"name": "Mezzy", "strength": 8, "handy": 9, "stamina": 8, "physical": 8},
]

# ─── Auth Routes ─────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        users = load_users()
        username = request.form["username"]
        password = request.form["password"]
        if username in users and users[username] == password:
            session["user"] = username
            flash(f"Logged in as {username}", "success")
            return redirect(url_for("home"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        users = load_users()
        username = request.form["username"]
        password = request.form["password"]
        if username in users:
            flash("Username already exists.", "danger")
        else:
            users[username] = password
            save_users(users)
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out.", "info")
    return redirect(url_for("login"))

# ─── Game Pages ──────────────────────────────────────────────────────────────
@app.route("/")
def home():
    if "user" not in session:
        return redirect(url_for("login"))
    username = session["user"]
    stats_html = get_match_stats_html(username)
    return render_template("index.html", tab="stats", stats_html=stats_html)

@app.route("/results")
def results():
    if "user" not in session:
        return redirect(url_for("login"))
    username = session["user"]
    story_data = get_latest_story_data(username)
    stats_html = get_match_stats_html(username)
    return render_template(
        "index.html",
        tab="results",
        story_message=story_data["story_message"],
        catastrophe_message=story_data["catastrophe_message"],
        miss_days=story_data["miss_days"],
        stats_html=stats_html
    )

@app.route("/draft", methods=["GET", "POST"])
def draft():
    username = session.get("user")
    if not username:
        return redirect(url_for("login"))

    if not os.path.exists("farm_stats.json"):
        return render_template("index.html", tab="draft", team=None, roles=[])

    with open("farm_stats.json", "r") as f:
        stats = json.load(f)

    if username not in stats["users"] or "drafted_team" not in stats["users"][username]:
        return render_template("index.html", tab="draft", team=None, roles=[])

    drafted_team = stats["users"][username]["drafted_team"]
    all_farmers = list(drafted_team.values())
    roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]

    if request.method == "POST":
        form = request.form.to_dict()
        new_team = {}
        used_farmers = set()

        for role in roles:
            farmer_name = form.get(role)
            for farmer in all_farmers:
                if farmer["name"] == farmer_name and farmer_name not in used_farmers:
                    new_team[role] = farmer
                    used_farmers.add(farmer_name)
                    break

        if len(new_team) == 5:
            stats["users"][username]["drafted_team"] = new_team
            with open("farm_stats.json", "w") as f:
                json.dump(stats, f, indent=2)
            flash("Team roles updated successfully.", "success")
        else:
            flash("Each role must be assigned a unique farmer.", "danger")

        return redirect(url_for("draft"))

    return render_template("index.html", tab="draft", team=all_farmers, roles=roles, current_team=drafted_team)

@app.route("/leaderboard")
def leaderboard():
    try:
        with open("farm_stats.json", "r") as f:
            stats = json.load(f)
            user_entries = stats.get("users", {})
    except Exception as e:
        flash("Could not load leaderboard data.", "danger")
        user_entries = {}

    board = []
    for username, user_data in user_entries.items():
        total_points = sum(
            f.get("points_after_catastrophe", 0)
            for match in user_data.get("data", [])
            for f in match.get("farmers", [])
        )
        board.append({
            "username": username,
            "total_points": total_points,
        })

    board.sort(key=lambda x: x["total_points"], reverse=True)

    return render_template("index.html", tab="leaderboard", leaderboard=board)

@app.route("/user/<username>")
def view_user_team(username):
    try:
        with open("farm_stats.json", "r") as f:
            data = json.load(f)
        team = data["users"].get(username, {}).get("drafted_team", {})
    except Exception:
        team = {}

    return render_template("user_team.html", username=username, team=team)

# ─── Leagues Tab ─────────────────────────────────────────────────────────────
def generate_league_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@app.route("/leagues", methods=["GET", "POST"])
def leagues():
    if "user" not in session:
        return redirect(url_for("login"))
    username = session["user"]
    leagues_data = load_leagues()
    current_league = None

    for code, league in leagues_data.items():
        if username in league["players"]:
            current_league = {**league, "code": code}
            break

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("league_name")
            matchdays = int(request.form.get("matchdays"))
            code = generate_league_code()
            leagues_data[code] = {
                "name": name,
                "host": username,
                "matchdays": matchdays,
                "players": [username]
            }
            save_leagues(leagues_data)
            return redirect(url_for("leagues"))

        elif action == "join":
            code = request.form.get("join_code")
            if code in leagues_data and username not in leagues_data[code]["players"]:
                for l in leagues_data.values():
                    if username in l["players"]:
                        flash("You are already in a league.", "danger")
                        return redirect(url_for("leagues"))
                leagues_data[code]["players"].append(username)
                save_leagues(leagues_data)
            else:
                flash("Invalid code or already joined.", "danger")
            return redirect(url_for("leagues"))

        elif action == "kick":
            kick_user = request.form.get("kick_user")
            code = current_league["code"]
            if username == leagues_data[code]["host"] and kick_user in leagues_data[code]["players"]:
                leagues_data[code]["players"].remove(kick_user)
                save_leagues(leagues_data)
            return redirect(url_for("leagues"))

        elif action == "delete":
            code = current_league["code"]
            if username == leagues_data[code]["host"]:
                leagues_data.pop(code)
                save_leagues(leagues_data)
            return redirect(url_for("leagues"))

        elif action == "set_matchdays":
            code = current_league["code"]
            if username == leagues_data[code]["host"]:
                leagues_data[code]["matchdays"] = int(request.form.get("matchdays"))
                save_leagues(leagues_data)
            return redirect(url_for("leagues"))

        elif action == "leave":
            code = current_league["code"]
            if username != leagues_data[code]["host"]:
                if username in leagues_data[code]["players"]:
                    leagues_data[code]["players"].remove(username)
                    save_leagues(leagues_data)
            return redirect(url_for("leagues"))

    return render_template("index.html", tab="leagues", current_league=current_league, all_leagues=leagues_data)

# ─── Waiting Room + Start League Logic ────────────────────────────────────────

VIEWERS_FILE = "viewer_log.json"
FARMER_POOL_FILE = "farmer_pool.json"

def log_viewer(username, league_code):
    viewers = {}
    if os.path.exists(VIEWERS_FILE):
        with open(VIEWERS_FILE, "r") as f:
            viewers = json.load(f)
    now = time.time()
    viewers[username] = {"time": now, "league": league_code}
    with open(VIEWERS_FILE, "w") as f:
        json.dump(viewers, f, indent=2)

def get_current_viewers(league_code):
    if not os.path.exists(VIEWERS_FILE):
        return []
    with open(VIEWERS_FILE, "r") as f:
        data = json.load(f)
    now = time.time()
    return [user for user, meta in data.items() if now - meta["time"] < 20 and meta["league"] == league_code]

@app.route("/start_league", methods=["POST"])
def start_league():
    if "user" not in session:
        return redirect(url_for("login"))
    username = session["user"]
    leagues_data = load_leagues()

    for code, league in leagues_data.items():
        if username == league.get("host") and "draft_time" not in league:
            draft_time = (datetime.utcnow() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            snake_order = []
            players = league["players"]
            for round_num in range(5):
                order = players if round_num % 2 == 0 else players[::-1]
                snake_order.extend(order)

            # Save all draft details
            league["draft_time"] = draft_time
            league["snake_order"] = snake_order
            league["draft_state"] = {
                "last_pick_message": "",
            }
            league["draft_completion"] = 1  # Mark draft as started
            league["draft_index"] = 0
            leagues_data[code] = league
            save_leagues(leagues_data)

            return redirect(url_for("waitingroom"))

    flash("You are not the host or league already started.", "danger")
    return redirect(url_for("leagues"))


@app.route("/waitingroom")
def waitingroom():
    if "user" not in session:
        return redirect(url_for("login"))
    username = session["user"]
    leagues_data = load_leagues()
    farmer_pool = []
    league_code = None
    current_league = None

    for code, league in leagues_data.items():
        if username in league["players"]:
            league_code = code
            current_league = league
            break

    if not current_league:
        flash("League not found.", "danger")
        return redirect(url_for("leagues"))

    if not os.path.exists(FARMER_POOL_FILE):
        flash("Farmer pool not found.", "danger")
        return redirect(url_for("leagues"))

    with open(FARMER_POOL_FILE, "r") as f:
        farmer_pool = json.load(f)

    players = current_league["players"]
    snake_order = []
    for round_num in range(5):
        order = players if round_num % 2 == 0 else players[::-1]
        snake_order.extend(order)

    log_viewer(username, league_code)

    return render_template("waitingroom.html",
        farmer_pool=farmer_pool,
        snake_order=snake_order,
        draft_time=current_league.get("draft_time"),
        viewers=get_current_viewers(league_code),
        league_code=league_code
    )


@app.route("/waitingroom/viewers")
def waitingroom_viewers():
    if "user" not in session:
        return jsonify([])
    username = session["user"]
    leagues = load_leagues()
    for code, league in leagues.items():
        if username in league["players"]:
            log_viewer(username, code)
            return jsonify(get_current_viewers(code))
    return jsonify([])

# ─── Draft Room ──────────────────────────────────────────────────────────────

def get_snake_order(players, total_rounds=5):
    order = []
    for r in range(total_rounds):
        order += players if r % 2 == 0 else players[::-1]
    return order

@app.route("/unlock_draft", methods=["POST"])
def unlock_draft():
    username = session.get("username")
    leagues = load_leagues()
    league_code, league_data = get_user_league_and_code(leagues, username)

    if league_data:
        league_data["draft_completion"] = 1
        save_leagues(leagues)
        return "Unlocked", 200
    return "Error", 400

@app.route("/finalize_draft_unlock", methods=["POST"])
def finalize_draft_unlock():
    if "user" not in session:
        print("⚠️ finalize_draft_unlock: No session user.")
        return "Unauthorized", 401

    username = session["user"]
    leagues = load_leagues()

    found = False
    for code, league in leagues.items():
        if username in league.get("players", []):
            leagues[code]["draft_completion"] = 0
            save_leagues(leagues)
            print(f"✅ Draft unlocked for league {code}")
            found = True
            break

    if not found:
        print("❌ finalize_draft_unlock: League not found for user.")
        return "League not found", 404

    return "OK", 200


@app.route("/check_draft_ready")
def check_draft_ready():
    if "user" not in session:
        return jsonify({"ready": False})
    username = session["user"]
    leagues = load_leagues()
    _, league = get_user_league_and_code(leagues, username)
    ready = league.get("draft_completion", 0) == 1 if league else False
    return jsonify({"ready": ready})


# Fix session key in draftroom route
@app.route("/draftroom")
def draftroom():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    leagues = load_leagues()
    league_code, league_data = get_user_league_and_code(leagues, username)

    if not league_code or not league_data:
        flash("You are not part of any league.")
        return redirect(url_for("leagues"))

    if league_data.get("draft_completion", 1) != 0:
        return render_template("draft_locked.html")

    farm_stats_file = f"farm_stats.json"
    if not os.path.exists(farm_stats_file):
        with open(farm_stats_file, "w") as f:
            json.dump({"users": {}}, f)

    with open(farm_stats_file, "r") as f:
        farm_stats = json.load(f)


    with open(FARMER_POOL_FILE, "r") as f:
        farmer_pool = json.load(f)

    player_picks = {}
    picked_farmer_names = []
    for user, data in farm_stats["users"].items():
        player_picks[user] = data.get("drafted_team", {})
        picked_farmer_names += [f["name"] for f in data.get("drafted_team", {}).values()]

    snake_order = league_data.get("snake_order", [])
    draft_index = league_data.get("draft_index", 0)

    if draft_index >= len(snake_order):
        draft_state = league_data.setdefault("draft_state", {})
        draft_state["last_pick_message"] = "Drafting complete!"
        return render_template("draftroom.html",
                               username=username,
                               league_code=league_code,
                               farmer_pool=farmer_pool,
                               snake_order=snake_order,
                               current_user_turn="None",
                               player_picks=player_picks,
                               draft_state=draft_state,
                               last_pick_message=draft_state.get("last_pick_message", ""),
                               picked_farmer_names=picked_farmer_names,
                               available_roles=[],
                               user_draft_complete=True,
                               picks_made=draft_index,
                               drafted=player_picks.get(username, {}),
                               pick_start_time=datetime.utcnow().isoformat() + "Z")

    current_user_turn = snake_order[draft_index]
    user_draft_complete = len(player_picks.get(username, {})) >= 5

    drafted_team = player_picks.get(username, {})
    all_roles = ["Fix Meiser", "Speed Runner", "Lift Tender", "Bench 1", "Bench 2"]
    available_roles = [role for role in all_roles if role not in drafted_team]

    draft_state = league_data.setdefault("draft_state", {})
    last_pick_message = draft_state.get("last_pick_message", "")

    # ✅ Ensure pick_start_time is present
    if "pick_start_time" not in league_data:
        league_data["pick_start_time"] = datetime.utcnow().isoformat() + "Z"
        leagues[league_code] = league_data
        save_leagues(leagues)

    return render_template("draftroom.html",
                           username=username,
                           league_code=league_code,
                           farmer_pool=farmer_pool,
                           snake_order=snake_order,
                           current_user_turn=current_user_turn,
                           player_picks=player_picks,
                           draft_state=draft_state,
                           last_pick_message=last_pick_message,
                           picked_farmer_names=picked_farmer_names,
                           available_roles=available_roles,
                           user_draft_complete=user_draft_complete,
                           picks_made=draft_index,
                           drafted=drafted_team,
                           pick_start_time=league_data["pick_start_time"])



@app.route("/complete_draft", methods=["POST"])
def complete_draft():
    username = session.get("username")
    league, code = get_user_league_and_code(username)
    if not league or not code:
        return "Unauthorized", 403

    farm_stats_path = f"farm_stats_{code}.json"
    farm_stats = load_json(farm_stats_path)
    league_players = league.get("players", [])

    all_done = True
    for user in league_players:
        if len(farm_stats.get(user, {}).get("team", {})) < 5:
            all_done = False
            break

    if all_done:
        leagues = load_json(LEAGUES_FILE)
        leagues[code]["draft_completion"] = 1
        save_json(LEAGUES_FILE, leagues)

    return "OK"

@app.route("/submit_pick", methods=["POST"])
def submit_pick():
    username = session.get("user")
    farmer_index = int(request.form["farmer_index"])
    league_code = request.form["league_code"]
    selected_role = request.form.get("selected_role")

    if not username or not selected_role:
        flash("Please choose a role before selecting a farmer.", "danger")
        return redirect(url_for("draftroom"))

    with open("leagues.json", "r") as f:
        leagues = json.load(f)
    with open("farmer_pool.json", "r") as f:
        farmer_pool = json.load(f)
    with open("farm_stats.json", "r") as f:
        farm_data = json.load(f)

    selected_farmer = farmer_pool[farmer_index]
    league = leagues.get(league_code)

    if not league:
        flash("League not found.", "danger")
        return redirect(url_for("draftroom"))

    # Initialize user farm data if needed
    if username not in farm_data["users"]:
        farm_data["users"][username] = {
            "matchday": 0,
            "drafted_team": {},
            "data": [],
            "skipped": 0
        }

    user_team = farm_data["users"][username].setdefault("drafted_team", {})

    # Only allow if role isn't already filled
    if selected_role in user_team:
        flash("You've already selected a farmer for this role.", "warning")
        return redirect(url_for("draftroom"))

    user_team[selected_role] = selected_farmer

    # Advance draft
    league["draft_index"] += 1
    league["pick_start_time"] = datetime.utcnow().isoformat() + "Z"
    league["draft_state"]["last_pick_message"] = f"{username} chose {selected_farmer['name']}"

    # Save changes
    with open("farm_stats.json", "w") as f:
        json.dump(farm_data, f, indent=2)
    with open("leagues.json", "w") as f:
        json.dump(leagues, f, indent=2)

    return redirect(url_for("draftroom"))

@app.route("/skip_turn", methods=["POST"])
def skip_turn():
    if "user" not in session:
        return "Unauthorized", 401

    username = session["user"]
    data = request.get_json()
    league_code = data.get("league_code")

    leagues = load_leagues()
    if league_code not in leagues:
        return "League not found", 404

    league = leagues[league_code]
    snake_order = league.get("snake_order", [])
    draft_index = league.get("draft_index", 0)

    if draft_index >= len(snake_order) or snake_order[draft_index] != username:
        return "Not your turn", 403

    # Load farm stats
    with open("farm_stats.json", "r") as f:
        farm_data = json.load(f)

    if username not in farm_data["users"]:
        farm_data["users"][username] = {"drafted_team": {}, "skipped": 1}
    else:
        if "skipped" not in farm_data["users"][username]:
            farm_data["users"][username]["skipped"] = 1
        else:
            farm_data["users"][username]["skipped"] += 1

    # Move to next turn
    league["draft_index"] += 1
    league["pick_start_time"] = datetime.utcnow().isoformat() + "Z"
    league["draft_state"]["last_pick_message"] = f"{username}'s turn was skipped."

    # Save both files
    leagues[league_code] = league
    save_leagues(leagues)
    with open("farm_stats.json", "w") as f:
        json.dump(farm_data, f, indent=2)

    return "Skipped", 200


# ─── App Runner ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)