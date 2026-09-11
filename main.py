import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import pyautogui


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Instinct 2.0 — Screen Point Automator")
        self.root.resizable(False, False)
        self.stop_event = threading.Event()
        self.worker = None

        self.x_var = tk.StringVar(value="500")
        self.y_var = tk.StringVar(value="500")
        self.interval_var = tk.StringVar(value="60")
        self.status_var = tk.StringVar(value="Готово")

        frame = ttk.Frame(root, padding=18)
        frame.grid()

        ttk.Label(frame, text="Instinct 2.0", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 14)
        )

        ttk.Label(frame, text="X координата:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.x_var, width=14).grid(row=1, column=1, sticky="w")

        ttk.Label(frame, text="Y координата:").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.y_var, width=14).grid(row=2, column=1, sticky="w")

        ttk.Label(frame, text="Интервал, сек:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.interval_var, width=14).grid(row=3, column=1, sticky="w")

        ttk.Label(
            frame,
            text="Цикл: клик по X,Y → ↑ → Enter → 3 сек → Enter",
        ).grid(row=4, column=0, columnspan=2, pady=(10, 5))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, pady=12)
        self.start_btn = ttk.Button(buttons, text="Старт", command=self.start)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(buttons, text="Стоп", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)

        ttk.Label(frame, textvariable=self.status_var).grid(
            row=6, column=0, columnspan=2, pady=(4, 0)
        )
        ttk.Label(frame, text="F8 — запуск / остановка").grid(
            row=7, column=0, columnspan=2, pady=(10, 0)
        )

        self.root.bind("<F8>", lambda _event: self.toggle())

    def validate(self):
        try:
            x = int(self.x_var.get())
            y = int(self.y_var.get())
            interval = float(self.interval_var.get())
        except ValueError:
            raise ValueError("X, Y и интервал должны быть числами.")

        width, height = pyautogui.size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Координаты должны быть в пределах экрана: 0..{width-1}, 0..{height-1}.")
        if interval < 0:
            raise ValueError("Интервал не может быть отрицательным.")
        return x, y, interval

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            x, y, interval = self.validate()
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        self.stop_event.clear()
        self.worker = threading.Thread(target=self.run_loop, args=(x, y, interval), daemon=True)
        self.worker.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set(f"Запущено: ({x}, {y})")

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

    def run_loop(self, x, y, interval):
        while not self.stop_event.is_set():
            try:
                pyautogui.click(x, y)
                pyautogui.press("up")
                pyautogui.press("enter")

                if self.stop_event.wait(3):
                    break

                pyautogui.press("enter")
                self.root.after(0, self.status_var.set, f"Выполнено: ({x}, {y})")

                if self.stop_event.wait(interval):
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
