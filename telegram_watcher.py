import io
import os
import time
import requests

try:
    from rapidocr import RapidOCR, EngineType, LangDet, LangRec, ModelType, OCRVersion
except Exception:
    RapidOCR = None
    EngineType = LangDet = LangRec = ModelType = OCRVersion = None


class TelegramWatcher:
    """OCR watcher for a selected game-chat region."""

    def __init__(self, token="", chat_id="", region=None, ocrspace_key=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.region = region
        self.ocrspace_key = (ocrspace_key or os.getenv("OCRSPACE_API_KEY", "helloworld")).strip()
        self.stop_event = __import__("threading").Event()
        self.seen = set()
        self.last_results = []
        self.ocr = None
        if RapidOCR:
            try:
                self.ocr = RapidOCR(params={
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Det.lang_type": LangDet.CH,
                    "Det.model_type": ModelType.MOBILE,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.lang_type": LangRec.CYRILLIC,
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Cls.engine_type": EngineType.ONNXRUNTIME,
                    "Cls.lang_type": LangDet.CH,
                    "Cls.model_type": ModelType.MOBILE,
                    "Cls.ocr_version": OCRVersion.PPOCRV4,
                })
            except Exception:
                self.ocr = None

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

    def _ocr_space(self, image):
        """OCR.Space fallback for the selected chat image."""
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

    def _ocr_variants(self, image):
        """Prepare several chat-specific images for OCR.
        The game uses small orange/white text on a dark background, so keeping
        the original RGB image alone is not reliable.
        """
        variants = [("original", image)]
        try:
            import cv2
            import numpy as np
            from PIL import Image

            rgb = np.array(image)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

            # Enlarge small game-chat glyphs.
            up = cv2.resize(bgr, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            variants.append(("enlarged", Image.fromarray(cv2.cvtColor(up, cv2.COLOR_BGR2RGB))))

            # High-contrast grayscale while retaining glyph shapes.
            gray_up = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray_up = clahe.apply(gray_up)
            variants.append(("gray", Image.fromarray(gray_up)))

            # Keep saturated colored chat text (orange/red/blue labels).
            sat_mask = cv2.inRange(hsv, np.array([0, 45, 60]), np.array([179, 255, 255]))
            sat_mask = cv2.resize(sat_mask, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
            variants.append(("color_mask", Image.fromarray(sat_mask)))

            # Bright text (white/yellow) on the dark game background.
            bright = cv2.inRange(gray, 145, 255)
            bright = cv2.resize(bright, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
            variants.append(("bright_mask", Image.fromarray(bright)))

            # Sharpened original for punctuation and unusual symbols.
            up_rgb = cv2.cvtColor(up, cv2.COLOR_BGR2RGB)
            blur = cv2.GaussianBlur(up_rgb, (0, 0), 1.2)
            sharp = cv2.addWeighted(up_rgb, 1.7, blur, -0.7, 0)
            variants.append(("sharp", Image.fromarray(sharp)))
        except Exception:
            pass
        return variants

    @staticmethod
    def _score_ocr(text, confidence=0.0):
        if not text:
            return -100000.0
        score = float(confidence) * 100.0
        if "Лично" in text:
            score += 10000.0
        cyr = sum(("А" <= ch <= "я") or ch in "Ёё" for ch in text)
        score += cyr * 12.0
        # Penalize obvious OCR noise instead of rewarding long garbage.
        replacement_noise = sum(ch in "{}<>|" for ch in text)
        score -= replacement_noise * 8.0
        return score

    def _run_rapidocr(self, image):
        if not self.ocr:
            return "", 0.0
        try:
            result = self.ocr(image)
            if result is None:
                return "", 0.0
            if hasattr(result, "txts"):
                texts = list(result.txts or ())
                scores = list(result.scores or ())
                text = "\n".join(str(x) for x in texts if str(x).strip())
                confidence = sum(float(x) for x in scores) / len(scores) if scores else 0.0
                return text, confidence
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                raw = result[0]
                parts, scores = [], []
                for item in raw or []:
                    if len(item) >= 3:
                        parts.append(str(item[1]))
                        try:
                            scores.append(float(item[2]))
                        except (TypeError, ValueError):
                            pass
                return "\n".join(parts), (sum(scores)/len(scores) if scores else 0.0)
        except Exception:
            return "", 0.0
        return "", 0.0

    def recognize(self, image):
        """Russian game-chat OCR: dedicated Cyrillic model first, OCR.Space fallback."""
        best_text = ""
        best_score = -100000.0
        best_engine = "OCR не распознал текст"
        variants = self._ocr_variants(image)

        for variant_name, variant in variants:
            text, confidence = self._run_rapidocr(variant)
            score = self._score_ocr(text, confidence)
            if score > best_score:
                best_text, best_score = text, score
                best_engine = f"RapidOCR Cyrillic ({variant_name})"
            if "Лично" in text and confidence >= 0.55:
                return text, f"RapidOCR Cyrillic ({variant_name})"

        for variant_name, variant in variants:
            fallback = self._ocr_space(variant)
            score = self._score_ocr(fallback, 0.0)
            if score > best_score:
                best_text, best_score = fallback, score
                best_engine = f"OCR.Space Russian ({variant_name})"
            if "Лично" in fallback:
                return fallback, f"OCR.Space Russian ({variant_name})"

        return best_text, best_engine

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
        self.last_results = []

        for player, message in self.extract_personal_all(text):
            key = f"{player}\n{message}"
            if key in self.seen:
                status = "ПОВТОР — не отправлено"
                statuses.append(status)
                self.last_results.append((player, message, False, status))
                continue

            ok, status = self._send(f"Игрок: {player}\nСообщение: {message}")
            statuses.append(status)
            self.last_results.append((player, message, ok, status))

            # Mark as processed only after Telegram confirms delivery.
            if ok:
                self.seen.add(key)
                sent_any = True

        if not statuses:
            return False, None
        return sent_any, statuses[-1]

    def stop(self):
        self.stop_event.set()
