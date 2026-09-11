import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pyautogui

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Instinct 2.0")
        self.root.resizable(False, False)

        self.stop_event = threading.Event()
        self.worker = None
        self.template_path = tk.StringVar()
        self.interval_var = tk.StringVar(value="60")
        self.threshold_var = tk.StringVar(value="0.85")
        self.status_var = tk.StringVar(value="Готово")

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Instinct 2.0", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(0, 14)
        )

        ttk.Label(frame, text="Изображение точки:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.template_path, width=36).grid(row=1, column=1, padx=8)
        ttk.Button(frame, text="Выбрать", command=self.choose_template).grid(row=1, column=2)

        ttk.Label(frame, text="Интервал, сек:").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.interval_var, width=12).grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frame, text="Точность поиска:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.threshold_var, width=12).grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(frame, text="Последовательность:").grid(row=4, column=0, sticky="nw", pady=8)
        ttk.Label(frame, text="найти точку → клик → ↑ → Enter → 3 сек → Enter").grid(
            row=4, column=1, columnspan=2, sticky="w", pady=8
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, pady=(14, 8))
        self.start_btn = ttk.Button(buttons, text="Старт", command=self.start)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(buttons, text="Стоп", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)

        ttk.Label(frame, textvariable=self.status_var).grid(row=6, column=0, columnspan=3, pady=(8, 0))
        ttk.Label(frame, text="F8 — остановка / запуск", foreground="#666").grid(
            row=7, column=0, columnspan=3, pady=(10, 0)
        )

        self.root.bind("<F8>", lambda _event: self.toggle())

    def choose_template(self):
        path = filedialog.askopenfilename(
            title="Выберите изображение точки",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")],
        )
        if path:
            self.template_path.set(path)

    def validate(self):
        path = self.template_path.get().strip()
        try:
            interval = float(self.interval_var.get())
            threshold = float(self.threshold_var.get())
        except ValueError:
            raise ValueError("Интервал и точность должны быть числами.")
        if not path:
            raise ValueError("Выберите изображение точки.")
        if interval < 0:
            raise ValueError("Интервал не может быть отрицательным.")
        if not 0.5 <= threshold <= 0.99:
            raise ValueError("Точность должна быть в диапазоне 0.50–0.99.")
        if cv2 is None:
            raise ValueError("Не установлены OpenCV/numpy. Запустите pip install -r requirements.txt.")
        return path, interval, threshold

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            path, interval, threshold = self.validate()
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        self.stop_event.clear()
        self.worker = threading.Thread(
            target=self.run_loop, args=(path, interval, threshold), daemon=True
        )
        self.worker.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Запущено")

    def stop(self):
        self.stop_event.set()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Остановлено")

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop()
        else:
            self.start()

    def find_template(self, path: str, threshold: float):
        template = cv2.imread(path, cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError("Не удалось открыть изображение точки.")

        screenshot = pyautogui.screenshot()
        screen = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            return None, max_val, template.shape[:2]

        h, w = template.shape[:2]
        x = max_loc[0] + w // 2
        y = max_loc[1] + h // 2
        return (x, y), max_val, (h, w)

    def run_loop(self, path: str, interval: float, threshold: float):
        while not self.stop_event.is_set():
            try:
                point, score, _ = self.find_template(path, threshold)
                if point is None:
                    self.root.after(0, self.status_var.set, f"Точка не найдена (score={score:.2f})")
                else:
                    x, y = point
                    pyautogui.click(x, y)
                    pyautogui.press("up")
                    pyautogui.press("enter")
                    self.root.after(0, self.status_var.set, f"Выполнено: ({x}, {y}), score={score:.2f}")
                    if self.stop_event.wait(3):
                        break
                    pyautogui.press("enter")

                if self.stop_event.wait(interval):
                    break
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"Ошибка: {exc}")
                self.root.after(0, self.stop)
                break

        self.root.after(0, self._worker_finished)

    def _worker_finished(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")


def main():
    pyautogui.PAUSE = 0.08
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
