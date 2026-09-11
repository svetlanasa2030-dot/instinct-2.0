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
        # Two recognition models are used because the fixed chat words are
        # Cyrillic while the player name may be Chinese, Latin, Cyrillic,
        # digits or mixed. PP-OCRv5 provides dedicated models for these
        # scripts; no whitelist is applied to the extracted nickname.
        self.ocr_cyrillic = None
        self.ocr_chinese = None
        if RapidOCR:
            self.ocr_cyrillic = self._create_ocr(LangRec.CYRILLIC)
            self.ocr_chinese = self._create_ocr(LangRec.CH)
        self.ocr_init_status = (
            "RapidOCR: Cyrillic + Chinese"
            if (self.ocr_cyrillic or self.ocr_chinese)
            else "RapidOCR не загрузился — используется OCR.Space"
        )

    def _create_ocr(self, language):
        try:
            return RapidOCR(params={
                # The game chat is horizontal; disabling orientation classification
                # avoids loading an unnecessary model and makes startup more reliable.
                "Global.use_cls": False,
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.lang_type": LangDet.CH,
                "Det.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.lang_type": language,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            })
        except Exception:
            return None

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
                    "language": "auto",
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
            if data.get("IsErroredOnProcessing"):
                return ""
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

    def _run_rapidocr(self, image, engine=None):
        if engine is None:
            engine = self.ocr_cyrillic
        if not engine:
            return "", 0.0
        try:
            result = engine(image)
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
                return "\n".join(parts), (sum(scores) / len(scores) if scores else 0.0)
        except Exception:
            return "", 0.0
        return "", 0.0

    @staticmethod
    def _has_cjk(text):
        return any(
            "\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
            or "\u3040" <= ch <= "\u30ff"
            for ch in text
        )

    @classmethod
    def _merge_multilingual_nickname(cls, cyr_text, ch_text):
        """Use Cyrillic OCR for the fixed Russian markers and Chinese OCR
        when the nickname itself contains CJK characters.
        """
        base = cls._personal_messages(cyr_text)
        if not base:
            return cls._personal_messages(ch_text)

        player, message = base[0]

        # Chinese/Japanese OCR can read a CJK nickname even when the
        # Cyrillic model cannot. Prefer the CJK run only for the nickname;
        # never alter the message body.
        ch_lines = [x.strip() for x in ch_text.splitlines() if x.strip()]
        for line in ch_lines:
            if cls._has_cjk(line):
                # Pick the token containing CJK/Japanese characters and
                # keep adjacent letters, digits and symbols such as _ or ★.
                import unicodedata
                tokens = []
                token = []
                for ch in line:
                    cat = unicodedata.category(ch)
                    if ch.isspace() or cat.startswith("P"):
                        if token:
                            tokens.append("".join(token))
                            token = []
                    else:
                        token.append(ch)
                if token:
                    tokens.append("".join(token))

                for token in tokens:
                    if cls._has_cjk(token):
                        player = token.strip()
                        break
                if player and cls._has_cjk(player):
                    break

        return [(player, message)]

    def recognize(self, image):
        """Multilingual game-chat OCR.

        Fixed words «Лично»/«шепчет» are recognized with the Cyrillic model.
        The nickname is additionally checked with the Chinese/English model,
        so a name such as 剑奇卡, Player_X7 or mixed text is not forced through
        a Russian-only dictionary. No nickname whitelist or character
        substitution is applied.
        """
        best_text = ""
        best_score = -100000.0
        best_engine = "OCR не распознал текст"
        variants = self._ocr_variants(image)

        for variant_name, variant in variants:
            cyr_text, cyr_conf = self._run_rapidocr(variant, self.ocr_cyrillic)
            ch_text, ch_conf = self._run_rapidocr(variant, self.ocr_chinese)

            score = self._score_ocr(cyr_text, cyr_conf)
            if score > best_score:
                best_text, best_score = cyr_text, score
                best_engine = f"RapidOCR Cyrillic + Chinese ({variant_name})"

            # The fixed Russian markers are the authoritative signal that a
            # line is a personal whisper.
            if ("Лично" in cyr_text or "Лично" in ch_text) and cyr_conf >= 0.35:
                merged = self._merge_multilingual_nickname(cyr_text, ch_text)
                if not merged:
                    merged = self._merge_multilingual_nickname(ch_text, cyr_text)
                if merged:
                    # Return a normalized two-line OCR representation so the
                    # existing Telegram parser can use it unchanged.
                    player, message = merged[0]
                    return (
                        f"Лично {player} шепчет: {message}",
                        f"RapidOCR multilingual ({variant_name})"
                    )

        # OCR.Space Russian remains the network fallback.
        for variant_name, variant in variants:
            fallback = self._ocr_space(variant)
            score = self._score_ocr(fallback, 0.0)
            if score > best_score:
                best_text, best_score = fallback, score
                best_engine = f"OCR.Space Russian ({variant_name})"
            # OCR.Space Engine 2 supports automatic language detection and
            # can read mixed Russian/Chinese/Latin text in the same line.
            if self._personal_messages(fallback):
                return fallback, f"OCR.Space Auto ({variant_name})"
            # Keep any non-empty fallback text as a candidate for diagnostics.
            if fallback and len(fallback.strip()) > 2:
                return fallback, f"OCR.Space Auto ({variant_name})"

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
