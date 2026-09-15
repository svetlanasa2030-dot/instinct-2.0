import io
import os
import time
import re
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
            self.ocr_errors.append("RapidOCR не импортирован" + (f": {RAPIDOCR_IMPORT_ERROR}" if RAPIDOCR_IMPORT_ERROR else ""))

    def _create_ocr(self, language):
        try:
            return RapidOCR(params={
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.lang_type": LangDet.CH,
                "Det.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.thresh": 0.12,
                "Det.box_thresh": 0.20,
                "Det.unclip_ratio": 1.8,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.lang_type": language,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            })
        except Exception as exc:
            self.ocr_errors.append(f"{getattr(language, 'value', language)}: {type(exc).__name__}: {exc}")
            return None

    def set_credentials(self, token, chat_id):
        self.token, self.chat_id = token.strip(), chat_id.strip()

    def set_owner_name(self, name):
        self.owner_name = name.strip()

    def set_region(self, region):
        self.region = region

    def check_telegram(self):
        return self._send("Instinct 2.0: Telegram подключён.", retries=1)

    def _send(self, text, retries=3):
        if not self.token or not self.chat_id:
            return False, "НЕ ОТПРАВЛЕНО: не задан Bot Token или Chat ID"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(retries):
            try:
                response = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
                if response.ok and response.json().get("ok"):
                    return True, "ОТПРАВЛЕНО"
            except requests.RequestException:
                pass
            if attempt < retries - 1:
                time.sleep(1)
        return False, f"НЕ ОТПРАВЛЕНО после {retries} попыток"

    def _ocr_variants(self, image):
        try:
            import cv2
            import numpy as np
            arr = np.asarray(image.convert("RGB"))
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            variants = [
                ("normal", Image.fromarray(cv2.cvtColor(cv2.resize(arr, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC), cv2.COLOR_RGB2BGR))),
                ("gray", Image.fromarray(gray)),
            ]
            # Thresholded variants make the small fixed word «Лично» much easier to read.
            for threshold in (150, 190, 220):
                bw = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)[1]
                variants.append((f"bw{threshold}", Image.fromarray(bw)))
            # Sharpened grayscale.
            blur = cv2.GaussianBlur(gray, (0, 0), 1.2)
            sharp = cv2.addWeighted(gray, 1.8, blur, -0.8, 0)
            variants.append(("sharp", Image.fromarray(sharp)))
            return variants
        except Exception:
            return [("original", image)]

    def _run_rapidocr(self, image, engine):
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
                parts, scores = [], []
                for item in result[0] or []:
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

    @staticmethod
    def _contains_personal_marker(text):
        if not text:
            return False
        s = text.replace("ё", "е").replace("Ё", "Е")
        if re.search(r"\bлично\b", s, re.IGNORECASE):
            return True

        # OCR often makes one or two mistakes in this small word.
        # Accept only very close variants, not arbitrary Russian words.
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", s):
            t = token.lower()
            t = t.replace("0", "о").replace("1", "и").replace("l", "л")
            if len(t) == 5:
                # Levenshtein distance <= 1 from «лично».
                target = "лично"
                prev = list(range(6))
                for i, a in enumerate(t, 1):
                    cur = [i]
                    for j, b in enumerate(target, 1):
                        cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j-1] + (a != b)))
                    prev = cur
                if prev[-1] <= 1:
                    return True
        return False

    def recognize(self, image):
        results = []
        for _, variant in self._ocr_variants(image):
            for engine, label in (
                (self.ocr_cyrillic, "Cyrillic"),
                (self.ocr_chinese, "Chinese/Latin"),
            ):
                text, confidence = self._run_rapidocr(variant, engine)
                if text.strip():
                    results.append((text.strip(), confidence, f"RapidOCR {label}"))

        if not results:
            diagnostics = []
            if self.ocr_errors:
                diagnostics.append("RapidOCR: " + " | ".join(self.ocr_errors))
            if self.ocr_runtime_errors:
                diagnostics.append("RapidOCR runtime: " + " | ".join(self.ocr_runtime_errors[-3:]))
            return "", "OCR не распознал текст" + (" — " + " || ".join(diagnostics) if diagnostics else "")

        # Critical fix: do not select only one OCR result.
        # If ANY preprocessing/model sees «Лично», preserve that text.
        personal = [r for r in results if self._contains_personal_marker(r[0])]
        if personal:
            best = max(personal, key=lambda r: r[1])
            return best[0], best[2]
        best = max(results, key=lambda r: r[1])
        return best[0], best[2]

    def check_new_message(self, text):
        return (
            (True, "ДА — найдено сообщение «Лично»")
            if self._contains_personal_marker(text)
            else (False, "НЕТ — «Лично» не найдено")
        )

    def process_ocr_text(self, text):
        self.last_results = []
        normalized = (text or "").strip()
        if not self._contains_personal_marker(normalized):
            return False, "НЕТ — «Лично» не найдено"

        # One alert per stable OCR block.
        key = normalized
        if key in self.seen:
            status = "ПОВТОР — не отправлено"
            self.last_results.append(("", "", False, status))
            return False, status

        alert = f"{self.owner_name} Вам пишут в лс!" if self.owner_name else "Вам пишут в лс!"
        ok, status = self._send(alert)
        self.last_results.append(("", "", ok, status))
        if ok:
            self.seen.add(key)
            return True, status
        return False, status

    def extract_personal(self, text):
        return None

    def extract_personal_all(self, text):
        return []

    def stop(self):
        self.stop_event.set()
