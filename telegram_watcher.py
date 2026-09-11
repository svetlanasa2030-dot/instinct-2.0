import re
import threading
import time
import requests

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None


class TelegramWatcher:
    """Independent OCR watcher for personal game-chat messages."""

    def __init__(self, token="", chat_id="", region=None, ocrspace_key=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.region = region
        self.ocrspace_key = ocrspace_key.strip()
        self.stop_event = threading.Event()
        self.seen = set()
        self.ocr = RapidOCR() if RapidOCR else None

    def set_credentials(self, token, chat_id):
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    def set_region(self, region):
        self.region = region

    def check_telegram(self):
        return self._send("Instinct 2.0: Telegram подключён.", retries=1)

    def _send(self, text, retries=3):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(retries):
            try:
                response = requests.post(
                    url,
                    json={"chat_id": self.chat_id, "text": text},
                    timeout=10,
                )
                if response.ok and response.json().get("ok"):
                    return True, "ОТПРАВЛЕНО"
            except requests.RequestException:
                pass
            if attempt < retries - 1:
                time.sleep(1)
        return False, f"НЕ ОТПРАВЛЕНО после {retries} попыток"

    @staticmethod
    def extract_personal(text):
        """Return player/message without filtering nickname characters."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            if not line.startswith("Лично"):
                continue

            payload = line[len("Лично"):].strip()
            if not payload and i + 1 < len(lines):
                payload = lines[i + 1].strip()
                i += 1

            if " шепчет:" in payload:
                player, message = payload.split(" шепчет:", 1)
            elif ":" in payload:
                player, message = payload.split(":", 1)
            else:
                if i + 1 >= len(lines):
                    continue
                player, message = payload, lines[i + 1].strip()

            player = player.strip()
            message = message.strip()
            if player and message:
                return player, message
        return None

    def check_new_message(self, text):
        result = self.extract_personal(text)
        if not result:
            return False, "НЕТ — сообщений «Лично» не найдено"
        player, message = result
        key = f"{player}\n{message}"
        if key in self.seen:
            return False, "НЕТ — сообщение уже обработано"
        return True, "ДА — найдено новое сообщение"

    def process_ocr_text(self, text):
        result = self.extract_personal(text)
        if not result:
            return False, None

        player, message = result
        key = f"{player}\n{message}"
        if key in self.seen:
            return False, None

        self.seen.add(key)
        return self._send(f"Игрок: {player}\nСообщение: {message}")

    def stop(self):
        self.stop_event.set()
