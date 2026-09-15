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

    def __init__(self, token="", chat_id="", region=None, ocrspace_key="", owner_name=""):
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.owner_name = owner_name.strip()
        self.region = region
        self.ocrspace_key = (ocrspace_key or os.getenv("OCRSPACE_API_KEY", "helloworld")).strip()
        self.stop_event = __import__("threading").Event()
        self.seen = set()
        self.last_results = []
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

    def set_owner_name(self, name):
        self.owner_name = name.strip()

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
        import re

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        messages = []
        for line in lines:
            marker = re.search(r"Лично", line, re.IGNORECASE)
            if not marker:
                continue

            payload = line[marker.end():].strip().lstrip(" )]}:;-—–")
            if not payload:
                continue

            # The exact game row is: Лично [ИМЯ] шепчет: [СООБЩЕНИЕ].
            # The alert only needs the fact that "Лично" was found, while
            # keeping extraction available for the journal.
            if ":" in payload:
                before, message = payload.rsplit(":", 1)
                parts = before.split()
                player = parts[0].strip("()[]{}:;-—–") if parts else ""
                if len(parts) > 1:
                    player = " ".join(parts[:-1]).strip("()[]{}:;-—–")
                message = message.strip()
            else:
                player = payload.strip("()[]{}:;-—–")
                message = ""

            if player and message and player != message:
                messages.append((player, message))

        return messages

    @classmethod
    def extract_personal(cls, text):
        messages = cls._personal_messages(text)
        return messages[0] if messages else None

    @classmethod
    def extract_personal_all(cls, text):
        return cls._personal_messages(text)

    def _ocr_space(self, image):
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
        return [image]

    def get_chat_row_preview(self, image):
        return image

    def _ocr_variants(self, image):
        try:
            import cv2
            import numpy as np
            arr = np.asarray(image.convert("RGB"))
            enlarged = cv2.resize(arr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            return [("chat", Image.fromarray(enlarged))]
        except Exception:
            return [("chat", image)]

    @staticmethod
    def _score_ocr(text, confidence=0.0):
        if not text:
            return -100000.0
        score = float(confidence) * 100.0
        if "Лично" in text:
            score += 10000.0
        cyr = sum(("А" <= ch <= "я") or ch in "Ёё" for ch in text)
        score += cyr * 12.0
        cjk = sum("\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff" for ch in text)
        score += cjk * 18.0
        if "шепчет" in text.lower():
            score += 5000.0
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
    def _contains_personal_marker(text):
        import re
        if not text:
            return False
        normalized = text.replace("ё", "е").replace("Ё", "Е")
        # Accept small OCR spacing/punctuation differences around the word.
        return bool(re.search(r"Лично", normalized, re.IGNORECASE))

    def recognize(self, image):
        """Run both OCR models and keep all useful results.

        Important: do not discard the Cyrillic result just because another
        OCR model has a higher confidence. The trigger word "Лично" is
        Russian, so any OCR result containing it must be preserved.
        """
        variants = self._ocr_variants(image)
        results = []

        for variant_name, variant in variants:
            for engine, label in (
                (self.ocr_cyrillic, "Cyrillic"),
                (self.ocr_chinese, "Chinese/Latin"),
            ):
                text, confidence = self._run_rapidocr(variant, engine)
                if text.strip():
                    results.append((text.strip(), confidence, f"RapidOCR {label}"))

        if results:
            personal = [r for r in results if self._contains_personal_marker(r[0])]
            if personal:
                # Prefer the result that actually contains the trigger.
                best = max(personal, key=lambda item: item[1])
            else:
                best = max(results, key=lambda item: item[1])
            return best[0], best[2]

        diagnostics = []
        if self.ocr_errors:
            diagnostics.append("RapidOCR: " + " | ".join(self.ocr_errors))
        if self.ocr_runtime_errors:
            diagnostics.append("RapidOCR runtime: " + " | ".join(self.ocr_runtime_errors[-3:]))
        return "", "OCR не распознал текст" + (
            " — " + " || ".join(diagnostics) if diagnostics else ""
        )

    def check_new_message(self, text):
        if self._contains_personal_marker(text):
            return True, "ДА — найдено сообщение «Лично»"
        return False, "НЕТ — «Лично» не найдено"

    def process_ocr_text(self, text):
        """Send exactly one alert for each newly seen OCR block containing 'Лично'."""
        self.last_results = []
        normalized = (text or "").strip()
        if not self._contains_personal_marker(normalized):
            return False, "НЕТ — «Лично» не найдено"

        key = normalized
        if key in self.seen:
            status = "ПОВТОР — не отправлено"
            self.last_results.append(("", "", False, status))
            return False, status

        # Desired Telegram format: "<имя> Вам пишут в лс!"
        alert = f"{self.owner_name} Вам пишут в лс!" if self.owner_name else "Вам пишут в лс!"
        ok, status = self._send(alert)
        self.last_results.append(("", "", ok, status))
        if ok:
            self.seen.add(key)
            return True, status
        return False, status

    def stop(self):
        self.stop_event.set()
