import io
import os
import time
import requests

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None


class TelegramWatcher:
    """OCR watcher for a selected game-chat region."""

    def __init__(self, token="", chat_id="", region=None, ocrspace_key=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.region = region
        self.ocrspace_key = (ocrspace_key or os.getenv("OCRSPACE_API_KEY", "helloworld")).strip()
        self.stop_event = __import__("threading").Event()
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
                    url, json={"chat_id": self.chat_id, "text": text}, timeout=10
                )
                if response.ok and response.json().get("ok"):
                    return True, "ОТПРАВЛЕНО"
            except requests.RequestException:
                pass
            if attempt < retries - 1:
                time.sleep(1)
        return False, f"НЕ ОТПРАВЛЕНО после {retries} попыток"

    @staticmethod
    def _personal_messages(text):
        # OCR.Space can wrap the marker in brackets, e.g. "(Лично )".
        # Search for the marker anywhere in the line and remove OCR-only
        # punctuation before parsing the player and message.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        messages = []
        i = 0

        while i < len(lines):
            line = lines[i]
            marker = line.find("Лично")
            if marker < 0:
                i += 1
                continue

            payload = line[marker + len("Лично"):].strip()
            payload = payload.lstrip(" )]}:;-—–")

            # OCR sometimes puts the personal marker on a separate line.
            if not payload:
                i += 1
                continue

            # Normal game format: "Игрок шепчет: сообщение"
            whisper = " шепчет:"
            if whisper in payload:
                player, message = payload.split(whisper, 1)
            elif "шепчет:" in payload:
                player, message = payload.split("шепчет:", 1)
            elif ":" in payload:
                player, message = payload.split(":", 1)
            else:
                # If OCR split the message over two lines, join the next line.
                player = payload
                message = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if message:
                    i += 1

            player = player.strip(" ()[]{}:;-—–")
            message = message.strip()

            if player and message:
                messages.append((player, message))

            i += 1

        return messages

    @classmethod
    def extract_personal(cls, text):
        messages = cls._personal_messages(text)
        return messages[0] if messages else None

    @classmethod
    def extract_personal_all(cls, text):
        return cls._personal_messages(text)

    def recognize(self, image):
        """RapidOCR first; OCR.Space Russian fallback when local OCR is unusable."""
        local_text = ""
        if self.ocr:
            try:
                result, _ = self.ocr(image)
                local_text = "\n".join(item[1] for item in result) if result else ""
            except Exception:
                local_text = ""

        # If the local model loses Cyrillic markers such as «Лично», use OCR.Space.
        if local_text and "Лично" in local_text:
            return local_text, "RapidOCR"

        fallback = self._ocr_space(image)
        if fallback:
            return fallback, "OCR.Space"
        return local_text, "RapidOCR" if local_text else "OCR не распознал текст"

    def _ocr_space(self, image):
        try:
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            response = requests.post(
                "https://api.ocr.space/parse/image",
                files={"file": ("chat.png", buf.getvalue(), "image/png")},
                data={
                    "apikey": self.ocrspace_key,
                    "language": "rus",
                    "OCREngine": "2",
                    "isOverlayRequired": "false",
                    "scale": "true",
                    "detectOrientation": "true",
                },
                timeout=20,
            )
            if not response.ok:
                return ""
            data = response.json()
            parsed = data.get("ParsedResults") or []
            return "\n".join(p.get("ParsedText", "") for p in parsed).strip()
        except (requests.RequestException, ValueError, OSError):
            return ""

    def check_new_message(self, text):
        messages = self.extract_personal_all(text)
        if not messages:
            return False, "НЕТ — сообщений «Лично» не найдено"
        fresh = []
        for player, message in messages:
            if f"{player}\n{message}" not in self.seen:
                fresh.append((player, message))
        if fresh:
            return True, f"ДА — новых сообщений: {len(fresh)}"
        return False, "НЕТ — сообщения уже обработаны"

    def process_ocr_text(self, text):
        statuses = []
        sent_any = False
        for player, message in self.extract_personal_all(text):
            key = f"{player}\n{message}"
            if key in self.seen:
                statuses.append("ПОВТОР — не отправлено")
                continue
            ok, status = self._send(f"Игрок: {player}\nСообщение: {message}")
            statuses.append(status)
            if ok:
                self.seen.add(key)
                sent_any = True
        if not statuses:
            return False, None
        return sent_any, statuses[-1]

    def stop(self):
        self.stop_event.set()
