import threading
import time
import sys

# Make Tkinter mouse coordinates use the same physical pixels as PyAutoGUI.
# Without DPI awareness, Windows scaling (125%/150%/175%) can make a
# correctly selected chat area point to a different part of the screen.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import messagebox, ttk
import pyautogui
from telegram_watcher import TelegramWatcher


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Instinct 2.0")
        self.root.resizable(False, False)
        self.stop_event = threading.Event()
        self.worker = None
        self.x = None
        self.y = None
        self.telegram = TelegramWatcher()
        self.telegram_thread = None
        self.telegram_stop = threading.Event()
        self.telegram_log = []

        self.interval_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Готово")
        self.timer_var = tk.StringVar(value="Осталось: 00:00:00")

        frame = ttk.Frame(root, padding=18)
        frame.grid()

        ttk.Label(frame, text="Instinct 2.0", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 14)
        )

        ttk.Button(frame, text="Указать точку на экране", command=self.select_point).grid(
            row=1, column=0, columnspan=2, pady=6, sticky="ew"
        )

        self.point_label = ttk.Label(frame, text="Точка: не выбрана")
        self.point_label.grid(row=2, column=0, columnspan=2, pady=6)

        ttk.Label(frame, text="Интервал, мин:").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.interval_var, width=14).grid(row=3, column=1, sticky="w")

        ttk.Label(
            frame,
            text="В выбранной точке: ↑ → Enter → 3 сек → Enter",
        ).grid(row=4, column=0, columnspan=2, pady=(10, 6))

        ttk.Button(frame, text="Настроить Telegram", command=self.configure_telegram).grid(row=4, column=0, columnspan=2, pady=6, sticky="ew")
        ttk.Button(frame, text="Проверить Telegram", command=self.test_telegram).grid(row=5, column=0, columnspan=2, pady=6, sticky="ew")
        ttk.Button(frame, text="Выбрать область чата", command=self.select_chat_region).grid(row=6, column=0, columnspan=2, pady=6, sticky="ew")
        ttk.Button(frame, text="Проверить область чата", command=self.check_chat_region).grid(row=7, column=0, columnspan=2, pady=6, sticky="ew")
        ttk.Button(frame, text="Журнал Telegram", command=self.show_telegram_log).grid(row=8, column=0, columnspan=2, pady=6, sticky="ew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=2, pady=12)
        self.start_btn = ttk.Button(buttons, text="Старт", command=self.start)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(buttons, text="Стоп", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)

        ttk.Label(frame, textvariable=self.timer_var, font=("Segoe UI", 12, "bold")).grid(row=10, column=0, columnspan=2, pady=(4, 2))
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=11, column=0, columnspan=2, pady=4
        )
        ttk.Label(frame, text="F8 — запуск / остановка").grid(
            row=12, column=0, columnspan=2, pady=(10, 0)
        )

        self.root.bind("<F8>", lambda _event: self.toggle())
        self.root.after(500, self.start_telegram)

    def configure_telegram(self):
        win = tk.Toplevel(self.root)
        win.title("Настроить Telegram")
        frame = ttk.Frame(win, padding=14)
        frame.grid()
        token_var = tk.StringVar(value=self.telegram.token)
        chat_var = tk.StringVar(value=self.telegram.chat_id)
        ttk.Label(frame, text="Bot Token:").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=token_var, width=42, show="*").grid(row=0, column=1, pady=5)
        ttk.Label(frame, text="Chat ID:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=chat_var, width=42).grid(row=1, column=1, pady=5)
        def save():
            self.telegram.set_credentials(token_var.get(), chat_var.get())
            self.status_var.set("Telegram сохранён")
            win.destroy()
        ttk.Button(frame, text="Сохранить", command=save).grid(row=2, column=0, columnspan=2, pady=10)

    def test_telegram(self):
        if not self.telegram.token or not self.telegram.chat_id:
            messagebox.showerror("Telegram", "Укажите Bot Token и Chat ID.")
            return
        ok, status = self.telegram.check_telegram()
        self.status_var.set(status)
        if not ok:
            messagebox.showerror("Telegram", status)

    def select_chat_region(self):
        self.root.withdraw()
        selector = tk.Toplevel()
        selector.attributes("-fullscreen", True)
        selector.attributes("-topmost", True)
        selector.attributes("-alpha", 0.25)
        selector.configure(bg="black")
        selector.config(cursor="crosshair")

        canvas = tk.Canvas(selector, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        start = {}
        rect = None

        def press(event):
            nonlocal rect
            start["x"], start["y"] = event.x, event.y
            if rect is not None:
                canvas.delete(rect)
            rect = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="red", width=3
            )

        def drag(event):
            nonlocal rect
            if "x" not in start:
                return
            canvas.coords(rect, start["x"], start["y"], event.x, event.y)

        def release(event):
            if "x" in start:
                x1, y1 = start["x"], start["y"]
                self.telegram.set_region((
                    min(x1, event.x), min(y1, event.y),
                    abs(event.x - x1), abs(event.y - y1)
                ))
            selector.destroy()
            self.root.deiconify()

        selector.bind("<ButtonPress-1>", press)
        selector.bind("<B1-Motion>", drag)
        selector.bind("<ButtonRelease-1>", release)
        selector.bind("<Escape>", lambda _e: (selector.destroy(), self.root.deiconify()))
        selector.focus_force()

    def start_telegram(self):
        if self.telegram_thread and self.telegram_thread.is_alive():
            return
        self.telegram_stop.clear()
        self.telegram_thread = threading.Thread(target=self.telegram_loop, daemon=True)
        self.telegram_thread.start()

    def telegram_loop(self):
        # Chat is checked exactly every 3 seconds. Point automation has its
        # own independent worker and is intentionally not touched here.
        while not self.telegram_stop.wait(3):
            if not self.telegram.region:
                continue
            x, y, w, h = self.telegram.region
            try:
                image = pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
                text, ocr_engine = self.telegram.recognize(image)
                ok, status = self.telegram.process_ocr_text(text)
                messages = self.telegram.extract_personal_all(text)

                if messages:
                    for player, message, sent_ok, message_status in self.telegram.last_results:
                        event = f"{message_status} — {player}: {message}"
                        self.root.after(0, self.add_telegram_log, event)
                    self.root.after(0, self.status_var.set,
                                     f"Чат: найдено {len(messages)} личных сообщений | {status or 'без новых'}")
                else:
                    self.root.after(0, self.status_var.set, "Чат: новых личных сообщений нет")
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"Чат: ошибка — {exc}")

    def add_telegram_log(self, message):
        stamp = time.strftime("%H:%M:%S")
        entry = f"{stamp}  {message}"
        self.telegram_log.append(entry)
        # Keep the in-memory journal bounded.
        if len(self.telegram_log) > 300:
            del self.telegram_log[:-300]

    def show_telegram_log(self):
        win = tk.Toplevel(self.root)
        win.title("Журнал Telegram")
        win.geometry("720x420")
        box = tk.Text(win, wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", "\n".join(self.telegram_log) or "Журнал пока пуст.")
        box.configure(state="disabled")

    def check_chat_region(self):
        if not self.telegram.region:
            messagebox.showwarning("Область чата", "Сначала выберите область чата.")
            return
        x, y, w, h = self.telegram.region
        try:
            # PyAutoGUI uses physical screen pixels. The app is made
            # per-monitor-DPI-aware above so these saved coordinates match.
            image = pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
            text, ocr_engine = self.telegram.recognize(image)
            is_new, new_status = self.telegram.check_new_message(text)
            self.status_var.set(f"Проверка чата: {new_status}")
            viewer = tk.Toplevel(self.root)
            viewer.title("Проверить область чата")
            viewer.geometry("760x620")
            ttk.Label(viewer, text=f"Координаты: X={x}, Y={y}, W={w}, H={h}").pack(pady=8)
            ttk.Label(viewer, text=f"Новое сообщение: {'ДА' if is_new else 'НЕТ'}", font=("Segoe UI", 11, "bold")).pack(pady=(0, 4))
            ttk.Label(viewer, text=new_status).pack(pady=(0, 4))
            ttk.Label(viewer, text=f"OCR: {ocr_engine}").pack(pady=(0, 8))
            box = tk.Text(viewer, wrap="word")
            box.pack(fill="both", expand=True, padx=10, pady=10)
            box.insert("1.0", text or "Текст не распознан.")
            box.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("OCR", str(exc))

    def select_point(self):
        if self.worker and self.worker.is_alive():
            return

        self.root.withdraw()
        selector = tk.Toplevel()
        selector.attributes("-fullscreen", True)
        selector.attributes("-topmost", True)
        selector.attributes("-alpha", 0.25)
        selector.configure(bg="black")
        selector.config(cursor="crosshair")

        def choose(event):
            self.x, self.y = event.x, event.y
            selector.destroy()
            self.root.deiconify()
            self.point_label.config(text=f"Точка: X={self.x}, Y={self.y}")
            self.status_var.set(f"Точка выбрана: X={self.x}, Y={self.y}")

        selector.bind("<Button-1>", choose)
        selector.bind("<Escape>", lambda _event: (selector.destroy(), self.root.deiconify()))
        selector.focus_force()

    def validate(self):
        if self.x is None or self.y is None:
            raise ValueError("Сначала нажмите «Указать точку на экране».")
        try:
            interval_minutes = float(self.interval_var.get())
        except ValueError:
            raise ValueError("Интервал должен быть числом.")
        if interval_minutes < 0:
            raise ValueError("Интервал не может быть отрицательным.")
        return interval_minutes * 60

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            interval = self.validate()
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        self.stop_event.clear()
        self.worker = threading.Thread(
            target=self.run_loop, args=(self.x, self.y, interval), daemon=True
        )
        self.worker.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set(f"Запущено: X={self.x}, Y={self.y}")
        self.timer_var.set("Осталось: 00:00:00")

    def stop(self):
        self.stop_event.set()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Остановлено")
        self.timer_var.set("Осталось: 00:00:00")

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop()
        else:
            self.start()

    def run_loop(self, x, y, interval):
        while not self.stop_event.is_set():
            try:
                pyautogui.moveTo(x, y, duration=0.15)
                pyautogui.click(x, y)
                pyautogui.press("up")
                pyautogui.press("enter")

                if self.stop_event.wait(3):
                    break

                pyautogui.press("enter")
                self.root.after(0, self.status_var.set, f"Выполнено в ({x}, {y})")

                end_time = time.monotonic() + interval
                while not self.stop_event.is_set():
                    remaining = max(0, end_time - time.monotonic())
                    self.root.after(
                        0,
                        self.timer_var.set,
                        f"Осталось: {int(remaining // 3600):02d}:{int((remaining % 3600) // 60):02d}:{int(remaining % 60):02d}"
                    )
                    if remaining <= 0:
                        break
                    if self.stop_event.wait(min(0.1, remaining)):
                        break
                if self.stop_event.is_set():
                    break
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"Ошибка: {exc}")
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
