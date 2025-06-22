import requests
import os
import threading
import time
import json
from datetime import datetime
from typing import Optional, Dict, Any


class TelegramBot:
    """Complete Telegram bot management for Smart Door system"""

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.command_offset = 0
        self.command_thread = None
        self.running = False

        # Door controller callback (will be set from main app)
        self.door_controller = None

    def set_door_controller(self, controller_func):
        """Set the door controller function from main app"""
        self.door_controller = controller_func

    def is_configured(self) -> bool:
        """Check if bot is properly configured"""
        return bool(self.bot_token and self.chat_id)

    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send text message to Telegram"""
        if not self.enabled or not self.is_configured():
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode}
            response = requests.post(url, json=data, timeout=10)

            if response.status_code == 200:
                print(f"[📱] Telegram message sent successfully")
                return True
            else:
                print(f"[📱] Telegram API error: {response.status_code}")
                return False

        except Exception as e:
            print(f"[📱] Telegram message failed: {e}")
            return False

    def send_photo(
        self, image_path: str, caption: str = "", parse_mode: str = "HTML"
    ) -> bool:
        """Send photo with caption to Telegram"""
        if not self.enabled or not self.is_configured():
            return False

        if not os.path.exists(image_path):
            print(f"[📱] Image not found: {image_path}")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"

            with open(image_path, "rb") as photo:
                files = {"photo": photo}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": parse_mode,
                }
                response = requests.post(url, files=files, data=data, timeout=15)

            if response.status_code == 200:
                print(f"[📱] Telegram photo sent successfully")
                return True
            else:
                print(f"[📱] Telegram photo API error: {response.status_code}")
                return False

        except Exception as e:
            print(f"[📱] Telegram photo failed: {e}")
            return False

    def send_visitor_notification(
        self,
        access_granted: bool,
        recognized_person: Optional[str] = None,
        image_path: Optional[str] = None,
    ) -> bool:
        """Send formatted visitor notification"""
        current_time = datetime.now().strftime("%d.%m.%Y la %H:%M:%S")

        if access_granted and recognized_person:
            # Known person detected
            message = f"""🚪 <b>ACCES PERMIS</b>
            
✅ Persoană recunoscută: <b>{recognized_person}</b>
🕐 Ora: {current_time}
🔓 Ușa s-a deschis automat

Bun venit acasă! 🏠"""

        else:
            # Unknown person detected
            message = f"""🚨 <b>VIZITATOR NECUNOSCUT</b>
            
👤 Persoană necunoscută la ușă
🕐 Ora: {current_time}
⚠️ Acces refuzat - verificați cine este

Răspundeți cu /open pentru a deschide ușa manual."""

        # Send photo with message if available
        if image_path and os.path.exists(image_path):
            return self.send_photo(image_path, message)
        else:
            return self.send_message(message)

    def send_system_notification(self, title: str, message: str) -> bool:
        """Send system status notification"""
        current_time = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"🔧 <b>{title}</b>\n\n{message}\n\n🕐 {current_time}"
        return self.send_message(formatted_message)

    def send_door_opened_notification(self, method: str = "manual") -> bool:
        """Send door opened notification"""
        current_time = datetime.now().strftime("%H:%M:%S")

        if method == "manual":
            message = f"""🚪 <b>UȘA DESCHISĂ MANUAL</b>

✋ Deschis din dashboard
🕐 Ora: {current_time}"""
        elif method == "telegram":
            message = f"""🚪 <b>UȘA DESCHISĂ</b>

📱 Deschis prin comanda Telegram
🕐 Ora: {current_time}"""
        else:
            message = f"""🚪 <b>UȘA DESCHISĂ</b>

🔓 Deschis automat
🕐 Ora: {current_time}"""

        return self.send_message(message)

    def send_test_notification(self) -> bool:
        """Send test notification"""
        message = f"""🧪 <b>Test Smart Door</b>

✅ Notificările Telegram funcționează perfect!
🕐 {datetime.now().strftime('%H:%M:%S')}

Sistemul este gata de utilizare! 🚀"""
        return self.send_message(message)

    def handle_command(self, command: str) -> str:
        """Process incoming Telegram commands"""
        command = command.lower().strip()

        if command == "/open":
            print(f"[📱] Open door command received - sending to frontend")

            if self.door_controller:
                try:
                    # Instead of opening door directly, send SSE to frontend
                    # The frontend will handle video pausing and door opening
                    result = self.door_controller()

                    if result and result.get("success"):
                        return "🚪 Comandă trimisă! Ușa se deschide..."
                    else:
                        return f"❌ Eroare la trimiterea comenzii: {result.get('message', 'Eroare necunoscută')}"

                except Exception as e:
                    print(f"[📱] Exception in door controller: {e}")
                    return f"❌ Eroare tehnică: {str(e)}"
            else:
                return "❌ Controlul ușii nu este disponibil"

        elif command == "/status":
            # System status
            return f"""📊 <b>Status Smart Door</b>

🎥 Camera: {'🟢 Online' if self.enabled else '🔴 Offline'}
🔒 Sistem: {'🟢 Activ' if self.enabled else '🔴 Inactiv'}
📱 Telegram: {'🟢 Conectat' if self.is_configured() else '🔴 NeConfigurat'}
🕐 {datetime.now().strftime('%d.%m.%Y la %H:%M:%S')}"""

        elif command == "/help":
            return """🤖 <b>Comenzi disponibile:</b>

/open - Deschide ușa din distanță
/status - Afișează status-ul sistemului  
/help - Afișează această listă
/settings - Afișează setările curente

💡 <b>Funcționalități:</b>
- Detectare automată persoane cunoscute
- Notificări instant cu poze
- Control de la distanță
- Istoric complet accesări"""

        elif command == "/settings":
            return f"""⚙️ <b>Setări curente:</b>

📱 Notificări: {'🟢 Activate' if self.enabled else '🔴 Dezactivate'}
🔑 Chat ID: <code>{self.chat_id}</code>
🤖 Bot Status: {'🟢 Configurat' if self.is_configured() else '🔴 Neconfigurat'}

Pentru a modifica setările, folosiți dashboard-ul web."""

        else:
            return """❓ <b>Comandă necunoscută</b>

Folosiți /help pentru a vedea comenzile disponibile."""

    def start_command_listener(self):
        """Start listening for Telegram commands in background thread"""
        if self.command_thread and self.command_thread.is_alive():
            print("[📱] Command listener already running")
            return

        if not self.is_configured():
            print("[📱] Bot not configured, command listener not started")
            return

        self.running = True
        self.command_thread = threading.Thread(
            target=self._command_listener_worker, daemon=True
        )
        self.command_thread.start()
        print("[📱] Telegram command listener started")

    def stop_command_listener(self):
        """Stop the command listener"""
        self.running = False
        if self.command_thread:
            print("[📱] Stopping Telegram command listener")

    def _command_listener_worker(self):
        """Background worker for processing Telegram commands"""
        while self.running:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {
                    "offset": self.command_offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }

                response = requests.get(url, params=params, timeout=35)

                if response.status_code == 200:
                    data = response.json()

                    for update in data.get("result", []):
                        self.command_offset = update["update_id"] + 1

                        if "message" in update:
                            message = update["message"]

                            # Only process messages from authorized chat
                            if str(message.get("chat", {}).get("id")) == str(
                                self.chat_id
                            ):
                                text = message.get("text", "")

                                if text.startswith("/"):
                                    print(f"[📱] Processing command: {text}")
                                    response_text = self.handle_command(text)
                                    self.send_message(response_text)
                else:
                    print(f"[📱] Telegram API error: {response.status_code}")
                    time.sleep(5)

            except requests.exceptions.Timeout:
                # Timeout is expected with long polling
                continue
            except Exception as e:
                print(f"[📱] Command listener error: {e}")
                time.sleep(5)

    def update_config(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        """Update bot configuration"""
        restart_needed = False

        if bot_token is not None and bot_token != self.bot_token:
            self.bot_token = bot_token
            restart_needed = True

        if chat_id is not None and chat_id != self.chat_id:
            self.chat_id = chat_id
            restart_needed = True

        if enabled is not None:
            self.enabled = enabled

        # Restart command listener if configuration changed
        if restart_needed and self.running:
            self.stop_command_listener()
            time.sleep(1)
            self.start_command_listener()

    def get_status(self) -> Dict[str, Any]:
        """Get bot status information"""
        return {
            "configured": self.is_configured(),
            "enabled": self.enabled,
            "listening": self.running,
            "chat_id": self.chat_id,
            "has_token": bool(self.bot_token),
        }


# Configuration management
class TelegramConfig:
    """Manage Telegram bot configuration"""

    CONFIG_FILE = "telegram_config.json"

    @classmethod
    def load_config(cls) -> Dict[str, str]:
        """Load configuration from file"""
        try:
            if os.path.exists(cls.CONFIG_FILE):
                with open(cls.CONFIG_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading Telegram config: {e}")

        return {"bot_token": "", "chat_id": "", "enabled": True}

    @classmethod
    def save_config(cls, config: Dict[str, Any]):
        """Save configuration to file"""
        try:
            with open(cls.CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving Telegram config: {e}")


# Create global bot instance
def create_telegram_bot() -> TelegramBot:
    """Create and configure Telegram bot instance"""
    config = TelegramConfig.load_config()

    bot = TelegramBot(
        bot_token=config.get("bot_token", ""),
        chat_id=config.get("chat_id", ""),
        enabled=config.get("enabled", True),
    )

    return bot


# Usage example and testing
if __name__ == "__main__":
    # For testing the bot independently
    config = TelegramConfig.load_config()

    if not config.get("bot_token") or not config.get("chat_id"):
        print("Please configure bot_token and chat_id in telegram_config.json")
        print("Example config:")
        print(
            json.dumps(
                {
                    "bot_token": "YOUR_BOT_TOKEN",
                    "chat_id": "YOUR_CHAT_ID",
                    "enabled": True,
                },
                indent=2,
            )
        )
    else:
        bot = create_telegram_bot()
        print("Testing Telegram bot...")

        # Test message
        success = bot.send_test_notification()
        print(f"Test notification: {'✅ Sent' if success else '❌ Failed'}")

        # Start command listener for testing
        bot.start_command_listener()
        print("Command listener started. Send /help to your bot to test commands.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping bot...")
            bot.stop_command_listener()
