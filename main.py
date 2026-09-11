import threading
import tkinter as tk
from tkinter import messagebox, ttk
import pyautogui


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Instinct 2.0 — Point Automator")
        self.root.resizable(False, False)
        self.stop_event = threading.Event()
        self.worker = None
        self.x = None
        self.y = None

        self.interval_var = tk.StringVar(value="60")
        self.status_var = tk.StringVar(value="Готово")
        self.timer_var = tk.StringVar(value="До следующего выполнения: —")

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

        ttk.Label(frame, text="Интервал, сек:").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.interval_var, width=14).grid(row=3, column=1, sticky="w")

        ttk.Label(
            frame,
            text="В выбранной точке: ↑ → Enter → 3 сек → Enter",
        ).grid(row=4, column=0, columnspan=2, pady=(10, 6))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, pady=12)
        self.start_btn = ttk.Button(buttons, text="Старт", command=self.start)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(buttons, text="Стоп", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)

        ttk.Label(frame, textvariable=self.timer_var, font=("Segoe UI", 12, "bold")).grid(row=6, column=0, columnspan=2, pady=(4, 2))
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=7, column=0, columnspan=2, pady=4
        )
        ttk.Label(frame, text="F8 — запуск / остановка").grid(
            row=8, column=0, columnspan=2, pady=(10, 0)
        )

        self.root.bind("<F8>", lambda _event: self.toggle())

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
            interval = float(self.interval_var.get())
        except ValueError:
            raise ValueError("Интервал должен быть числом.")
        if interval < 0:
            raise ValueError("Интервал не может быть отрицательным.")
        return interval

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
        self.timer_var.set("До следующего выполнения: сейчас")

    def stop(self):
        self.stop_event.set()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Остановлено")
        self.timer_var.set("До следующего выполнения: —")

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop()
        else:
            self.start()

    def run_loop(self, x, y, interval):
        while not self.stop_event.is_set():
            try:
                pyautogui.moveTo(x, y, duration=0.15)
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
                        f"До следующего выполнения: {remaining:.1f} сек"
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
