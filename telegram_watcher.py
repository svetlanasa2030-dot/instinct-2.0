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
        """Extract only personal-message rows.

        The game format is fixed:
            Лично [НИК] шепчет: [СООБЩЕНИЕ]

        "Лично" and "шепчет:" are fixed UI words. OCR may distort the
        spelling of "шепчет", especially for mixed/CJK rows, so the parser
        uses the colon as the boundary and removes the final OCR token before
        it. The nickname itself has no character whitelist.
        """
        import re

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        messages = []
        i = 0

        # Common OCR variants of the fixed word "шепчет".
        whisper_re = re.compile(
            r"(?:шепчет|uenhe?t|uenve?t|uenet|шеп[чч]ет|"
            r"whisper|whe?sp[e3]r)\\s*:",
            re.IGNORECASE
        )

        while i < len(lines):
            line = lines[i]
            marker = line.find("Лично")
            if marker < 0:
                # OCR may split "Лично" slightly; do not accept arbitrary
                # lines because only blue-row crops are allowed upstream.
                i += 1
                continue

            payload = line[marker + len("Лично"):].strip()
            payload = payload.lstrip(" )]}:;-—–")

            # Marker can be on its own line; combine with the following row.
            if not payload and i + 1 < len(lines):
                payload = lines[i + 1].strip()
                i += 1

            if not payload:
                i += 1
                continue

            player = ""
            message = ""

            # Preferred path: fixed whisper marker was recognized.
            m = whisper_re.search(payload)
            if m:
                player = payload[:m.start()].strip()
                message = payload[m.end():].strip()
            elif ":" in payload:
                # OCR distorted "шепчет", but the colon survived.
                # Everything before the final colon consists of:
                #     nickname + distorted fixed marker
                before, message = payload.rsplit(":", 1)
                before = before.rstrip()

                # Remove the OCR representation of the fixed marker.
                # Keep the nickname untouched, including CJK/symbols/spaces.
                marker_removed = re.sub(
                    r"\\s+(?:[^\\s:]{2,12})$",
                    "",
                    before
                )
                player = marker_removed.strip()
            else:
                # No separator yet: try the next line as continuation.
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if ":" in next_line:
                        payload = payload + " " + next_line
                        i += 1
                        before, message = payload.rsplit(":", 1)
                        marker_removed = re.sub(
                            r"\\s+(?:[^\\s:]{2,12})$",
                            "",
                            before.rstrip()
                        )
                        player = marker_removed.strip()

            player = player.strip(" ()[]{}:;-—–")
            message = message.strip()

            # Never send a malformed result.
            if player and message and player != message:
                messages.append((player, message))

            i += 1

        return messages

    @classmethod
    def _personal_payload(cls,text):
        """Parse [nickname] [fixed whisper marker]: [message]."""
        for line in [x.strip() for x in text.splitlines() if x.strip()]:
            if ":" not in line: continue
            before,message=line.rsplit(":",1)
            parts=before.rstrip().rsplit(None,1)
            if len(parts)!=2 or not message.strip(): continue
            player=parts[0].strip(" ()[]{}:;-—–")
            marker=parts[1].strip(" ()[]{}:;-—–").lower()
            marker_compact = marker.replace("ё", "е").replace("0", "о").replace("1", "и")
            known_variants = {
                "шепчет", "шепет", "шепчетъ",
                "uenhet", "uenvet", "uenve", "uenet",
                "uенheт", "uенveт"
            }
            if marker_compact in known_variants or (
                2 <= len(marker_compact) <= 12
                and any(ch in marker_compact for ch in "uеeнhvcтш")
            ):
                return player,message.strip()
        return None
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
        """Simple mode: return the selected chat image unchanged."""
        return [image]

    def get_chat_row_preview(self, image):
        return image

    def _ocr_variants(self, image):
        """Simple mode: one enlarged copy of the selected chat."""
        try:
            import cv2
            import numpy as np
            arr = np.asarray(image.convert("RGB"))
            enlarged = cv2.resize(
                arr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC
            )
            return [("chat", Image.fromarray(enlarged))]
        except Exception:
            return [("chat", image)]


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
        """Simple, non-blocking-friendly OCR: one image, local RapidOCR."""
        variants = self._ocr_variants(image)
        best_text = ""
        best_conf = -1.0
        best_engine = "OCR не распознал текст"

        for variant_name, variant in variants:
            for engine, label in (
                (self.ocr_cyrillic, "Cyrillic"),
                (self.ocr_chinese, "Chinese/Latin"),
            ):
                text, confidence = self._run_rapidocr(variant, engine)
                if text and confidence > best_conf:
                    best_text = text
                    best_conf = confidence
                    best_engine = f"RapidOCR {label}"

        if best_text.strip():
            return best_text.strip(), best_engine

        diagnostics = []
        if self.ocr_errors:
            diagnostics.append("RapidOCR: " + " | ".join(self.ocr_errors))
        if self.ocr_runtime_errors:
            diagnostics.append("RapidOCR runtime: " + " | ".join(self.ocr_runtime_errors[-3:]))
        return "", "OCR не распознал текст" + (
            " — " + " || ".join(diagnostics) if diagnostics else ""
        )

    def check_new_message(self, text):
        # Simple detection: any OCR text containing the fixed word "Лично"
        # means a private message is visible in the selected chat area.
        if "Лично" in (text or ""):
            return True, "ДА — найдено сообщение «Лично»"
        return False, "НЕТ — «Лично» не найдено"

    def process_ocr_text(self, text):
        """Send one short Telegram alert when 'Лично' appears anywhere in OCR."""
        self.last_results = []
        normalized = (text or "").strip()
        if "Лично" not in normalized:
            return False, "НЕТ — «Лично» не найдено"

        # Prevent the same visible OCR block from generating repeated alerts.
        key = normalized
        if key in self.seen:
            status = "ПОВТОР — не отправлено"
            self.last_results.append(("", "", False, status))
            return False, status

        ok, status = self._send("Вам написали в ЛС")
        self.last_results.append(("", "", ok, status))
        if ok:
            self.seen.add(key)
            return True, status
        return False, status

    def stop(self):
        self.stop_event.set()
