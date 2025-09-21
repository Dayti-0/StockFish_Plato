"""Utility helpers for the Stockfish trainer application."""

def clamp(v, vmin, vmax):
    return max(vmin, min(v, vmax))
