import requests
import json
import os
from datetime import datetime, timedelta
from tradinglib import ksplib
from tradinglib import tools

class PushoverNotifier:
    def __init__(self, api: str = "tradingdesk", storage_file: str = "pushover_data.json"):
        ksp = ksplib.Ksp()
        (self.user_key, self.api_token, self.url ) = ksp.get_ksp(api).values()
        self.storage_file = storage_file
        self.data = self._load_data()
    
    def _load_data(self):
        if os.path.exists(self.storage_file):
            with open(self.storage_file, "r") as file:
                return json.load(file)
        return {}
    
    def _save_data(self):
        with open(self.storage_file, "w") as file:
            json.dump(self.data, file)
    
    def _should_send(self, ticker: str, price: float, date: str):

        now = datetime.now().strftime("%Y-%M-%d 00:00:00")
        last_entry = self.data.get(ticker, {})
        last_price = last_entry.get("price")
        last_sent = last_entry.get("last_sent")

        print(ticker)
        # Send on first time identidied
        if last_sent == None:
            print("no entry yet")
            return True
    
        # Send if price has changed
        if round(last_price,3) != round(price,3) and last_sent != date:# and last_sent != date:
            print(f"price change {last_price}:{price}")
            return True
                
        print("no update send")
        return False
    
    def send_notification(self, ticker: str, price: float, date: str, message: str = "", title: str = "Trade processor"):
        if not self._should_send(ticker, price, date):
            return False
        if message == '':
            message = f"{ticker}: {price}, {date}"
        hostname = "localhost"
        try:
            hostname = os.uname()[1]
        except Exception:
            pass
        data = {
            "token": self.api_token,
            "user": self.user_key,
            "title": f"{hostname}:{title}",
            "message": f"{message}"
        }
        response = requests.post(self.url, data=data)
        
        if response.status_code == 200:
            print("sent and saving data")
            self.data[ticker] = {"price": price, "last_sent": date}
            self._save_data()
            return True
        
        return False
