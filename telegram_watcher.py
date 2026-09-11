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

    def _ocr_variants(self, image):
        """Create enlarged/contrast variants without changing the characters."""
        variants = [image]
        try:
            import cv2
            import numpy as np

            rgb = np.array(image)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

            # Game chat font is small, so enlarge it before OCR.
            up = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            up = cv2.GaussianBlur(up, (3, 3), 0)
            sharp = cv2.addWeighted(
                cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                1.6, up, -0.6, 0
            )

            # Keep several versions because colored chat text can disappear
            # with a single threshold. No character whitelist is applied.
            _, binary = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.extend([
                Image.fromarray(cv2.cvtColor(
                    cv2.resize(bgr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                    cv2.COLOR_BGR2RGB
                )),
                Image.fromarray(sharp),
                Image.fromarray(binary),
            ])
        except Exception:
            pass
        return variants

    @staticmethod
    def _score_ocr(text):
        if not text:
            return -100000
        score = len(text)
        if "Лично" in text:
            score += 10000
        # Prefer text containing Cyrillic instead of Latin transliteration.
        cyr = sum("А" <= ch <= "я" or ch in "Ёё" for ch in text)
        score += cyr * 8
        return score

    def recognize(self, image):
        """Recognize the chat using enlarged variants; preserve all symbols."""
        best_text = ""
        best_score = -100000

        variants = self._ocr_variants(image)

        if self.ocr:
            for variant in variants:
                try:
                    result, _ = self.ocr(variant)
                    text = "\n".join(item[1] for item in result) if result else ""
                    score = self._score_ocr(text)
                    if score > best_score:
                        best_text, best_score = text, score
                    if "Лично" in text:
                        return text, "RapidOCR"
                except Exception:
                    continue

        # OCR.Space gets the same enlarged image. It is useful when local OCR
        # confuses Cyrillic letters. The API is not given a character whitelist,
        # so usernames and message symbols are not intentionally restricted.
        for variant in variants[1:]:
            fallback = self._ocr_space(variant)
            score = self._score_ocr(fallback)
            if score > best_score:
                best_text, best_score = fallback, score
            if "Лично" in fallback:
                return fallback, "OCR.Space"

        return best_text, "RapidOCR" if best_text else "OCR не распознал текст"

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
