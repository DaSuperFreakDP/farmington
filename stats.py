import json
import os

STATS_FILE = "farm_stats.json"

def load_stats():
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f:
            json.dump({"users": {}}, f)
        return {"users": {}}
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_stats(data):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_user_stats(username):
    data = load_stats()
    return data["users"].get(username, {
        "matchday": 0,
        "drafted_team": {},
        "data": []
    })

def update_user_stats(username, user_stats):
    data = load_stats()
    data["users"][username] = user_stats
    save_stats(data)

def get_global_farmer_stats():
    """Get performance statistics for all farmers across all teams"""
    data = load_stats()
    global_stats = {}

    # Process all users' data
    for username, user_data in data["users"].items():
        for entry in user_data.get("data", []):
            for farmer in entry["farmers"]:
                name = farmer["name"]
                job = farmer["job"]
                points = farmer["points_after_catastrophe"]

                if name not in global_stats:
                    global_stats[name] = {
                        "name": name,
                        "role": job,
                        "team_owner": username,
                        "total_points": 0,
                        "matchdays_played": 0,
                        "best_performance": 0,
                        "performances": []
                    }

                global_stats[name]["total_points"] += points
                global_stats[name]["matchdays_played"] += 1
                global_stats[name]["best_performance"] = max(global_stats[name]["best_performance"], points)
                global_stats[name]["performances"].append(points)

    # Calculate averages and sort
    farmer_list = []
    for farmer_data in global_stats.values():
        if farmer_data["matchdays_played"] > 0:
            farmer_data["avg_points"] = farmer_data["total_points"] / farmer_data["matchdays_played"]
        else:
            farmer_data["avg_points"] = 0
        farmer_list.append(farmer_data)

    # Sort by total points descending
    farmer_list.sort(key=lambda x: x["total_points"], reverse=True)
    return farmer_list

def get_match_stats_html(username):
    user_data = get_user_stats(username)
    stats_data = user_data
    html = ""

    # Total points by farmer
    final_totals = {}
    final_matches = {}
    final_jobs = {}
    for entry in stats_data["data"]:
        for farmer in entry["farmers"]:
            name = farmer["name"]
            job = farmer["job"]
            pts = farmer["points_after_catastrophe"]
            final_totals[name] = final_totals.get(name, 0) + pts
            final_matches[name] = final_matches.get(name, 0) + 1
            final_jobs[name] = job

    html += "<h5>🏆 Total Points by Farmer</h5><ul>"
    sorted_farmers = sorted(final_totals.items(), key=lambda x: x[1], reverse=True)
    for farmer, total_pts in sorted_farmers:
        job = final_jobs.get(farmer, "N/A")
        html += f"<li><strong>{farmer}</strong>: {total_pts} points over {final_matches[farmer]} matchday(s) as {job}</li>"
    html += "</ul>"

    html += "<h4>🌾 Farmington Matchday History</h4><hr>"

    # Averages
    farmer_totals = {}
    farmer_matches = {}
    matchday_avgs = {}

    # First pass to calculate cumulative injuries per matchday
    cumulative_injuries_by_day = {}
    cumulative_injury_points_by_day = {}
    running_injuries = {}
    running_injury_points = {}

    for entry in stats_data["data"]:
        matchday = entry["matchday"]
        cumulative_injuries_by_day[matchday] = {}
        cumulative_injury_points_by_day[matchday] = {}
        for farmer in entry["farmers"]:
            name = farmer["name"]
            inj = farmer.get("injuries_this_season", 0)
            injpts = farmer.get("injury_points_lost", 0)
            running_injuries[name] = running_injuries.get(name, 0) + inj
            running_injury_points[name] = running_injury_points.get(name, 0) + injpts
            cumulative_injuries_by_day[matchday][name] = running_injuries[name]
            cumulative_injury_points_by_day[matchday][name] = running_injury_points[name]

    # Second pass for rendering
    for entry in stats_data["data"]:
        matchday = entry["matchday"]
        matchday_avgs[matchday] = {}
        for farmer in entry["farmers"]:
            name = farmer["name"]
            pts = farmer["points_after_catastrophe"]
            farmer_totals[name] = farmer_totals.get(name, 0) + pts
            farmer_matches[name] = farmer_matches.get(name, 0) + 1
            avg = farmer_totals[name] / farmer_matches[name]
            matchday_avgs[matchday][name] = avg

    for entry in reversed(stats_data["data"]):
        matchday = entry["matchday"]
        season = entry["season"]
        affected = entry["affected_farmer"]
        farmers = entry["farmers"]

        html += f"<h5>Matchday {matchday} — {season.title()}</h5>"
        html += f"<p><strong>Daily Crop:</strong> {entry.get('daily_crop', 'N/A').title()}</p>"
        html += "<table class='table table-sm table-bordered'><thead><tr>"
        html += "<th>Name</th><th>Job</th><th>Total</th><th>Task Pts</th><th>Crop Pts</th><th>CatLoss</th><th>InjLoss</th><th>InjTot</th><th>InjPtTot</th><th>Avg</th></tr></thead><tbody>"

        match_total = 0
        for farmer in farmers:
            name = farmer["name"]
            job = farmer["job"]
            total_pts = farmer["points_after_catastrophe"]
            crop_pts = farmer.get("crop_points", 0)
            task_pts = total_pts - crop_pts  # Calculate task points by subtracting crop points from total
            catlost = farmer.get("catastrophe_loss", 0)
            injlost = farmer.get("daily_injury_loss", 0)

            injtot = cumulative_injuries_by_day[matchday].get(name, 0)
            injptot = cumulative_injury_points_by_day[matchday].get(name, 0)

            avg = matchday_avgs[matchday].get(name, 0.0)
            cat_flag = "ABC" if affected == name else ""

            # Add heart emoji if preferred crop matches daily crop
            heart_emoji = ""
            daily_crop = entry.get('daily_crop', 'N/A').lower()
            
            # Load farmer crop preferences
            try:
                import json
                with open('farmer_crop_preferences.json', 'r') as f:
                    farmer_preferences = json.load(f)
                
                # Get current season from the entry
                current_season = season.lower()
                
                # Check if farmer has a preference for this season that matches daily crop
                if name in farmer_preferences:
                    preferred_crop = farmer_preferences[name].get(current_season, "").lower()
                    if preferred_crop == daily_crop:
                        heart_emoji = " ❤️"
            except:
                pass  # If file doesn't exist or other error, just skip the heart emoji

            html += f"<tr><td>{name}{heart_emoji}</td><td>{job}</td><td>{total_pts}</td><td>{task_pts}</td><td>{crop_pts}</td><td>{catlost} {cat_flag}</td><td>{injlost}</td><td>{injtot}</td><td>{injptot}</td><td>{avg:.2f}</td></tr>"
            match_total += total_pts

        html += "</tbody></table>"
        html += f"<p><strong>Total points this matchday:</strong> {match_total}</p><hr>"

    return html

