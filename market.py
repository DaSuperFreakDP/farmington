import json
import os
import random
from tasks import get_task_for_job

MARKET_STATS_FILE = "market_stats.json"

class MarketManager:
    def __init__(self):
        self.stats_file = MARKET_STATS_FILE
    
    def load_market_stats(self):
        if not os.path.exists(self.stats_file):
            return {}
        with open(self.stats_file, "r") as f:
            return json.load(f)
    
    def save_market_stats(self, stats):
        with open(self.stats_file, "w") as f:
            json.dump(stats, f, indent=4)
    
    def get_market_stats(self):
        """Get performance statistics for undrafted farmers"""
        stats = self.load_market_stats()
        
        # Calculate derived stats
        for farmer_name, data in stats.items():
            if data["matchdays_played"] > 0:
                data["avg_points"] = data["total_points"] / data["matchdays_played"]
            else:
                data["avg_points"] = 0.0
            
            # Recent form (last 5 performances)
            recent = data.get("recent_form", [])
            if recent:
                data["recent_avg"] = sum(recent) / len(recent)
            else:
                data["recent_avg"] = 0.0
        
        return stats
    
    def update_farmer_performance(self, farmer_name, points, role):
        """Update performance stats for a market farmer (max 5 matchdays)"""
        stats = self.load_market_stats()
        
        if farmer_name not in stats:
            stats[farmer_name] = {
                "total_points": 0,
                "matchdays_played": 0,
                "roles_played": {},
                "recent_form": []
            }
        
        farmer_stats = stats[farmer_name]
        
        # Only track up to 5 matchdays
        if farmer_stats["matchdays_played"] < 5:
            farmer_stats["total_points"] += points
            farmer_stats["matchdays_played"] += 1
            
            # Track role performance
            if role not in farmer_stats["roles_played"]:
                farmer_stats["roles_played"][role] = {"count": 0, "total_points": 0}
            farmer_stats["roles_played"][role]["count"] += 1
            farmer_stats["roles_played"][role]["total_points"] += points
            
            # Update recent form (exactly 5 entries max)
            farmer_stats["recent_form"].append(points)
        else:
            # Roll over - remove oldest, add newest
            farmer_stats["total_points"] = farmer_stats["total_points"] - farmer_stats["recent_form"][0] + points
            farmer_stats["recent_form"] = farmer_stats["recent_form"][1:] + [points]
        
        self.save_market_stats(stats)

def get_undrafted_farmers():
    """Get list of farmers not currently drafted by any user"""
    # Load farmer pool
    try:
        with open("farmer_pool.json", "r") as f:
            farmer_pool = json.load(f)
    except FileNotFoundError:
        return []
    
    # Load user stats to see who's drafted
    from stats import load_stats
    stats = load_stats()
    
    drafted_farmers = set()
    for user_data in stats["users"].values():
        for farmer_data in user_data.get("drafted_team", {}).values():
            if isinstance(farmer_data, dict):
                drafted_farmers.add(farmer_data["name"])
    
    # Return undrafted farmers
    undrafted = []
    for farmer in farmer_pool:
        if farmer["name"] not in drafted_farmers:
            undrafted.append(farmer)
    
    return undrafted

def assign_market_farmers_to_roles():
    """Assign farmers to their optimal roles based on best stats (excluding physical)"""
    undrafted = get_undrafted_farmers()
    
    market_assignments = {}
    for farmer in undrafted:
        # Find best stat (excluding physical)
        stats = {
            "Fix Meiser": farmer["handy"],
            "Speed Runner": farmer["stamina"], 
            "Lift Tender": farmer["strength"]
        }
        
        # Assign to role with highest stat
        suggested_role = max(stats.keys(), key=lambda x: stats[x])
        
        market_assignments[farmer["name"]] = {
            "farmer": farmer,
            "role": suggested_role
        }
    
    # Save assignments
    with open("market_assignments.json", "w") as f:
        json.dump(market_assignments, f, indent=4)
    
    return market_assignments

def run_market_matchday():
    """Run matchday simulation for market farmers"""
    try:
        with open("market_assignments.json", "r") as f:
            assignments = json.load(f)
    except FileNotFoundError:
        return
    
    market_manager = MarketManager()
    
    # Run each farmer's performance
    for farmer_name, assignment in assignments.items():
        farmer = assignment["farmer"]
        role = assignment["role"]
        
        # Simulate performance using existing task system
        points, results = get_task_for_job(
            role,
            farmer["strength"],
            farmer["handy"],
            farmer["stamina"],
            farmer["name"],
            []  # No other farmers for market simulation
        )
        
        # Apply injury/catastrophe simulation (simplified)
        injury_loss = 0
        if random.randint(1, 3) == 3 and random.randint(1, 11) > farmer["physical"]:
            injury_loss = random.randint(1, 2)
        
        final_points = max(0, points - injury_loss)
        
        # Update market stats
        market_manager.update_farmer_performance(farmer_name, final_points, role)
        
        print(f"[Market] {farmer_name} ({role}): {final_points} points")

if __name__ == "__main__":
    # Test the market system
    assign_market_farmers_to_roles()
    run_market_matchday()
