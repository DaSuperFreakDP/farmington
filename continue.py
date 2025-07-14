
import json
import os
import random
from stats import load_stats, get_user_stats
from market import get_undrafted_farmers

def archive_season_performance(league_code):
    """Archive all farmers' performance data from the completed season"""
    archive_file = f"previous_szn_stats_{league_code}.json"
    
    # Load current season data
    all_stats = load_stats()
    leagues = load_leagues()
    league = leagues.get(league_code)
    
    if not league:
        return False
    
    # Load farmer pool for role assignments
    farmer_pool = load_farmer_pool()
    
    archived_performance = {}
    league_players = league.get("players", [])
    season_length = league.get("matchdays", 30)
    
    # Track all farmers that were drafted in this league
    drafted_farmers = set()
    for player in league_players:
        user_data = get_user_stats(player)
        for farmer_data in user_data.get("drafted_team", {}).values():
            if isinstance(farmer_data, dict):
                drafted_farmers.add(farmer_data["name"])
    
    # Process each farmer in the pool
    for farmer in farmer_pool:
        farmer_name = farmer["name"]
        performance_data = {
            "name": farmer_name,
            "original_stats": {
                "strength": farmer["strength"],
                "handy": farmer["handy"], 
                "stamina": farmer["stamina"],
                "physical": farmer["physical"]
            },
            "games_played": 0,
            "total_points": 0,
            "total_injuries": 0,
            "best_role": determine_best_role(farmer),
            "was_drafted": farmer_name in drafted_farmers,
            "simulated_games": 0
        }
        
        # Collect actual performance data if farmer was drafted
        if farmer_name in drafted_farmers:
            for player in league_players:
                user_data = get_user_stats(player)
                for entry in user_data.get("data", []):
                    for farmer_match in entry.get("farmers", []):
                        if farmer_match.get("name") == farmer_name:
                            performance_data["games_played"] += 1
                            performance_data["total_points"] += farmer_match.get("points_after_catastrophe", 0)
                            performance_data["total_injuries"] += farmer_match.get("injuries_this_season", 0)
        
        # Simulate missing games if farmer was drafted but didn't play full season
        if performance_data["was_drafted"] and performance_data["games_played"] < season_length:
            missing_games = season_length - performance_data["games_played"]
            simulated_performance = simulate_farmer_performance(farmer, missing_games)
            performance_data["total_points"] += simulated_performance["points"]
            performance_data["total_injuries"] += simulated_performance["injuries"]
            performance_data["simulated_games"] = missing_games
        
        # Simulate entire season if farmer was never drafted
        elif not performance_data["was_drafted"]:
            simulated_performance = simulate_farmer_performance(farmer, season_length)
            performance_data["total_points"] = simulated_performance["points"]
            performance_data["total_injuries"] = simulated_performance["injuries"]
            performance_data["games_played"] = season_length
            performance_data["simulated_games"] = season_length
        
        archived_performance[farmer_name] = performance_data
    
    # Save archived performance
    with open(archive_file, "w") as f:
        json.dump(archived_performance, f, indent=4)
    
    return True

def simulate_farmer_performance(farmer, num_games):
    """Simulate farmer performance for missing games using exact core.py logic"""
    from tasks import get_task_for_job
    
    best_role = determine_best_role(farmer)
    total_points = 0
    total_injuries = 0
    injury_points_lost = 0
    miss_days = 0
    
    # Load seasonal crops and farmer preferences
    try:
        with open("seasonal_crops.json", "r") as f:
            seasonal_crops = json.load(f)
    except FileNotFoundError:
        seasonal_crops = {
            "summer": ["tomatoes", "corn", "peppers", "cucumbers", "watermelons", "zucchini"],
            "fall": ["pumpkins", "apples", "squash", "sweet_potatoes", "cranberries", "carrots"],
            "winter": ["kale", "brussels_sprouts", "potatoes", "onions", "winter_wheat", "cabbage"],
            "spring": ["lettuce", "radishes", "peas", "strawberries", "spinach", "asparagus"]
        }
    
    try:
        with open("farmer_crop_preferences.json", "r") as f:
            farmer_preferences = json.load(f)
    except FileNotFoundError:
        farmer_preferences = {}
    
    # Get season (default to summer for simulation)
    season = "summer"
    
    for game_num in range(num_games):
        # Skip if farmer is injured
        if miss_days > 0:
            miss_days -= 1
            continue
        
        # Select random daily crop from season
        daily_crop = random.choice(seasonal_crops.get(season, ["corn"]))
        
        # Roll catastrophe exactly like core.py
        event_type = 0
        cat_ptloss = 0
        is_affected_by_catastrophe = False
        
        roll = random.randint(1, 100)
        if roll < 60:
            event_type = 1
            cat_ptloss = 1
            # 1/3 chance this farmer is specifically affected
            is_affected_by_catastrophe = random.random() < 0.33
        elif 80 <= roll < 90:
            event_type = 2
            cat_ptloss = 2
            is_affected_by_catastrophe = True  # All farmers affected
        elif roll >= 90:
            event_type = 3
            is_affected_by_catastrophe = True  # All farmers affected
        
        # Get task points using exact core.py logic
        pts, _ = get_task_for_job(
            best_role,
            farmer["strength"],
            farmer["handy"],
            farmer["stamina"],
            farmer["name"],
            []  # No other farmers for simulation
        )
        
        # Check for injury exactly like core.py
        injury_loss = 0
        if random.randint(1, 3) == 3 and random.randint(1, 11) > farmer["physical"]:
            injury_loss = random.randint(1, 2)
            total_injuries += 1
            injury_points_lost += injury_loss
            if random.random() < 0.5:
                miss_days = random.randint(1, 2)
        
        # Calculate crop harvest exactly like core.py
        task_success = pts > 0
        is_injured = injury_loss > 0
        
        # Base crop amount based on task success
        if task_success:
            base_crops = random.randint(30, 50)
        else:
            base_crops = random.randint(5, 20)
        
        # Apply preference multiplier
        preferred_crop = farmer_preferences.get(farmer["name"], {}).get(season, "")
        if preferred_crop == daily_crop:
            base_crops = int(base_crops * 1.5)
        
        # Apply injury/catastrophe multipliers to crops
        if event_type >= 2:
            final_crops = 0
        elif is_injured or event_type == 1:
            final_crops = int(base_crops * 0.4)
        else:
            final_crops = base_crops
        
        final_crops = max(0, final_crops)
        
        # Calculate final points exactly like core.py
        final_points = pts
        
        # Apply catastrophe effects
        if event_type == 1 and is_affected_by_catastrophe:
            final_points -= cat_ptloss
        elif event_type == 2:
            final_points -= cat_ptloss
        elif event_type == 3:
            final_points = 0
        
        # Apply injury loss
        final_points -= injury_loss
        final_points = max(0, final_points)
        
        # Add crop points to total (crops are separate from task points)
        final_points += final_crops
        
        total_points += final_points
    
    return {"points": total_points, "injuries": total_injuries}

def determine_best_role(farmer):
    """Determine farmer's best role based on highest non-physical stat"""
    stats = {
        "Fix Meiser": farmer["handy"],
        "Speed Runner": farmer["stamina"],
        "Lift Tender": farmer["strength"]
    }
    return max(stats.keys(), key=lambda x: stats[x])

def get_role_stat(farmer, role):
    """Get the stat value for a specific role"""
    role_stats = {
        "Fix Meiser": farmer["handy"],
        "Speed Runner": farmer["stamina"], 
        "Lift Tender": farmer["strength"]
    }
    return role_stats.get(role, 5)

def calculate_stat_progression(league_code):
    """Calculate new farmer stats based on previous season performance"""
    archive_file = f"previous_szn_stats_{league_code}.json"
    
    if not os.path.exists(archive_file):
        return False
    
    with open(archive_file, "r") as f:
        archived_performance = json.load(f)
    
    # Load original farmer pool
    farmer_pool = load_farmer_pool()
    
    # Create new league-specific farmer pool
    new_farmer_pool = []
    
    for farmer in farmer_pool:
        farmer_name = farmer["name"]
        performance = archived_performance.get(farmer_name, {})
        
        if not performance:
            # No performance data, keep original stats
            new_farmer_pool.append(farmer.copy())
            continue
        
        new_farmer = farmer.copy()
        
        # Calculate performance metrics
        avg_points = performance["total_points"] / max(performance["games_played"], 1)
        total_injuries = performance["total_injuries"]
        best_role = performance["best_role"]
        role_stat = get_role_stat(farmer, best_role)
        
        # Define performance thresholds
        good_performance_threshold = 10  # Average points per game
        poor_performance_threshold = 9   # 9 and lower is poor performance
        
        is_good_performance = avg_points >= good_performance_threshold
        is_poor_performance = avg_points <= poor_performance_threshold
        
        # Apply role stat changes
        if role_stat >= 6 and role_stat <= 9:
            if is_good_performance:
                new_farmer = modify_role_stat(new_farmer, best_role, 1)
            elif is_poor_performance:
                new_farmer = modify_role_stat(new_farmer, best_role, -2)
        elif role_stat >= 1 and role_stat <= 5:
            if is_good_performance:
                new_farmer = modify_best_stat(new_farmer, 2)
            elif is_poor_performance:
                new_farmer = modify_best_stat(new_farmer, -1)
        
        # Apply physical health changes
        physical_stat = farmer["physical"]
        many_injuries = total_injuries >= 6  # 6 or more injuries
        
        if physical_stat >= 6 and physical_stat <= 10:  # Physical stat 6-10
            if many_injuries:
                # Decrease by 1 with 50% chance for another decrease
                new_farmer["physical"] = max(1, new_farmer["physical"] - 1)
                if random.random() < 0.5:  # 50% chance
                    new_farmer["physical"] = max(1, new_farmer["physical"] - 1)
            # No change for few injuries when physical is 6-10
        elif physical_stat >= 1 and physical_stat <= 5:  # Physical stat 1-5
            if not many_injuries:  # Less than 6 injuries
                # Increase by 1 with 50% chance for another increase
                new_farmer["physical"] = min(10, new_farmer["physical"] + 1)
                if random.random() < 0.5:  # 50% chance
                    new_farmer["physical"] = min(10, new_farmer["physical"] + 1)
            # No change for many injuries when physical is 1-5
        
        new_farmer_pool.append(new_farmer)
    
    # Apply random stat boosts to 5 farmers
    new_farmer_pool = apply_random_stat_boosts(new_farmer_pool, league_code)
    
    # Save league-specific farmer pool
    league_farmer_pool_file = f"farmer_pool_{league_code}.json"
    with open(league_farmer_pool_file, "w") as f:
        json.dump(new_farmer_pool, f, indent=4)
    
    return True

def modify_role_stat(farmer, role, change):
    """Modify the stat associated with a specific role"""
    farmer = farmer.copy()
    role_stat_map = {
        "Fix Meiser": "handy",
        "Speed Runner": "stamina",
        "Lift Tender": "strength"
    }
    
    stat_name = role_stat_map.get(role)
    if stat_name:
        farmer[stat_name] = max(1, min(10, farmer[stat_name] + change))
    
    return farmer

def modify_best_stat(farmer, change):
    """Modify farmer's best non-physical stat"""
    farmer = farmer.copy()
    
    # Find best stat (excluding physical)
    stats = {
        "strength": farmer["strength"],
        "handy": farmer["handy"],
        "stamina": farmer["stamina"]
    }
    
    best_stat = max(stats.keys(), key=lambda x: stats[x])
    farmer[best_stat] = max(1, min(10, farmer[best_stat] + change))
    
    return farmer

def apply_random_stat_boosts(farmer_pool, league_code):
    """Apply random stat boosts to 5 randomly selected farmers"""
    print(f"\n=== RANDOM STAT BOOSTS FOR LEAGUE {league_code} ===")
    
    # Make a copy of the farmer pool to modify
    boosted_farmer_pool = [farmer.copy() for farmer in farmer_pool]
    
    # Randomly select 5 farmers
    selected_farmers = random.sample(boosted_farmer_pool, min(5, len(boosted_farmer_pool)))
    
    print("Selected farmers for random stat boosts:")
    
    for farmer in selected_farmers:
        # Find best non-physical stat
        stats = {
            "strength": farmer["strength"],
            "handy": farmer["handy"],
            "stamina": farmer["stamina"]
        }
        best_stat = max(stats.keys(), key=lambda x: stats[x])
        original_value = farmer[best_stat]
        
        # Random boost: 50% chance for +1, 50% chance for +2
        boost = random.choice([1, 2])
        
        # Apply boost with cap at 10
        farmer[best_stat] = min(10, farmer[best_stat] + boost)
        new_value = farmer[best_stat]
        
        # Console logging
        print(f"  🎲 {farmer['name']}: {best_stat.upper()} {original_value} → {new_value} (+{boost})")
    
    print("=== END RANDOM STAT BOOSTS ===\n")
    
    return boosted_farmer_pool

def reset_league_for_new_season(league_code):
    """Reset league data while preserving core settings and using new farmer pool"""
    leagues = load_leagues()
    
    if league_code not in leagues:
        return False
    
    league = leagues[league_code]
    
    # Preserve core league settings
    core_settings = {
        "name": league["name"],
        "code": league["code"],
        "host": league["host"],
        "players": league["players"],
        "season": league["season"],
        "matchdays": league["matchdays"],
        "use_playoffs": league["use_playoffs"],
        "playoff_cutoff": league["playoff_cutoff"],
        "lock_market_in_playoffs": league["lock_market_in_playoffs"]
    }
    
    # Reset league to pre-draft state
    leagues[league_code] = {
        **core_settings,
        "draft_time": None,
        "draft_complete": False,
        "snake_order": [],
        "market_initialized": False,
        "playoff_records": {},
        "recorded_matchups": [],
        "status": "active",  # Remove finished status
        "picks_made": 0,
        "picked_farmers": [],
        "user_drafts": {},
        "matchup_schedule": {},
        "brackets_created": False,
        "playoff_brackets": {},
        "bracket_schedules": {}
    }
    
    # Clear final standings and archived teams
    if "final_standings" in leagues[league_code]:
        del leagues[league_code]["final_standings"]
    if "winner" in leagues[league_code]:
        del leagues[league_code]["winner"]
    if "completion_date" in leagues[league_code]:
        del leagues[league_code]["completion_date"]
    if "archived_teams" in leagues[league_code]:
        del leagues[league_code]["archived_teams"]
    
    save_leagues(leagues)
    
    # Reset all players' stats
    from stats import load_stats, save_stats
    all_stats = load_stats()
    
    for player in league["players"]:
        if player in all_stats["users"]:
            all_stats["users"][player] = {
                "matchday": 0,
                "drafted_team": {},
                "data": []
            }
    
    save_stats(all_stats)
    
    # Clean up league-specific files
    cleanup_league_files(league_code)
    
    return True

def cleanup_league_files(league_code):
    """Clean up league-specific files for fresh start"""
    files_to_clean = [
        f"market_{league_code}.json",
        f"chat_{league_code}.json"
    ]
    
    for file_path in files_to_clean:
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # Clean story data for league players
    try:
        with open("story.json", "r") as f:
            story_data = json.load(f)
        
        leagues = load_leagues()
        league = leagues.get(league_code, {})
        
        for player in league.get("players", []):
            if player in story_data:
                del story_data[player]
        
        with open("story.json", "w") as f:
            json.dump(story_data, f, indent=4)
    except FileNotFoundError:
        pass

def load_leagues():
    """Load leagues data"""
    try:
        with open("leagues.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_leagues(leagues):
    """Save leagues data"""
    with open("leagues.json", "w") as f:
        json.dump(leagues, f, indent=4)

def load_farmer_pool():
    """Load farmer pool data"""
    try:
        with open("farmer_pool.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def continue_league_new_season(league_code):
    """Main function to continue a league with a new season"""
    print(f"Starting new season progression for league {league_code}...")
    
    # Archive previous season performance
    if not archive_season_performance(league_code):
        print("Failed to archive season performance")
        return False
    
    print("Previous season performance archived successfully")
    
    # Calculate and apply stat progression
    if not calculate_stat_progression(league_code):
        print("Failed to calculate stat progression")
        return False
    
    print("Farmer stat progression calculated and applied")
    
    # Reset league for new season
    if not reset_league_for_new_season(league_code):
        print("Failed to reset league for new season")
        return False
    
    print(f"League {league_code} successfully reset for new season with evolved farmer stats!")
    return True

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python continue.py <league_code>")
        sys.exit(1)
    
    league_code = sys.argv[1]
    success = continue_league_new_season(league_code)
    
    if success:
        print("League continuation completed successfully!")
    else:
        print("League continuation failed!")
        sys.exit(1)
