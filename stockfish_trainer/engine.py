"""Stockfish engine helpers used by the GUI application."""

from __future__ import annotations

import os
import platform
import tkinter as tk
from tkinter import filedialog
from typing import Iterable, List, Optional

from stockfish import Stockfish


def guess_stockfish_paths() -> List[str]:
    """Return a list of plausible locations for the Stockfish executable."""
    candidates: List[str] = []
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        candidates.append(env)

    if platform.system() == "Windows":
        candidates.append(
            r"C:\\Users\\Nix\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\stockfish\\stockfish-windows-x86-64-avx2.exe"
        )
        base = r"C:\\Program Files"
        base86 = r"C:\\Program Files (x86)"
        for base_dir in (base, base86):
            candidates += [
                os.path.join(base_dir, "Stockfish", "stockfish.exe"),
                os.path.join(base_dir, "stockfish", "stockfish.exe"),
                os.path.join(base_dir, "Stockfish", "bin", "stockfish.exe"),
            ]
    else:
        candidates += [
            "/usr/bin/stockfish",
            "/usr/local/bin/stockfish",
            "/opt/homebrew/bin/stockfish",
        ]
    return [path for path in candidates if path and os.path.isfile(path)]


class SafeStockfish:
    """Wrapper pour sécuriser l'initialisation et les appels au moteur."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.ready = False
        self.engine: Optional[Stockfish] = None
        self.path: Optional[str] = None
        self.init_engine(path)

    def init_engine(self, path: Optional[str] = None) -> None:
        paths: List[str] = []
        if path:
            paths.append(path)
        paths += guess_stockfish_paths()
        for candidate in paths:
            try:
                engine = Stockfish(path=candidate)
                _ = engine.get_parameters()
                self.engine = engine
                self.path = candidate
                self.ready = True
                return
            except Exception:
                continue

        try:
            root = tk._default_root or tk.Tk()
            root.withdraw()
            chosen = filedialog.askopenfilename(
                title="Sélectionner Stockfish",
                filetypes=[("Exécutable", "*.exe;*"), ("Tous", "*.*")],
            )
            root.deiconify()
            if chosen:
                engine = Stockfish(path=chosen)
                _ = engine.get_parameters()
                self.engine = engine
                self.path = chosen
                self.ready = True
                return
        except Exception:
            pass
        self.ready = False

    def set_elo_rating(self, elo: int) -> None:
        if self.ready and self.engine:
            try:
                self.engine.set_elo_rating(int(elo))
            except Exception:
                pass

    def update_engine_options(
        self,
        threads: Optional[int] = None,
        hash_mb: Optional[int] = None,
        skill: Optional[int] = None,
    ) -> None:
        if not self.ready or not self.engine:
            return
        try:
            params = self.engine.get_parameters()
            if threads is not None and "Threads" in params:
                params["Threads"] = int(threads)
            if hash_mb is not None and "Hash" in params:
                params["Hash"] = int(hash_mb)
            if skill is not None and "Skill Level" in params:
                params["Skill Level"] = int(skill)
            self.engine.update_engine_parameters(params)
        except Exception:
            pass

    def set_position(self, uci_moves: Iterable[str]) -> None:
        if self.ready and self.engine:
            try:
                self.engine.set_position(list(uci_moves))
            except Exception:
                pass

    def get_best_move(self) -> Optional[str]:
        if not self.ready or not self.engine:
            return None
        try:
            return self.engine.get_best_move()
        except Exception:
            return None

    def get_evaluation(self) -> dict:
        if not self.ready or not self.engine:
            return {"type": "cp", "value": 0}
        try:
            return self.engine.get_evaluation()
        except Exception:
            return {"type": "cp", "value": 0}
