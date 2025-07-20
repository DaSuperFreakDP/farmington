import json
import os
import uuid
from datetime import datetime
from stats import get_user_stats, update_user_stats

TRADES_FILE = "trades.json"

class TradingManager:
    def __init__(self):
        self.trades_file = TRADES_FILE
    
    def load_trades(self):
        if not os.path.exists(self.trades_file):
            return []
        with open(self.trades_file, "r") as f:
            return json.load(f)
    
    def save_trades(self, trades):
        with open(self.trades_file, "w") as f:
            json.dump(trades, f, indent=4)
    
    def propose_trade(self, from_user, to_user, offered_farmer_name, requested_farmer_name, message=""):
        """Create a new trade proposal based on specific farmer names"""
        trades = self.load_trades()
        
        # Get farmer details
        from_user_data = get_user_stats(from_user)
        to_user_data = get_user_stats(to_user)
        
        from_user_team = from_user_data.get("drafted_team", {})
        to_user_team = to_user_data.get("drafted_team", {})
        
        # Find the offered farmer by name in from_user's team
        offered_farmer = None
        offered_role = None
        for role, farmer_data in from_user_team.items():
            if farmer_data and farmer_data.get("name") == offered_farmer_name:
                offered_farmer = farmer_data
                offered_role = role
                break
        
        # Find the requested farmer by name in to_user's team
        requested_farmer = None
        requested_role = None
        for role, farmer_data in to_user_team.items():
            if farmer_data and farmer_data.get("name") == requested_farmer_name:
                requested_farmer = farmer_data
                requested_role = role
                break
        
        if not offered_farmer or not requested_farmer:
            return False
        
        trade = {
            "id": str(uuid.uuid4()),
            "from_user": from_user,
            "to_user": to_user,
            "offered_farmer_name": offered_farmer_name,
            "offered_farmer": offered_farmer,
            "offered_role": offered_role,
            "requested_farmer_name": requested_farmer_name,
            "requested_farmer": requested_farmer,
            "requested_role": requested_role,
            "message": message,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "responded_at": None
        }
        
        trades.append(trade)
        self.save_trades(trades)
        return True
    
    def accept_trade(self, trade_id, accepting_user):
        """Accept a trade proposal and execute the swap"""
        trades = self.load_trades()
        
        trade = None
        for t in trades:
            if t["id"] == trade_id and t["to_user"] == accepting_user and t["status"] == "pending":
                trade = t
                break
        
        if not trade:
            return False
        
        # Execute the trade
        from_user_data = get_user_stats(trade["from_user"])
        to_user_data = get_user_stats(trade["to_user"])
        
        # Get current teams
        from_user_team = from_user_data.get("drafted_team", {})
        to_user_team = to_user_data.get("drafted_team", {})
        
        # Validate that the exact farmers mentioned in the trade are still available
        # Find the offered farmer by name in from_user's current team
        offered_farmer = None
        offered_role = None
        for role, farmer_data in from_user_team.items():
            if farmer_data and farmer_data.get("name") == trade["offered_farmer_name"]:
                offered_farmer = farmer_data
                offered_role = role
                break
        
        # Find the requested farmer by name in to_user's current team
        requested_farmer = None
        requested_role = None
        for role, farmer_data in to_user_team.items():
            if farmer_data and farmer_data.get("name") == trade["requested_farmer_name"]:
                requested_farmer = farmer_data
                requested_role = role
                break
        
        # If either farmer is no longer available or has been changed, reject the trade
        if not offered_farmer or not requested_farmer:
            return False
        
        # Preserve injury data during the trade
        self._preserve_injury_data(trade["offered_farmer_name"], trade["requested_farmer_name"])
        
        # Perform the swap using the current roles where these farmers are located
        from_user_team[offered_role] = requested_farmer
        to_user_team[requested_role] = offered_farmer
        
        # Update user stats
        update_user_stats(trade["from_user"], from_user_data)
        update_user_stats(trade["to_user"], to_user_data)
        
        # Mark trade as completed
        trade["status"] = "accepted"
        trade["responded_at"] = datetime.now().isoformat()
        
        self.save_trades(trades)
        return True
    
    def _preserve_injury_data(self, farmer1_name, farmer2_name):
        """Preserve injury data when farmers are traded"""
        try:
            # Load story data to get current miss_days
            story_file = "story.json"
            if os.path.exists(story_file):
                with open(story_file, "r") as f:
                    story_data = json.load(f)
                
                # Collect injury data for both farmers across all users
                farmer1_miss_days = 0
                farmer2_miss_days = 0
                
                for username, user_story in story_data.items():
                    miss_days = user_story.get("miss_days", {})
                    if farmer1_name in miss_days:
                        farmer1_miss_days = max(farmer1_miss_days, miss_days[farmer1_name])
                    if farmer2_name in miss_days:
                        farmer2_miss_days = max(farmer2_miss_days, miss_days[farmer2_name])
                
                # Update story data for all users to reflect the trade
                for username, user_story in story_data.items():
                    miss_days = user_story.get("miss_days", {})
                    
                    # Update miss_days for both farmers
                    if farmer1_name in miss_days or farmer1_miss_days > 0:
                        miss_days[farmer1_name] = farmer1_miss_days
                    if farmer2_name in miss_days or farmer2_miss_days > 0:
                        miss_days[farmer2_name] = farmer2_miss_days
                    
                    user_story["miss_days"] = miss_days
                
                # Save updated story data
                with open(story_file, "w") as f:
                    json.dump(story_data, f, indent=4)
                    
        except Exception as e:
            print(f"Error preserving injury data during trade: {e}")
            # Don't fail the trade if injury preservation fails
    
    def reject_trade(self, trade_id):
        """Reject a trade proposal"""
        trades = self.load_trades()
        
        for trade in trades:
            if trade["id"] == trade_id and trade["status"] == "pending":
                trade["status"] = "rejected"
                trade["responded_at"] = datetime.now().isoformat()
                break
        
        self.save_trades(trades)
        return True
    
    def get_incoming_trades(self, username):
        """Get pending trade proposals sent to this user"""
        trades = self.load_trades()
        return [t for t in trades if t["to_user"] == username and t["status"] == "pending"]
    
    def get_outgoing_trades(self, username):
        """Get trade proposals sent by this user"""
        trades = self.load_trades()
        return [t for t in trades if t["from_user"] == username and t["status"] in ["pending", "accepted", "rejected"]]
    
    def get_trade_history(self, username):
        """Get all trades involving this user"""
        trades = self.load_trades()
        return [t for t in trades if t["from_user"] == username or t["to_user"] == username]

if __name__ == "__main__":
    # Test the trading system
    tm = TradingManager()
    print("Trading system initialized")
