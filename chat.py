
import os
import json
from datetime import datetime

class ChatManager:
    def __init__(self):
        self.chats_dir = "league_chats"
        os.makedirs(self.chats_dir, exist_ok=True)
    
    def get_chat_file(self, league_code):
        """Get the chat file path for a league"""
        return os.path.join(self.chats_dir, f"chat_{league_code}.json")
    
    def load_chat_messages(self, league_code):
        """Load chat messages for a league"""
        chat_file = self.get_chat_file(league_code)
        try:
            with open(chat_file, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return []
    
    def save_chat_messages(self, league_code, messages):
        """Save chat messages for a league"""
        chat_file = self.get_chat_file(league_code)
        with open(chat_file, "w") as f:
            json.dump(messages, f, indent=4)
    
    def add_message(self, league_code, username, message):
        """Add a new message to the league chat"""
        messages = self.load_chat_messages(league_code)
        
        new_message = {
            "id": len(messages) + 1,
            "username": username,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        
        messages.append(new_message)
        self.save_chat_messages(league_code, messages)
        return new_message
    
    def delete_league_chat(self, league_code):
        """Delete all chat messages for a league"""
        chat_file = self.get_chat_file(league_code)
        if os.path.exists(chat_file):
            os.remove(chat_file)
    
    def get_recent_messages(self, league_code, limit=50):
        """Get recent messages for a league"""
        messages = self.load_chat_messages(league_code)
        return messages[-limit:] if len(messages) > limit else messages
