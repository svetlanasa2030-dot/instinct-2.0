import io
import os
import time
import requests
from PIL import Image

RAPIDOCR_IMPORT_ERROR = ""
try:
    from rapidocr import RapidOCR, EngineType, LangDet, LangRec, ModelType, OCRVersion
except Exception as exc:
    RapidOCR = None
    EngineType = LangDet = LangRec = ModelType = OCRVersion = None
    RAPIDOCR_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


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
        self.ocr_errors = []
        self.ocr_runtime_errors = []
        self.ocrspace_error = ""
        if RapidOCR:
            self.ocr_cyrillic = self._create_ocr(LangRec.CYRILLIC)
            self.ocr_chinese = self._create_ocr(LangRec.CH)
        else:
            self.ocr_errors.append(
                "RapidOCR не импортирован"
                + (f": {RAPIDOCR_IMPORT_ERROR}" if RAPIDOCR_IMPORT_ERROR else "")
            )
        self.ocr_init_status = (
            "RapidOCR: Cyrillic + Chinese"
            if (self.ocr_cyrillic or self.ocr_chinese)
            else "RapidOCR не загрузился"
        )

    def _create_ocr(self, language):
        try:
            return RapidOCR(params={
                "Det.engine_type": EngineType.ONNXRUNTIME,
                # MULTI detection is more tolerant of mixed-language player names.
                "Det.lang_type": LangDet.CH,
                "Det.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.thresh": 0.20,
                "Det.box_thresh": 0.30,
                "Det.unclip_ratio": 1.8,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.lang_type": language,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            })
        except Exception as exc:
            self.ocr_errors.append(f"{language.value}: {type(exc).__name__}: {exc}")
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

            # The game always uses the fixed separator "шепчет:".
            # OCR can distort this fixed word (for example "uenHeT:"),
            # especially with Chinese/mixed-language names. Therefore do NOT
            # trust OCR spelling of "шепчет". If a colon is present, the token
            # immediately before it is the fixed whisper marker; everything
            # before that token is the nickname and everything after it is the
            # message. This also preserves arbitrary nickname characters.
            whisper = " шепчет:"
            if whisper in payload:
                player, message = payload.split(whisper, 1)
            elif "шепчет:" in payload:
                player, message = payload.split("шепчет:", 1)
            elif ":" in payload:
                before, message = payload.split(":", 1)
                before = before.rstrip()
                # Remove the OCR representation of the fixed "шепчет" token.
                # Only the final whitespace-delimited token is removed, so a
                # nickname may contain spaces and arbitrary symbols.
                parts = before.rsplit(None, 1)
                if len(parts) == 2:
                    player = parts[0]
                else:
                    player = ""
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
                self.ocrspace_error = f"HTTP {response.status_code}"
                return ""
            data = response.json()
            if data.get("IsErroredOnProcessing"):
                errors = data.get("ErrorMessage") or data.get("ErrorDetails") or "OCR.Space error"
                self.ocrspace_error = str(errors)
                return ""
            parsed = data.get("ParsedResults") or []
            return "\n".join(p.get("ParsedText", "") for p in parsed).strip()
        except (requests.RequestException, ValueError, OSError) as exc:
            self.ocrspace_error = f"{type(exc).__name__}: {exc}"
            return ""

    def _blue_row_crops(self, image):
        """Find chat rows containing the blue personal-message marker.
        The original row (not only the blue pixels) is sent to OCR so the
        nickname/message can contain arbitrary colors and characters.
        """
        try:
            import cv2
            import numpy as np

            arr = np.asarray(image.convert("RGB"))
            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

            # Broad HSV range for the blue used by the game's "Лично" marker
            # and blue "шепчет:" text. Saturation/value thresholds tolerate
            # anti-aliasing and small rendering differences.
            lower = np.array([98, 70, 80], dtype=np.uint8)
            upper = np.array([125, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower, upper)

            # The blue "Лично" marker is at the left edge of the chat row.
            # Ignore blue UI/background elements elsewhere in the row.
            marker_zone = mask.copy()
            marker_x = max(1, int(marker_zone.shape[1] * 0.24))
            marker_zone[:, marker_x:] = 0

            # Horizontal projection: only the left-side blue marker identifies
            # a personal-message row. Purple system text is excluded.
            projection = (marker_zone > 0).sum(axis=1)
            bands = []
            start = None
            for y, count in enumerate(projection):
                if count >= 2 and start is None:
                    start = y
                elif count < 2 and start is not None:
                    if y - start >= 1:
                        bands.append((max(0, start - 7), min(arr.shape[0], y + 7)))
                    start = None
            if start is not None:
                bands.append((max(0, start - 7), arr.shape[0]))

            # Merge close bands so one wrapped message remains one crop.
            merged = []
            for y1, y2 in bands:
                if merged and y1 <= merged[-1][1] + 5:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], y2))
                else:
                    merged.append((y1, y2))

            crops = []
            for y1, y2 in merged:
                # Keep the complete row: nickname/message may use any color.
                crop_arr = arr[y1:y2, :, :]
                crop = Image.fromarray(crop_arr)
                crop = crop.resize(
                    (crop.width * 6, crop.height * 6),
                    Image.Resampling.LANCZOS
                )
                crops.append(crop)

                # Add a high-contrast text-only version. This is important
                # when the game background is textured/transparent and OCR
                # cannot detect glyphs on the original screenshot.
                try:
                    gray = cv2.cvtColor(crop_arr, cv2.COLOR_RGB2GRAY)
                    hsv_row = cv2.cvtColor(crop_arr, cv2.COLOR_RGB2HSV)
                    bright = cv2.inRange(gray, 105, 255)
                    saturated = cv2.inRange(hsv_row, np.array([0, 45, 70]), np.array([179, 255, 255]))
                    clean = cv2.bitwise_or(bright, saturated)
                    # Remove isolated noise while preserving small glyphs.
                    clean = cv2.morphologyEx(
                        clean, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8)
                    )
                    clean = cv2.resize(
                        clean, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC
                    )
                    crops.append(Image.fromarray(clean))
                except Exception:
                    pass
            return crops
        except Exception:
            return []

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
            gray_up = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray_up = clahe.apply(gray_up)
            variants.append(("gray", Image.fromarray(gray_up)))

            # Game text is bright on a very dark background. Adaptive and
            # Otsu thresholding help the detector find small glyphs.
            adaptive = cv2.adaptiveThreshold(
                gray_up, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 31, 7
            )
            variants.append(("adaptive", Image.fromarray(adaptive)))

            _, otsu = cv2.threshold(
                gray_up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            variants.append(("otsu", Image.fromarray(otsu)))

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
        cjk = sum("\\u3400" <= ch <= "\\u4dbf" or "\\u4e00" <= ch <= "\\u9fff" for ch in text)
        score += cjk * 18.0
        if "шепчет" in text.lower():
            score += 5000.0
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
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if msg not in self.ocr_runtime_errors:
                self.ocr_runtime_errors.append(msg)
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
        """Robust multilingual OCR for the game chat.

        Do not require a perfect OCR confidence score before parsing a
        personal message. Small game-chat text can temporarily produce a low
        confidence score even when the words are usable.
        """
        best_text = ""
        best_score = -100000.0
        best_engine = "OCR не распознал текст"

        # First prioritize rows identified by the blue "Лично" marker.
        # OCR receives the complete original row, preserving arbitrary
        # nickname/message colors and symbols.
        blue_rows = self._blue_row_crops(image)
        blue_variants = [(f"blue_row_{i + 1}", row) for i, row in enumerate(blue_rows)]
        variants = blue_variants + self._ocr_variants(image)

        # Local OCR: test every image variant with both recognition dictionaries.
        # IMPORTANT: only blue_row variants are allowed to produce a Telegram
        # personal message. Full-chat OCR is diagnostic only, so purple/red/
        # system messages can never be sent accidentally.
        for variant_name, variant in variants:
            cyr_text, cyr_conf = self._run_rapidocr(variant, self.ocr_cyrillic)
            ch_text, ch_conf = self._run_rapidocr(variant, self.ocr_chinese)

            for txt, conf, label in (
                (cyr_text, cyr_conf, "Cyrillic"),
                (ch_text, ch_conf, "Chinese/Latin"),
            ):
                score = self._score_ocr(txt, conf)
                if score > best_score:
                    best_text, best_score = txt, score
                    best_engine = f"RapidOCR {label} ({variant_name})"

            # Accept a usable personal-message marker even when confidence is
            # below the previous 0.35 threshold.
            # Personal-message extraction is enabled ONLY for rows selected by
            # the blue "Лично" marker. OCR of the complete chat cannot trigger
            # Telegram sending anymore.
            if not variant_name.startswith("blue_row_"):
                continue

            cyr_personal = self._personal_messages(cyr_text)
            ch_personal = self._personal_messages(ch_text)

            if cyr_personal:
                player, message = self._merge_multilingual_nickname(
                    cyr_text, ch_text
                )[0] if self._merge_multilingual_nickname(cyr_text, ch_text) else cyr_personal[0]
                return (
                    f"Лично {player} шепчет: {message}",
                    f"RapidOCR multilingual ({variant_name})"
                )

            if ch_personal:
                player, message = ch_personal[0]
                return (
                    f"Лично {player} шепчет: {message}",
                    f"RapidOCR multilingual ({variant_name})"
                )

        # OCR.Space fallback: try all variants and accept any personal message.
        # Engine 2 + language=auto can detect multiple languages in one image.
        fallback_best = ""
        for variant_name, variant in variants:
            fallback = self._ocr_space(variant)
            if fallback:
                if not fallback_best:
                    fallback_best = fallback
                personal = self._personal_messages(fallback) if variant_name.startswith("blue_row_") else []
                if personal:
                    player, message = personal[0]
                    return (
                        f"Лично {player} шепчет: {message}",
                        f"OCR.Space Auto ({variant_name})"
                    )

        if fallback_best and len(fallback_best.strip()) > 1:
            return fallback_best, "OCR.Space Auto"

        diagnostics = []
        if self.ocr_errors:
            diagnostics.append("RapidOCR init: " + " | ".join(self.ocr_errors))
        if self.ocr_runtime_errors:
            diagnostics.append("RapidOCR runtime: " + " | ".join(self.ocr_runtime_errors[-3:]))
        if self.ocrspace_error:
            diagnostics.append("OCR.Space: " + self.ocrspace_error)
        if diagnostics:
            return "", "OCR не распознал текст — " + " || ".join(diagnostics)

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
