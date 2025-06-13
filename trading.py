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
    
    def propose_trade(self, from_user, to_user, offered_role, requested_role, message=""):
        """Create a new trade proposal"""
        trades = self.load_trades()
        
        # Get farmer details
        from_user_data = get_user_stats(from_user)
        to_user_data = get_user_stats(to_user)
        
        offered_farmer = from_user_data.get("drafted_team", {}).get(offered_role)
        requested_farmer = to_user_data.get("drafted_team", {}).get(requested_role)
        
        if not offered_farmer or not requested_farmer:
            return False
        
        trade = {
            "id": str(uuid.uuid4()),
            "from_user": from_user,
            "to_user": to_user,
            "offered_role": offered_role,
            "offered_farmer": offered_farmer,
            "requested_role": requested_role,
            "requested_farmer": requested_farmer,
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
        
        # Swap farmers
        from_user_team = from_user_data.get("drafted_team", {})
        to_user_team = to_user_data.get("drafted_team", {})
        
        # Store the farmers being swapped
        offered_farmer = from_user_team.get(trade["offered_role"])
        requested_farmer = to_user_team.get(trade["requested_role"])
        
        if not offered_farmer or not requested_farmer:
            return False
        
        # Perform the swap
        from_user_team[trade["offered_role"]] = requested_farmer
        to_user_team[trade["requested_role"]] = offered_farmer
        
        # Update user stats
        update_user_stats(trade["from_user"], from_user_data)
        update_user_stats(trade["to_user"], to_user_data)
        
        # Mark trade as completed
        trade["status"] = "accepted"
        trade["responded_at"] = datetime.now().isoformat()
        
        self.save_trades(trades)
        return True
    
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
