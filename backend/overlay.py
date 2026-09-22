"""Оверлей поверх игры: компактное окно с рекомендацией.

Читает готовое состояние с localhost:3000/state и ничего не считает сам,
поэтому не влияет на игру. Окно можно таскать мышью, Esc — закрыть.

Запуск:  python overlay.py   (при запущенном app.py)
"""

import json
import tkinter as tk
import urllib.error
import urllib.request

STATE_URL = "http://localhost:3000/state"
REFRESH_MS = 1500

# Сколько неудачных опросов подряд терпим, прежде чем закрыться.
# Оверлей без движка бесполезен: закрыли консоль — закрылся и он.
MAX_FAILURES = 4

BG = "#0d1117"
FG = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#e3b341"
GOOD = "#3fb950"


class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dota2Helper")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)
        self.root.configure(bg=BG)
        self.root.geometry("+40+40")

        self.body = tk.Frame(self.root, bg=BG, padx=12, pady=10)
        self.body.pack(fill="both", expand=True)

        self.header = tk.Label(
            self.body,
            text="ожидаю игру...",
            font=("Segoe UI", 11, "bold"),
            bg=BG,
            fg=ACCENT,
            anchor="w",
            justify="left",
        )
        self.header.pack(fill="x")

        self.enemies = tk.Label(
            self.body, text="", font=("Segoe UI", 8), bg=BG, fg=DIM,
            anchor="w", justify="left", wraplength=320,
        )
        self.enemies.pack(fill="x", pady=(2, 6))

        self.sections = {}

        for key, title in (
            ("starting", "СТАРТ"),
            ("early", "РАННЯЯ"),
            ("core", "КОР"),
            ("situational", "ПРОТИВ ЭТОГО ПИКА"),
            ("skills", "СКИЛЛЫ"),
            ("talents", "ТАЛАНТЫ"),
        ):
            caption = tk.Label(
                self.body, text=title, font=("Segoe UI", 7, "bold"),
                bg=BG, fg=DIM, anchor="w",
            )
            caption.pack(fill="x", pady=(6, 0))

            value = tk.Label(
                self.body, text="-", font=("Segoe UI", 9), bg=BG, fg=FG,
                anchor="w", justify="left", wraplength=320,
            )
            value.pack(fill="x")

            self.sections[key] = (caption, value)

        # Перетаскивание окна мышью.
        for widget in (self.root, self.body, self.header):
            widget.bind("<Button-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag)

        self.root.bind("<Escape>", lambda event: self.root.destroy())

        self._offset = (0, 0)
        self._failures = 0

        self.refresh()

    def _grab(self, event):
        self._offset = (event.x_root - self.root.winfo_x(),
                        event.y_root - self.root.winfo_y())

    def _drag(self, event):
        x = event.x_root - self._offset[0]
        y = event.y_root - self._offset[1]

        self.root.geometry(f"+{x}+{y}")

    def fetch(self):
        try:
            with urllib.request.urlopen(STATE_URL, timeout=2) as response:
                return json.load(response)

        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    def refresh(self):
        state = self.fetch()

        if state is None:
            self._failures += 1

            if self._failures >= MAX_FAILURES:
                # Движок закрыли — оверлею дальше делать нечего.
                self.root.destroy()

                return

            self.header.config(
                text=f"нет связи с Helper ({self._failures}/{MAX_FAILURES})",
                fg="#f85149",
            )

            self.root.after(REFRESH_MS, self.refresh)

            return

        self._failures = 0

        if state.get("waiting") or state.get("error"):
            self.header.config(text="ожидаю игру...", fg=ACCENT)

        else:
            known = state.get("enemies_known", 0)

            self.header.config(
                text=f"{state['hero'].replace('_', ' ').title()}"
                     f"  ·  {state['position'].replace('POSITION_', 'поз. ')}"
                     f"  ·  {state['winrate']}%",
                fg=ACCENT,
            )

            match = state.get("match") or {}
            enemies = ", ".join(
                name.replace("_", " ").title() for name in match.get("enemies", [])
            )

            lane = ", ".join(
                name.replace("_", " ").title()
                for name in state.get("lane_against", [])
            )

            self.enemies.config(
                text=f"врагов известно {known}/5"
                     + (f": {enemies}" if enemies else "")
                     + (f"\nна линии против: {lane}" if lane else "")
                     + f"\nпо {state['matches']} матчам"
            )

            self._set("starting", ", ".join(
                item["display"] for item in state.get("starting", [])[:7]
            ))

            for phase in ("early", "core"):
                self._set(phase, "\n".join(
                    f"{item['display']}"
                    + (f"   ↑{item['boost']}" if item.get("boost", 0) >= 3 else "")
                    + f"   {item['share']}%  ~{item['median_time'] // 60}мин"
                    for item in state.get(phase, [])[:4]
                ))

            self._set("situational", ", ".join(
                f"{item['display']} (+{item['score']})"
                for item in state.get("situational", [])[:4]
            ) or "-")

            self._set("skills", " > ".join(
                entry.get("display") or entry["ability"]
                for entry in state.get("skills", [])[:8]
            ))

            self._set("talents", "\n".join(
                f"{entry['level']}: {entry.get('display') or entry['talent']}"
                for entry in state.get("talents", [])
            ))

        self.root.after(REFRESH_MS, self.refresh)

    def _set(self, key, text):
        self.sections[key][1].config(text=text or "-")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Overlay().run()
