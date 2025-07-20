import random
import json
import sys
import os
import traceback
from tasks import get_task_for_job
from stats import get_user_stats, update_user_stats

def load_seasonal_crops():
    try:
        with open("seasonal_crops.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "summer": ["tomatoes", "corn", "peppers", "cucumbers", "watermelons", "zucchini"],
            "fall": ["pumpkins", "apples", "squash", "sweet_potatoes", "cranberries", "carrots"],
            "winter": ["kale", "brussels_sprouts", "potatoes", "onions", "winter_wheat", "cabbage"],
            "spring": ["lettuce", "radishes", "peas", "strawberries", "spinach", "asparagus"]
        }

def load_farmer_crop_preferences():
    try:
        with open("farmer_crop_preferences.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def main():
    if len(sys.argv) < 2:
        raise ValueError("Usage: python core.py <username>")
    username = sys.argv[1]
    print(f"[core.py] Running for user: {username}")

    user_data = get_user_stats(username)
    if not user_data:
        print(f"[core.py] User '{username}' not found in farm_stats.json.")
        return
    if "drafted_team" not in user_data or not user_data["drafted_team"]:
        print(f"[core.py] No drafted team found for user '{username}'. Skipping matchday.")
        return

    STORY_FILE = "story.json"
    prev_miss = {}
    REQUIRED_ROLES = {"Fix Meiser", "Speed Runner", "Lift Tender"}

    if user_data["data"]:
        last_day = user_data["data"][-1]
        for farmer_rec in last_day["farmers"]:
            prev_miss[farmer_rec["name"]] = farmer_rec.get("miss_days", 0)

    class Character:
        def __init__(self, name, job, strength, handy, stamina, physical):
            self.name = name
            self.job = job
            self.strength = strength
            self.handy = handy
            self.stamina = stamina
            self.physical = physical
            self.total_points = 0
            self.injuries_this_season = 0
            self.injury_points_lost = 0
            self.miss_days = prev_miss.get(name, 0)

        def check_success(self, characters):
            other_names = [c.name for c in characters if c.name != self.name]
            return get_task_for_job(
                self.job,
                self.strength,
                self.handy,
                self.stamina,
                self.name,
                other_names
            )

        def check_injury(self):
            injury_loss = 0
            if random.randint(1, 3) == 3 and random.randint(1, 11) > self.physical:
                injury_loss = random.randint(1, 2)
                self.injuries_this_season += 1
                self.injury_points_lost += injury_loss
                if random.random() < 0.5:
                    self.miss_days = random.randint(1, 2)
                    print(f"⚠️ {self.name} will miss the next {self.miss_days} matchday(s) due to injury.")
            return injury_loss

        def harvest_crops(self, season, daily_crop, task_success, is_injured, catastrophe_level, farmer_preferences):
            # Base crop amount based on task success
            if task_success:
                base_crops = random.randint(30, 50)
                reason = "Task succeeded"
            else:
                base_crops = random.randint(5, 20)
                reason = "Task failed"

            original_base = base_crops

            # Apply preference multiplier
            preferred_crop = farmer_preferences.get(self.name, {}).get(season, "")
            if preferred_crop == daily_crop:
                base_crops = int(base_crops * 1.5)
                preference_note = f"Preferred crop matched ({preferred_crop}), 1.5x bonus applied"
            else:
                preference_note = "No crop preference bonus"

            # Apply injury/catastrophe multipliers
            if catastrophe_level >= 2:
                final_crops = 0
                condition_note = f"Catastrophe level {catastrophe_level} - severe, crop yield is 0"
            elif is_injured or catastrophe_level == 1:
                final_crops = int(base_crops * 0.4)
                if is_injured:
                    condition_note = "Injured - 60% penalty applied"
                else:
                    condition_note = f"Catastrophe level {catastrophe_level} - minor, 60% penalty applied"
            else:
                final_crops = base_crops
                condition_note = "Healthy and no catastrophe - full yield"

            final_crops = max(0, final_crops)

            print(f"[DEBUG] {self.name}: {reason}, base: {original_base} → after preference: {base_crops}. {preference_note}. {condition_note}. Final yield: {final_crops}.")

            return final_crops


    def roll_catastrophe(season, characters):
        event_type = 0
        event_message = ""
        cat_ptloss = 0
        catastrophe_messages = []
        affected_farmer = None
        roll = random.randint(1, 100)

        if roll < 60:
            event_type = 1
            affected_farmer = random.choice(characters)
            cat_ptloss = 1
            event_message = f"Oh no! {affected_farmer.name} got heat stroke and struggled to do their task." if season == "summer" else \
                            f"Brrr! {affected_farmer.name} got frostbite and struggled to do their task." if season == "winter" else \
                            f"Yikes! {affected_farmer.name} overate at Thanksgiving and got gout!" if season == "autumn" else \
                            f"Spooky! {affected_farmer.name} saw a ghost and let their fear affect their work!"
            catastrophe_messages.append(f"⚠️ Catastrophe Type 1: {affected_farmer.name} will lose {cat_ptloss} point(s).")
        elif 80 <= roll < 90:
            event_type = 2
            cat_ptloss = 2
            event_message = {
                "summer": "A devastating drought hit, ruining all crop-related work!",
                "winter": "Frost has set in, making any crop harvesting impossible!",
                "autumn": "A major machine breakdown occurred, making all mechanical work impossible!",
                "spring": "A storm has damaged all machinery, ruining any related tasks!"
            }.get(season, "")
            catastrophe_messages.append(f"⚠️ Catastrophe Type 2: ALL farmers will lose {cat_ptloss} point(s).")
        elif roll >= 90:
            event_type = 3
            event_message = {
                "summer": "A raging wildfire has forced all farmers to evacuate—no work today!",
                "winter": "A blizzard has shut everything down! No work can be done today.",
                "spring": "Massive flooding has covered the fields! Work is impossible.",
                "autumn": "A tornado has swept through, leaving no chance for farm work today!"
            }.get(season, "")
            catastrophe_messages.append("🔥 Catastrophe Type 3: ALL farmers lose ALL their points!")
        else:
            event_type = 0
            event_message = "No catastrophe today!"

        print("\n🚨 Catastrophe Report 🚨")
        for msg in catastrophe_messages:
            print(msg)
        print(f"\n📢 Event: {event_message}")

        return event_type, event_message, cat_ptloss, affected_farmer

    # Load current injury data from story for all farmers
    def get_current_miss_days(farmer_name):
        """Get current miss_days for a farmer from story data"""
        try:
            with open(STORY_FILE, "r") as f:
                all_stories = json.load(f)

            # Check all users' story data for this farmer's injury status
            max_miss_days = 0
            for user_story in all_stories.values():
                miss_days = user_story.get("miss_days", {})
                if farmer_name in miss_days:
                    max_miss_days = max(max_miss_days, miss_days[farmer_name])

            return max_miss_days
        except:
            return prev_miss.get(farmer_name, 0)

    # Get user's league to load the correct farmer pool
    def get_user_league_code(username):
        try:
            with open("leagues.json", "r") as f:
                leagues = json.load(f)
            for code, league in leagues.items():
                if username in league.get("players", []):
                    return code
        except FileNotFoundError:
            pass
        return None

    # Check if previous season stats exist for this league and load appropriate farmer pool
    def load_farmer_pool_for_league(league_code):
        if league_code:
            # First try to load league-specific evolved farmer pool directly
            league_pool_file = f"farmer_pool_{league_code}.json"
            if os.path.exists(league_pool_file):
                try:
                    with open(league_pool_file, "r") as f:
                        print(f"[POOL TRACE] ✅ League code {league_code} farmer pool EXISTS - Loading evolved farmer stats from {league_pool_file}")
                        return json.load(f)
                except FileNotFoundError:
                    print(f"[POOL TRACE] ❌ League farmer pool file exists but couldn't be read for league {league_code}")
                    pass
            else:
                print(f"[POOL TRACE] ❌ League code {league_code} farmer pool NOT FOUND - File {league_pool_file} does not exist")
            
            # If league-specific pool doesn't exist, check if previous stats exist
            prev_stats_file = f"previous_szn_stats_{league_code}.json"
            if os.path.exists(prev_stats_file):
                print(f"[POOL TRACE] ⚠️ Previous season stats found but no evolved farmer pool for league {league_code}")

        # No league-specific pool or league code, use original farmer pool
        try:
            with open("farmer_pool.json", "r") as f:
                if league_code:
                    print(f"[POOL TRACE] 🔄 Defaulting to BASIC farmer pool for league {league_code}")
                else:
                    print(f"[POOL TRACE] 🔄 No league code provided - Using BASIC farmer pool")
                return json.load(f)
        except FileNotFoundError:
            print(f"[POOL TRACE] 🚨 ERROR: Basic farmer pool file not found!")
            return []

    # Get league code and appropriate farmer pool
    league_code = get_user_league_code(username)
    farmer_pool = load_farmer_pool_for_league(league_code)

    # Create a lookup dictionary for farmer stats
    farmer_stats = {}
    for farmer in farmer_pool:
        farmer_stats[farmer["name"]] = farmer

    # Extract farmers from drafted_team dictionary
    characters = []
    drafted_team = user_data["drafted_team"]

    # Check if all required roles are filled
    filled_roles = []
    for role in REQUIRED_ROLES:
        if role in drafted_team and isinstance(drafted_team[role], dict) and drafted_team[role].get("name"):
            filled_roles.append(role)

    if len(filled_roles) < len(REQUIRED_ROLES):
        missing_roles = [role for role in REQUIRED_ROLES if role not in filled_roles]
        print(f"[core.py] User '{username}' has incomplete team. Missing roles: {missing_roles}. Skipping matchday.")
        return

    for role, farmer_data in drafted_team.items():
        if role in REQUIRED_ROLES and isinstance(farmer_data, dict):
            farmer_name = farmer_data["name"]

            # Use stats from the farmer pool (either evolved or original)
            if farmer_name in farmer_stats:
                pool_data = farmer_stats[farmer_name]
                char = Character(
                    name     = farmer_name,
                    job      = role,
                    strength = pool_data["strength"],
                    handy    = pool_data["handy"],
                    stamina  = pool_data["stamina"],
                    physical = pool_data["physical"]
                )
                print(f"[DEBUG] Using farmer pool stats for {farmer_name}: STR={pool_data['strength']}, HANDY={pool_data['handy']}, STA={pool_data['stamina']}, PHYS={pool_data['physical']}")
            else:
                # Farmer not found in pool, use drafted stats as fallback
                print(f"[DEBUG] Farmer not found in pool, using drafted stats for {farmer_name}: STR={farmer_data['strength']}, HANDY={farmer_data['handy']}, STA={farmer_data['stamina']}, PHYS={farmer_data['physical']}")

                char = Character(
                    name     = farmer_name,
                    job      = role,
                    strength = farmer_data["strength"],
                    handy    = farmer_data["handy"],
                    stamina  = farmer_data["stamina"],
                    physical = farmer_data["physical"]
                )

            # Set injury status from global story data
            char.miss_days = get_current_miss_days(farmer_name)
            characters.append(char)

    if len(characters) == 0:
        # No farmers assigned to starting positions
        story_message = "Nobody was assigned to a starting position...are you counting sheep over there?"

        try:
            with open(STORY_FILE, "r") as f:
                all_stories = json.load(f)
        except:
            all_stories = {}

        all_stories[username] = {
            "story_message": story_message,
            "catastrophe_message": "No work could be done today.",
            "miss_days": {}
        }
        with open(STORY_FILE, "w") as sf:
            json.dump(all_stories, sf, indent=4)

        # Add empty matchday data
        user_data["matchday"] += 1
        user_data["data"].append({
            "matchday": user_data["matchday"],
            "season": season,
            "daily_crop": daily_crop,
            "catastrophe_loss": 0,
            "affected_farmer": None,
            "story_message": story_message,
            "farmers": []
        })

        update_user_stats(username, user_data)
        print(f"\n📖 {story_message}")
        return

    # Load seasonal data
    seasonal_crops = load_seasonal_crops()
    farmer_preferences = load_farmer_crop_preferences()

    # Get season from user's league settings
    def get_user_league_season(username):
        try:
            with open("leagues.json", "r") as f:
                leagues = json.load(f)
            for league in leagues.values():
                if username in league.get("players", []):
                    return league.get("season", "summer")
        except FileNotFoundError:
            pass
        return "summer"  # Default fallback

    season = get_user_league_season(username)

    # Select random daily crop from season
    daily_crop = random.choice(seasonal_crops.get(season, ["corn"]))
    print(f"🌾 Today's featured crop: {daily_crop.title()}")

    event_type, event_message, cat_ptloss, affected_farmer = roll_catastrophe(season, characters)

    story_results = []
    injury_loss_map = {}
    injury_flavors = [
        "due to throwing out his back riding the mechanical bull at the local bar",
        "after slipping on a rogue vegetable during lunch break",
        "after a silo fell and crushed his legs",
        "from sucking an infected cow teet",
        "because he tried to arm wrestle a gangster cow and lost",
        "blowing out his fat wife's back",
        "after falling off a tractor trying to jack off"
    ]

    crop_harvest_map = {}
    for char in characters:
        if char.miss_days > 0:
            msg = f"{char.name} was ready to work, but due to their previous injury they failed and collected no points."
            story_results.append((char.name, [msg]))
            injury_loss_map[char.name] = 0
            crop_harvest_map[char.name] = 0
            char.miss_days -= 1
            continue

        pts, results = char.check_success(characters)
        task_success = pts > 0  # Determine if task was successful
        loss = char.check_injury()
        char.total_points += pts
        injury_loss_map[char.name] = loss

        # Calculate crop harvest - use random numbers not task points
        is_injured = loss > 0 or char.miss_days > 0
        crops_harvested = char.harvest_crops(season, daily_crop, task_success, is_injured, 0, farmer_preferences)
        crop_harvest_map[char.name] = crops_harvested

        story_results.append((char.name, results))

    for char in characters:
        final = char.total_points
        if event_type == 1 and char is affected_farmer:
            final -= cat_ptloss
        elif event_type == 2:
            final -= cat_ptloss
        elif event_type == 3:
            final = 0
        final -= injury_loss_map.get(char.name, 0)
        char.total_points = max(0, final)

        # Apply catastrophe effects to crops and add crop points
        crops = crop_harvest_map.get(char.name, 0)
        if event_type == 1 and char is affected_farmer:
            crops = int(crops * 0.4)
        elif event_type >= 2:
            crops = 0

        crop_harvest_map[char.name] = crops
        # Add crop points to total matchday points
        char.total_points += crops

    story_output_lines = []
    for idx, (name, lines) in enumerate(story_results):
        intro = "First," if idx == 0 else "Then," if idx == 1 else "Finally,"
        story_output_lines.append(intro)
        for line in lines:
            story_output_lines.append(line)
        loss = injury_loss_map.get(name, 0)
        if loss:
            flavor = random.choice(injury_flavors)
            story_output_lines.append(f"Due to {flavor}, {name} became injured and will miss {next(c.miss_days for c in characters if c.name == name)} matchday(s).")

    story_output_lines.append("\nYeeeeeeHawww! That's all the news for this matchday. Stay tuned for more Farmington News! YEEEEEHAWWWW!")
    story_message = "\n".join(story_output_lines)

    try:
        with open(STORY_FILE, "r") as f:
            all_stories = json.load(f)
    except:
        all_stories = {}

    # Update story for current user
    all_stories[username] = {
        "story_message": story_message,
        "catastrophe_message": event_message,
        "miss_days": {c.name: c.miss_days for c in characters}
    }

    # Update miss_days for all farmers across all users to maintain consistency
    for user_story in all_stories.values():
        user_miss_days = user_story.get("miss_days", {})
        for char in characters:
            if char.name in user_miss_days:
                user_miss_days[char.name] = char.miss_days

    with open(STORY_FILE, "w") as sf:
        json.dump(all_stories, sf, indent=4)

    user_data["matchday"] += 1
    user_data["data"].append({
        "matchday": user_data["matchday"],
        "season": season,
        "daily_crop": daily_crop,
        "catastrophe_loss": cat_ptloss,
        "affected_farmer": affected_farmer.name if affected_farmer else None,
        "story_message": story_message,
        "farmers": [
            {
                "name": c.name,
                "job": c.job,
                "points_after_catastrophe": c.total_points,
                "crop_points": crop_harvest_map.get(c.name, 0),
                "catastrophe_loss": cat_ptloss if (event_type == 1 and c is affected_farmer) else (cat_ptloss if event_type == 2 else 0),
                "daily_injury_loss": injury_loss_map.get(c.name, 0),
                "injuries_this_season": c.injuries_this_season,
                "injury_points_lost": c.injury_points_lost,
                "miss_days": c.miss_days
            }
            for c in characters
        ]
    })

    # Update season-long injury stats
    total_injuries = sum(c.injuries_this_season for c in characters)
    total_injury_loss = sum(c.injury_points_lost for c in characters)

    user_data["total_injuries"] = user_data.get("total_injuries", 0) + total_injuries
    user_data["total_injury_points_lost"] = user_data.get("total_injury_points_lost", 0) + total_injury_loss

    update_user_stats(username, user_data)

    print("\n--- Points After Catastrophe ---")
    for c in characters:
        print(f"{c.name}: {c.total_points} points")
    total_points = sum(c.total_points for c in characters)
    print(f"\n🌾 Total points earned by all farmers today: {total_points}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n🔥 ERROR in core.py:")
        traceback.print_exc()
        sys.exit(1)