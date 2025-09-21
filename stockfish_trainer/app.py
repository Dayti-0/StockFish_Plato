"""Application entry point for the Stockfish trainer GUI."""

from __future__ import annotations

import tkinter as tk

from .gui import ChessGUI


def main() -> None:
    """Launch the Tkinter GUI application."""
    root = tk.Tk()
    app = ChessGUI(root)
    try:
        root.iconbitmap(default="chess.ico")
    except Exception:
        pass
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (1320 // 2)
    y = (root.winfo_screenheight() // 2) - (860 // 2)
    root.geometry(f"1320x860+{x}+{y}")
    root.mainloop()


__all__ = ["main"]
