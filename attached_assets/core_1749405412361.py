import random
import json
import sys
import os
import traceback
from tasks import get_task_for_job
from stats import get_user_stats, update_user_stats

def main():
    if len(sys.argv) < 2:
        raise ValueError("Usage: python core.py <username>")
    username = sys.argv[1]
    print(f"[core.py] Running for user: {username}")

    user_data = get_user_stats(username)
    if not user_data:
        raise ValueError(f"User '{username}' not found in farm_stats.json.")
    if "drafted_team" not in user_data or not user_data["drafted_team"]:
        raise ValueError(f"No drafted team found for user '{username}'. Assign farmers in the My Farm tab.")

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

    normalized_roles = {role.strip().title(): role for role in user_data["drafted_team"]}
    non_bench_roles = {norm for norm in normalized_roles if "bench" not in norm.lower()}

    print(f"[DEBUG] Non-bench drafted roles: {non_bench_roles}")
    print(f"[DEBUG] Required roles: {REQUIRED_ROLES}")

    if not REQUIRED_ROLES.issubset(non_bench_roles):
        raise ValueError(f"Drafted team must include: {', '.join(REQUIRED_ROLES)}.")

    characters = []
    for role, data in user_data["drafted_team"].items():
        if role not in REQUIRED_ROLES:
            continue
        characters.append(Character(
            name     = data["name"],
            job      = role,
            strength = data["strength"],
            handy    = data["handy"],
            stamina  = data["stamina"],
            physical = data["physical"]
        ))

    season = 'summer'
    event_type, event_message, cat_ptloss, affected_farmer = roll_catastrophe(season, characters)

    story_results = []
    injury_loss_map = {}
    injury_flavors = [
        "due to throwing out his back riding the mechanical bull at the local bar",
        "after slipping on a rogue vegetable during lunch break",
        "while doing an overenthusiastic victory dance",
        "because he tried to arm wrestle a gangster cow and lost",
        "blowing out his fat wife's back",
        "after falling off a tractor trying to jack off"
    ]

    for char in characters:
        if char.miss_days > 0:
            msg = f"{char.name} was ready to work, but due to their previous injury they failed and collected no points."
            story_results.append((char.name, [msg]))
            injury_loss_map[char.name] = 0
            char.miss_days -= 1
            continue
        pts, results = char.check_success(characters)
        loss = char.check_injury()
        char.total_points += pts
        injury_loss_map[char.name] = loss
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

    all_stories[username] = {
        "story_message": story_message,
        "catastrophe_message": event_message,
        "miss_days": {c.name: c.miss_days for c in characters}
    }
    with open(STORY_FILE, "w") as sf:
        json.dump(all_stories, sf, indent=4)

    user_data["matchday"] += 1
    user_data["data"].append({
        "matchday": user_data["matchday"],
        "season": season,
        "catastrophe_loss": cat_ptloss,
        "affected_farmer": affected_farmer.name if affected_farmer else None,
        "story_message": story_message,
        "farmers": [
    {
        "name": c.name,
        "job": c.job,
        "points_after_catastrophe": c.total_points,
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
