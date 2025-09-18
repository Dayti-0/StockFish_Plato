#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entraîneur d'Échecs (Tkinter) avec rendu SVG -> PNG Haute Qualité
Dépendances:
    pip install python-chess stockfish pillow cairosvg

Fonctions clés:
- Rendu net des pièces via chess.svg + CairoSVG (icônes officielles)
- Coordonnées a-h / 1-8 alignées autour du plateau
- Sélection intelligente (cliquer une autre pièce remplace la sélection courante)
- Indice visuel (flèche animée) + bouton pour effacer
- Mode manuel (toi vs Stockfish)
- Mode AUTO (Stockfish vs Stockfish) avec ELO séparés pour Blancs et Noirs (+ raccourcis clavier)
- Undo/Redo, horloge, export/copie/import PGN, stats, thème clair/sombre, flip de l’échiquier
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import chess
import chess.pgn
import chess.svg
from stockfish import Stockfish
import threading
import json
import os
from datetime import datetime
import queue
import platform
from io import BytesIO

from PIL import Image, ImageTk
import cairosvg


# --------------------------
# Utilitaires
# --------------------------

def clamp(v, vmin, vmax):
    return max(vmin, min(v, vmax))

def guess_stockfish_paths():
    candidates = []
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        candidates.append(env)

    if platform.system() == "Windows":
        # ton chemin connu en premier
        candidates.append(r"C:\Users\Nix\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\stockfish\stockfish-windows-x86-64-avx2.exe")
        # quelques emplacements classiques
        base = r"C:\Program Files"
        base86 = r"C:\Program Files (x86)"
        for b in (base, base86):
            candidates += [
                os.path.join(b, "Stockfish", "stockfish.exe"),
                os.path.join(b, "stockfish", "stockfish.exe"),
                os.path.join(b, "Stockfish", "bin", "stockfish.exe"),
            ]
    else:
        candidates += [
            "/usr/bin/stockfish",
            "/usr/local/bin/stockfish",
            "/opt/homebrew/bin/stockfish",
        ]
    return [p for p in candidates if p and os.path.isfile(p)]


class SafeStockfish:
    """Wrapper pour sécuriser l'initialisation et les appels au moteur."""
    def __init__(self, path=None):
        self.ready = False
        self.engine = None
        self.path = None
        self.init_engine(path)

    def init_engine(self, path=None):
        paths = []
        if path: paths.append(path)
        paths += guess_stockfish_paths()
        for p in paths:
            try:
                st = Stockfish(path=p)
                _ = st.get_parameters()
                self.engine = st
                self.path = p
                self.ready = True
                return
            except Exception:
                continue
        # Sélection manuelle (si UI déjà ouverte)
        try:
            root = tk._default_root or tk.Tk()
            root.withdraw()
            p = filedialog.askopenfilename(
                title="Sélectionner Stockfish",
                filetypes=[("Exécutable", "*.exe;*"), ("Tous", "*.*")]
            )
            root.deiconify()
            if p:
                st = Stockfish(path=p)
                _ = st.get_parameters()
                self.engine = st
                self.path = p
                self.ready = True
                return
        except Exception:
            pass
        self.ready = False

    def set_elo_rating(self, elo):
        if self.ready:
            try: self.engine.set_elo_rating(int(elo))
            except: pass

    def update_engine_options(self, threads=None, hash_mb=None, skill=None):
        if not self.ready: return
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

    def set_position(self, uci_moves):
        if self.ready:
            try: self.engine.set_position(uci_moves)
            except: pass

    def get_best_move(self):
        if not self.ready: return None
        try:
            return self.engine.get_best_move()
        except Exception:
            return None

    def get_evaluation(self):
        if not self.ready: return {"type": "cp", "value": 0}
        try:
            return self.engine.get_evaluation()
        except Exception:
            return {"type": "cp", "value": 0}


# --------------------------
# Application
# --------------------------

class ChessGUI:
    BOARD_SIZE = 640
    PADDING = 40
    CANVAS_SIZE = BOARD_SIZE + 2 * PADDING

    def __init__(self, master):
        self.master = master
        self.master.title("🏆 Entraîneur d'Échecs (SVG HD)")
        self.default_geometry = "1320x860"
        self.board_only_geometry = f"{self.CANVAS_SIZE + 180}x860"
        self.master.geometry(self.default_geometry)
        self.theme_dark = True
        self.colors = self.get_colors(self.theme_dark)
        self.master.configure(bg=self.colors['bg'])

        # Moteur
        default_path = r"C:\Users\Nix\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\stockfish\stockfish-windows-x86-64-avx2.exe"
        self.stockfish = SafeStockfish(path=default_path)
        self.stockfish_ready = self.stockfish.ready

        # État de jeu
        self.board = chess.Board()
        self.player_color = chess.WHITE
        self.game_active = False
        self.selected_square = None
        self.legal_targets_cache = set()
        self.last_move = None
        self.flip_board = False
        self.difficulty = 1500
        self.move_stack_for_redo = []

        # Stats
        self.stats_path = "chess_gui_stats.json"
        self.stats = {"games":0,"wins":0,"losses":0,"draws":0,"best_streak":0,"current_streak":0,"last_result":"","last_game_time":""}
        self.load_stats()

        # Horloge
        self.time_white = 5 * 60
        self.time_black = 5 * 60
        self.increment = 0
        self.clock_running = False
        self.clock_job = None

        # Analyse / options moteur
        self.analysis_depth = 16
        self.engine_threads = 2
        self.engine_hash_mb = 128
        self.engine_skill = 20

        # Async (thread → UI)
        self.engine_queue = queue.Queue()

        # Indice visuel
        self.hint_move = None

        # Auto (Stockfish vs Stockfish)
        self.auto_mode = False
        self.auto_white_elo = 1500
        self.auto_black_elo = 1500
        self.auto_job = None

        # UI
        self.setup_ui()
        self.update_board_display()
        self.update_options_labels()
        self.update_clock_labels()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.master.after(100, self.process_engine_queue)

    # ---------- Thèmes ----------
    def get_colors(self, dark=True):
        if dark:
            return {
                'bg': '#1f1f1f',
                'panel': '#2c2c2c',
                'fg': '#ffffff',
                'light': '#f0d9b5',
                'darksquare': '#b58863',
                'highlight': '#fffb91',
                'lastmove': '#cbe86b',
                'legal': '#9ae6b4',
                'accent': '#FF9800',
                'coord': '#dddddd',
                'arrow': '#00e5ff'
            }
        else:
            return {
                'bg': '#f5f5f5',
                'panel': '#ffffff',
                'fg': '#222222',
                'light': '#f0d9b5',
                'darksquare': '#b58863',
                'highlight': '#fff176',
                'lastmove': '#aed581',
                'legal': '#a5d6a7',
                'accent': '#FB8C00',
                'coord': '#222222',
                'arrow': '#0288d1'
            }

    # ---------- UI ----------
    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        main = tk.Frame(self.master, bg=self.colors['bg'])
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Gauche
        left = tk.Frame(main, bg=self.colors['bg'])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        title = tk.Label(left, text="♟️ ENTRAÎNEUR D'ÉCHECS (rendu HD)",
                         font=("Arial", 20, "bold"), fg=self.colors['fg'], bg=self.colors['bg'])
        title.pack(pady=(0, 8))

        status_color = '#00ff00' if self.stockfish_ready else '#ff0000'
        status_text = ("✅ Stockfish connecté"
                       if self.stockfish_ready else "❌ Erreur Stockfish (Options > Choisir Stockfish)")
        self.status_label = tk.Label(left, text=status_text, font=("Arial", 10),
                                     fg=status_color, bg=self.colors['bg'])
        self.status_label.pack()

        # Canvas
        self.canvas = tk.Canvas(left, width=self.CANVAS_SIZE, height=self.CANVAS_SIZE,
                                bg=self.colors['bg'], highlightthickness=0, cursor="hand2")
        self.canvas.pack(pady=10)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-3>", self.on_right_click)  # clic droit: désélection

        # Infos
        self.game_info = tk.Label(left, text="Nouvelle partie", font=("Arial", 12),
                                  fg=self.colors['fg'], bg=self.colors['bg'])
        self.game_info.pack(pady=6)

        # Évaluation (texte)
        self.eval_label = tk.Label(left, text="= 0.00", fg=self.colors['fg'], bg=self.colors['bg'],
                                   font=("Arial", 12, "bold"))
        self.eval_label.pack(pady=(0, 6))

        # Historique
        hist_frame = tk.LabelFrame(left, text="📜 Historique", fg=self.colors['fg'],
                                   bg=self.colors['panel'], font=("Arial", 11, "bold"))
        hist_frame.pack(fill=tk.BOTH, expand=False, pady=8)
        self.moves_text = scrolledtext.ScrolledText(hist_frame, height=6,
                                                    bg=self.colors['bg'], fg=self.colors['fg'],
                                                    font=("Consolas", 10), state=tk.DISABLED)
        self.moves_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Droite
        self.right_panel = tk.Frame(main, bg=self.colors['panel'], width=430)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        self.right_panel.pack_propagate(False)

        # Contrôles de Jeu (manuel)
        controls = tk.LabelFrame(self.right_panel, text="🎮 Contrôles de Jeu",
                                 fg=self.colors['fg'], bg=self.colors['panel'], font=("Arial", 12, "bold"))
        controls.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(controls, text="Votre couleur:", fg=self.colors['fg'], bg=self.colors['panel']).pack(anchor='w', padx=5)
        row = tk.Frame(controls, bg=self.colors['panel']); row.pack(fill=tk.X, padx=5, pady=5)
        self.color_var = tk.StringVar(value="white")
        tk.Radiobutton(row, text="Blancs", variable=self.color_var, value="white",
                       fg=self.colors['fg'], bg=self.colors['panel'], selectcolor=self.colors['panel']).pack(side=tk.LEFT)
        tk.Radiobutton(row, text="Noirs", variable=self.color_var, value="black",
                       fg=self.colors['fg'], bg=self.colors['panel'], selectcolor=self.colors['panel']).pack(side=tk.LEFT)

        tk.Label(controls, text="Difficulté (ELO):", fg=self.colors['fg'], bg=self.colors['panel']).pack(anchor='w', padx=5, pady=(10,0))
        self.difficulty_var = tk.IntVar(value=1500)
        tk.Scale(controls, from_=800, to=3000, orient=tk.HORIZONTAL,
                 variable=self.difficulty_var, bg=self.colors['panel'], fg=self.colors['fg'],
                 highlightbackground=self.colors['panel']).pack(fill=tk.X, padx=5, pady=5)

        btns = tk.Frame(controls, bg=self.colors['panel']); btns.pack(fill=tk.X, padx=5, pady=8)
        self.start_btn = tk.Button(btns, text="🎯 Nouvelle Partie (Manuelle)", command=self.start_new_game,
                                   bg='#4CAF50', fg='white', font=("Arial", 10, "bold"))
        self.start_btn.pack(fill=tk.X, pady=2)
        self.hint_btn = tk.Button(btns, text="💡 Indice (visuel)", command=self.show_hint,
                                  bg='#2196F3', fg='white', font=("Arial", 10, "bold"))
        self.hint_btn.pack(fill=tk.X, pady=2)
        self.clear_hint_btn = tk.Button(btns, text="🧽 Effacer l’indice", command=self.clear_hint,
                                        bg='#607D8B', fg='white', font=("Arial", 10, "bold"))
        self.clear_hint_btn.pack(fill=tk.X, pady=2)
        self.resign_btn = tk.Button(btns, text="🏳️ Abandonner", command=self.resign_game,
                                    bg='#f44336', fg='white', font=("Arial", 10, "bold"))
        self.resign_btn.pack(fill=tk.X, pady=2)
        ur = tk.Frame(controls, bg=self.colors['panel']); ur.pack(fill=tk.X, padx=5, pady=(0,8))
        tk.Button(ur, text="↩️ Annuler (Undo)", command=self.undo_move).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        tk.Button(ur, text="↪️ Refaire (Redo)", command=self.redo_move).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        # Horloge
        clock = tk.LabelFrame(self.right_panel, text="⏱️ Horloge", fg=self.colors['fg'],
                              bg=self.colors['panel'], font=("Arial", 12, "bold"))
        clock.pack(fill=tk.X, padx=10, pady=10)
        self.clock_white_lbl = tk.Label(clock, text="Blancs: 05:00", fg=self.colors['fg'], bg=self.colors['panel'],
                                        font=("Consolas", 12, "bold")); self.clock_white_lbl.pack(fill=tk.X, padx=6, pady=2)
        self.clock_black_lbl = tk.Label(clock, text="Noirs:  05:00", fg=self.colors['fg'], bg=self.colors['panel'],
                                        font=("Consolas", 12, "bold")); self.clock_black_lbl.pack(fill=tk.X, padx=6, pady=2)
        r = tk.Frame(clock, bg=self.colors['panel']); r.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(r, text="Minutes:", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT)
        self.time_minutes_var = tk.IntVar(value=5)
        tk.Spinbox(r, from_=1, to=180, textvariable=self.time_minutes_var, width=5).pack(side=tk.LEFT, padx=6)
        tk.Label(r, text="Incr (s):", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT, padx=(12,0))
        self.increment_var = tk.IntVar(value=0)
        tk.Spinbox(r, from_=0, to=60, textvariable=self.increment_var, width=5).pack(side=tk.LEFT, padx=6)

        # Statistiques
        stats = tk.LabelFrame(self.right_panel, text="📊 Statistiques",
                              fg=self.colors['fg'], bg=self.colors['panel'], font=("Arial", 12, "bold"))
        stats.pack(fill=tk.X, padx=10, pady=10)
        self.stats_text = tk.Text(stats, height=7, bg=self.colors['bg'], fg=self.colors['fg'],
                                  font=("Arial", 10), state=tk.DISABLED)
        self.stats_text.pack(fill=tk.X, padx=5, pady=5)

        # Analyse
        an = tk.LabelFrame(self.right_panel, text="🔍 Analyse",
                           fg=self.colors['fg'], bg=self.colors['panel'], font=("Arial", 12, "bold"))
        an.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.analysis_text = scrolledtext.ScrolledText(an, height=10, bg=self.colors['bg'], fg=self.colors['fg'],
                                                       font=("Consolas", 9), state=tk.DISABLED)
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        ab = tk.Frame(an, bg=self.colors['panel']); ab.pack(fill=tk.X, padx=5, pady=5)
        tk.Button(ab, text="📈 Analyser Position", command=self.analyze_position,
                  bg=self.colors['accent'], fg='white', font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=2)
        tk.Button(ab, text="🧹 Effacer", command=self.clear_analysis,
                  bg='#607D8B', fg='white', font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=2)

        # ⚙️ Options moteur
        opt = tk.LabelFrame(self.right_panel, text="⚙️ Options moteur",
                            fg=self.colors['fg'], bg=self.colors['panel'], font=("Arial", 12, "bold"))
        opt.pack(fill=tk.X, padx=10, pady=10)
        r1 = tk.Frame(opt, bg=self.colors['panel']); r1.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(r1, text="Threads", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT)
        self.threads_var = tk.IntVar(value=2)
        tk.Spinbox(r1, from_=1, to=16, textvariable=self.threads_var, width=5, command=self.apply_engine_options).pack(side=tk.LEFT, padx=6)
        tk.Label(r1, text="Hash(MB)", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT, padx=(12,0))
        self.hash_var = tk.IntVar(value=128)
        tk.Spinbox(r1, from_=16, to=4096, textvariable=self.hash_var, width=6, command=self.apply_engine_options).pack(side=tk.LEFT, padx=6)

        r2 = tk.Frame(opt, bg=self.colors['panel']); r2.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(r2, text="Skill(0-20)", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT)
        self.skill_var = tk.IntVar(value=20)
        tk.Spinbox(r2, from_=0, to=20, textvariable=self.skill_var, width=5, command=self.apply_engine_options).pack(side=tk.LEFT, padx=6)
        tk.Button(r2, text="🎨 Thème clair/sombre", command=self.toggle_theme).pack(side=tk.RIGHT)

        r3 = tk.Frame(opt, bg=self.colors['panel']); r3.pack(fill=tk.X, padx=6, pady=2)
        self.flip_var = tk.BooleanVar(value=self.flip_board)
        tk.Checkbutton(r3, text="Inverser l'échiquier", var=self.flip_var, command=self.toggle_flip,
                       fg=self.colors['fg'], bg=self.colors['panel'], selectcolor=self.colors['panel']).pack(side=tk.LEFT)
        tk.Button(r3, text="📁 Choisir Stockfish", command=self.choose_stockfish).pack(side=tk.RIGHT)

        r4 = tk.Frame(opt, bg=self.colors['panel']); r4.pack(fill=tk.X, padx=6, pady=2)
        self.board_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r4, text="Afficher seulement le plateau", variable=self.board_only_var,
                       command=self.toggle_board_only, fg=self.colors['fg'], bg=self.colors['panel'],
                       selectcolor=self.colors['panel']).pack(side=tk.LEFT)

        # 🤖 Mode Auto (SFSF) — panneau et boutons
        auto = tk.LabelFrame(self.right_panel, text="🤖 Mode Auto: Stockfish vs Stockfish",
                             fg=self.colors['fg'], bg=self.colors['panel'], font=("Arial", 12, "bold"))
        auto.pack(fill=tk.X, padx=10, pady=10)

        rowa = tk.Frame(auto, bg=self.colors['panel']); rowa.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(rowa, text="ELO Blancs:", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT)
        self.auto_white_elo_var = tk.IntVar(value=self.auto_white_elo)
        tk.Spinbox(rowa, from_=800, to=3000, textvariable=self.auto_white_elo_var, width=6).pack(side=tk.LEFT, padx=6)

        tk.Label(rowa, text="ELO Noirs:", fg=self.colors['fg'], bg=self.colors['panel']).pack(side=tk.LEFT, padx=(12,0))
        self.auto_black_elo_var = tk.IntVar(value=self.auto_black_elo)
        tk.Spinbox(rowa, from_=800, to=3000, textvariable=self.auto_black_elo_var, width=6).pack(side=tk.LEFT, padx=6)

        rowb = tk.Frame(auto, bg=self.colors['panel']); rowb.pack(fill=tk.X, padx=6, pady=2)
        tk.Button(rowb, text="🚀 Lancer Partie Auto (SFSF)", command=self.start_auto_game,
                  bg='#8E24AA', fg='white', font=("Arial", 10, "bold")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=3)
        tk.Button(rowb, text="⏹️ Arrêter Auto", command=self.stop_auto_game,
                  bg='#B71C1C', fg='white', font=("Arial", 10, "bold")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=3)

        # Raccourcis clavier utiles
        self.master.bind("<Key-h>", lambda e: self.show_hint())
        self.master.bind("<Key-H>", lambda e: self.show_hint())
        self.master.bind("<Key-c>", lambda e: self.clear_hint())
        self.master.bind("<Key-C>", lambda e: self.clear_hint())
        self.master.bind("<Key-n>", lambda e: self.start_new_game())
        self.master.bind("<Key-N>", lambda e: self.start_new_game())
        self.master.bind("<Key-a>", lambda e: self.start_auto_game())
        self.master.bind("<Key-A>", lambda e: self.start_auto_game())
        self.master.bind("<Key-s>", lambda e: self.stop_auto_game())
        self.master.bind("<Key-S>", lambda e: self.stop_auto_game())
        self.master.bind("<Key-f>", lambda e: (self.flip_var.set(not self.flip_var.get()), self.toggle_flip()))
        self.master.bind("<Key-F>", lambda e: (self.flip_var.set(not self.flip_var.get()), self.toggle_flip()))

    # ---------- Coordonnées & mapping ----------
    def square_to_coords(self, square):
        file = chess.square_file(square)  # 0..7
        rank = chess.square_rank(square)  # 0..7
        if self.flip_board:
            file = 7 - file
            rank = 7 - rank
        sq_size = self.BOARD_SIZE // 8
        x = self.PADDING + file * sq_size
        y = self.PADDING + (7 - rank) * sq_size
        return x, y

    def square_center(self, square):
        x, y = self.square_to_coords(square)
        sq = self.BOARD_SIZE // 8
        return x + sq/2, y + sq/2

    def coords_to_square(self, x, y):
        if not (self.PADDING <= x < self.PADDING + self.BOARD_SIZE and
                self.PADDING <= y < self.PADDING + self.BOARD_SIZE):
            return None
        sq_size = self.BOARD_SIZE // 8
        file = (x - self.PADDING) // sq_size
        rank_from_top = (y - self.PADDING) // sq_size
        rank = 7 - rank_from_top
        if self.flip_board:
            file = 7 - file
            rank = 7 - rank
        return chess.square(int(file), int(rank))

    # ---------- Rendu plateau ----------
    def render_board_image(self):
        svg = chess.svg.board(
            board=self.board,
            lastmove=self.last_move,
            size=self.BOARD_SIZE,
            coordinates=False,
            squares=None
        )
        png_bytes = cairosvg.svg2png(bytestring=svg.encode('utf-8'),
                                     output_width=self.BOARD_SIZE, output_height=self.BOARD_SIZE)
        return Image.open(BytesIO(png_bytes))

    def draw_coordinates(self):
        self.canvas.delete("coord")
        sq = self.BOARD_SIZE // 8
        # Files
        for i in range(8):
            file_idx = i if not self.flip_board else 7 - i
            letter = chr(ord('a') + file_idx)
            cx = self.PADDING + i * sq + sq/2
            cy = self.PADDING + self.BOARD_SIZE + 16
            self.canvas.create_text(cx, cy, text=letter, fill=self.colors['coord'],
                                    font=("Consolas", 12, "bold"), tags="coord")
        # Rangs
        for i in range(8):
            rank_idx = 8 - i if not self.flip_board else i + 1
            cx = self.PADDING - 16
            cy = self.PADDING + i * sq + sq/2
            self.canvas.create_text(cx, cy, text=str(rank_idx), fill=self.colors['coord'],
                                    font=("Consolas", 12, "bold"), tags="coord")

    def draw_highlights(self):
        self.canvas.delete("hl")
        sq = self.BOARD_SIZE // 8

        if self.last_move:
            for sqr in (self.last_move.from_square, self.last_move.to_square):
                x, y = self.square_to_coords(sqr)
                self.canvas.create_rectangle(x, y, x+sq, y+sq, outline=self.colors['lastmove'],
                                             width=3, tags="hl")

        if self.selected_square is not None and not self.auto_mode:
            x, y = self.square_to_coords(self.selected_square)
            self.canvas.create_rectangle(x, y, x+sq, y+sq, outline=self.colors['highlight'],
                                         width=3, tags="hl")

        for t in self.legal_targets_cache:
            x, y = self.square_to_coords(t)
            r = sq * 0.18
            cx = x + sq/2
            cy = y + sq/2
            self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=self.colors['legal'],
                                    width=0, stipple="gray25", tags="hl")

        # Indice visuel (statique)
        self.canvas.delete("hint")
        if self.hint_move:
            fx, fy = self.square_center(self.hint_move.from_square)
            tx, ty = self.square_center(self.hint_move.to_square)
            x1, y1 = self.square_to_coords(self.hint_move.from_square)
            x2, y2 = self.square_to_coords(self.hint_move.to_square)
            self.canvas.create_rectangle(x1, y1, x1+sq, y1+sq, outline=self.colors['arrow'], width=3, tags="hint")
            self.canvas.create_rectangle(x2, y2, x2+sq, y2+sq, outline=self.colors['arrow'], width=3, tags="hint")
            self.canvas.create_line(fx, fy, tx, ty, fill=self.colors['arrow'], width=5,
                                    arrow=tk.LAST, arrowshape=(14,18,6), tags="hint")

    def update_board_display(self):
        img = self.render_board_image()
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("board")
        self.canvas.create_rectangle(0, 0, self.CANVAS_SIZE, self.CANVAS_SIZE, fill=self.colors['bg'], width=0, tags="board")
        self.canvas.create_image(self.PADDING, self.PADDING, anchor="nw", image=self.tk_img, tags="board")
        self.draw_coordinates()
        self.draw_highlights()
        self.update_game_info()
        self.update_move_history()
        self.update_eval_label()

    # ---------- Animation d’indice ----------
    def draw_hint_arrow_segment(self, from_xy, to_xy, progress):
        self.canvas.delete("hint_anim")
        fx, fy = from_xy
        tx, ty = to_xy
        ix = fx + (tx - fx) * progress
        iy = fy + (ty - fy) * progress
        self.canvas.create_line(fx, fy, ix, iy, fill=self.colors['arrow'], width=5, tags="hint_anim")
        if progress > 0.8:
            self.canvas.create_line(fx, fy, ix, iy, fill=self.colors['arrow'],
                                    width=5, arrow=tk.LAST, arrowshape=(14,18,6), tags="hint_anim")

    def animate_hint_arrow(self, mv, steps=14, delay_ms=18):
        self.canvas.delete("hint")
        sq = self.BOARD_SIZE // 8
        x1, y1 = self.square_to_coords(mv.from_square)
        x2, y2 = self.square_to_coords(mv.to_square)
        self.canvas.create_rectangle(x1, y1, x1+sq, y1+sq, outline=self.colors['arrow'], width=3, tags="hint")
        self.canvas.create_rectangle(x2, y2, x2+sq, y2+sq, outline=self.colors['arrow'], width=3, tags="hint")

        fxy = self.square_center(mv.from_square)
        txy = self.square_center(mv.to_square)

        def step(i=0):
            if i > steps:
                self.canvas.delete("hint_anim")
                self.hint_move = mv
                self.update_board_display()
                return
            self.draw_hint_arrow_segment(fxy, txy, i/steps)
            self.master.after(delay_ms, lambda: step(i+1))
        step()

    # ---------- Clics ----------
    def on_canvas_click(self, event):
        if not self.game_active or self.auto_mode:
            return
        if self.board.turn != self.player_color:
            return

        square = self.coords_to_square(event.x, event.y)
        if square is None:
            return

        piece = self.board.piece_at(square)

        if self.selected_square is not None:
            if square == self.selected_square:
                self.selected_square = None
                self.legal_targets_cache = set()
                self.update_board_display()
                return
            if piece and piece.color == self.player_color:
                self.selected_square = square
                self.legal_targets_cache = {mv.to_square for mv in self.board.legal_moves if mv.from_square == square}
                self.update_board_display()
                return
            try:
                move = chess.Move(self.selected_square, square)
                if (self.board.piece_at(self.selected_square) and
                    self.board.piece_at(self.selected_square).piece_type == chess.PAWN and
                    (chess.square_rank(square) == 7 or chess.square_rank(square) == 0)):
                    move = chess.Move(self.selected_square, square, promotion=chess.QUEEN)
                if move in self.board.legal_moves:
                    self.make_move(move)
                else:
                    messagebox.showwarning("Coup illégal", "Ce coup n'est pas autorisé!")
            except Exception:
                pass
            self.selected_square = None
            self.legal_targets_cache = set()
            self.hint_move = None
            self.update_board_display()
            return

        if piece and piece.color == self.player_color:
            self.selected_square = square
            self.legal_targets_cache = {mv.to_square for mv in self.board.legal_moves if mv.from_square == square}
            self.update_board_display()

    def on_right_click(self, event):
        if not self.game_active or self.auto_mode:
            return
        self.selected_square = None
        self.legal_targets_cache = set()
        self.update_board_display()

    # ---------- Jeu (manuel) ----------
    def make_move(self, move):
        if self.board.turn == chess.WHITE and self.increment > 0:
            self.time_white += self.increment
        elif self.board.turn == chess.BLACK and self.increment > 0:
            self.time_black += self.increment

        self.board.push(move)
        self.last_move = move
        self.move_stack_for_redo.clear()
        self.hint_move = None
        self.update_board_display()

        if self.board.is_game_over():
            self.end_game()
            return

        if self.board.turn != self.player_color:
            self.disable_inputs(True)
            self.master.after(120, self.stockfish_move_once_for_manual)
        else:
            self.disable_inputs(False)
        self.update_clock_labels()

    def stockfish_move_once_for_manual(self):
        if not self.stockfish_ready or not self.game_active or self.auto_mode:
            self.disable_inputs(False)
            return

        def engine_task():
            try:
                self.stockfish.set_elo_rating(self.difficulty_var.get())
                self.stockfish.set_position([m.uci() for m in self.board.move_stack])
                move_uci = self.stockfish.get_best_move()
                if move_uci:
                    self.engine_queue.put(("engine_move_manual", chess.Move.from_uci(move_uci)))
            except Exception as e:
                self.engine_queue.put(("engine_error", str(e)))
        threading.Thread(target=engine_task, daemon=True).start()

    # ---------- File d’événements ----------
    def process_engine_queue(self):
        try:
            while True:
                kind, payload = self.engine_queue.get_nowait()
                if kind == "engine_move_manual":
                    mv = payload
                    if self.board.turn == chess.WHITE and self.increment > 0:
                        self.time_white += self.increment
                    elif self.board.turn == chess.BLACK and self.increment > 0:
                        self.time_black += self.increment
                    if mv in self.board.legal_moves:
                        self.board.push(mv)
                        self.last_move = mv
                        self.update_board_display()
                        self.disable_inputs(False)
                        if self.board.is_game_over():
                            self.end_game()
                    else:
                        self.disable_inputs(False)

                elif kind == "engine_move_auto":
                    mv = payload
                    if self.board.turn == chess.WHITE and self.increment > 0:
                        self.time_white += self.increment
                    elif self.board.turn == chess.BLACK and self.increment > 0:
                        self.time_black += self.increment
                    if mv in self.board.legal_moves:
                        self.board.push(mv)
                        self.last_move = mv
                        self.update_board_display()
                        if self.board.is_game_over() or not self.auto_mode:
                            self.stop_auto_game()
                            self.end_game()
                        else:
                            self.schedule_next_auto_move()
                    else:
                        self.stop_auto_game()

                elif kind == "engine_error":
                    self.add_analysis("❌ Erreur moteur: " + str(payload))
                    self.disable_inputs(False)
                    self.stop_auto_game()
        except queue.Empty:
            pass
        finally:
            self.master.after(100, self.process_engine_queue)

    # ---------- Indice visuel ----------
    def show_hint(self):
        if not self.stockfish_ready or not self.game_active or self.auto_mode:
            return
        def get_hint():
            try:
                self.stockfish.set_elo_rating(self.difficulty_var.get())
                self.stockfish.set_position([m.uci() for m in self.board.move_stack])
                best_move = self.stockfish.get_best_move()
                if best_move:
                    mv = chess.Move.from_uci(best_move)
                    self.master.after(0, lambda: self.animate_hint_arrow(mv))
                    self.master.after(0, lambda: self.add_analysis(f"💡 Meilleur coup (visuel): {best_move}"))
            except Exception as e:
                self.master.after(0, lambda: self.add_analysis(f"❌ Indice indisponible: {e}"))
        threading.Thread(target=get_hint, daemon=True).start()

    def clear_hint(self):
        self.hint_move = None
        self.canvas.delete("hint_anim")
        self.update_board_display()

    # ---------- Analyse ----------
    def analyze_position(self):
        if not self.stockfish_ready:
            self.add_analysis("Moteur indisponible.")
            return
        def analyze():
            try:
                self.stockfish.set_position([m.uci() for m in self.board.move_stack])
                evaluation = self.stockfish.get_evaluation()
                best_move = self.stockfish.get_best_move()
                analysis = "📊 ANALYSE DE POSITION:\n"
                if evaluation['type'] == 'cp':
                    score = evaluation['value'] / 100
                    analysis += f"• Évaluation: {score:+.2f} points\n"
                    if abs(score) < 0.5: analysis += "• Position équilibrée\n"
                    elif score > 0: analysis += "• Avantage aux blancs\n"
                    else: analysis += "• Avantage aux noirs\n"
                else:
                    mate_in = evaluation['value']
                    analysis += f"• Mat en {abs(mate_in)} coups pour {'blancs' if mate_in>0 else 'noirs'}\n"
                if best_move:
                    analysis += f"• Meilleur coup: {best_move}\n"
                analysis += f"• Coups joués: {len(self.board.move_stack)}\n" + "─"*30
                self.master.after(0, lambda: self.add_analysis(analysis))
                self.master.after(0, self.update_eval_label)
            except Exception as e:
                self.master.after(0, lambda: self.add_analysis(f"❌ Analyse impossible: {e}"))
        threading.Thread(target=analyze, daemon=True).start()

    def get_eval_cp(self):
        if not self.stockfish_ready:
            return None
        try:
            self.stockfish.set_position([m.uci() for m in self.board.move_stack])
            evaluation = self.stockfish.get_evaluation()
            if evaluation['type'] == 'cp':
                return int(evaluation['value'])
            elif evaluation['type'] == 'mate':
                return 100000 if evaluation['value'] > 0 else -100000
        except Exception:
            return None
        return None

    def update_eval_label(self):
        cp = self.get_eval_cp()
        cp = 0 if cp is None else cp
        score = cp / 100.0
        self.eval_label.config(text=("= 0.00" if abs(score) < 0.05 else f"{'White' if score>=0 else 'Black'} {score:+.2f}"))

    # ---------- Historique / Infos ----------
    def update_move_history(self):
        self.moves_text.config(state=tk.NORMAL)
        self.moves_text.delete(1.0, tk.END)
        game = chess.pgn.Game()
        node = game
        for mv in self.board.move_stack:
            node = node.add_variation(mv)
        self.moves_text.insert(tk.END, str(game.mainline()))
        self.moves_text.config(state=tk.DISABLED)

    def update_game_info(self):
        if self.game_active:
            if self.auto_mode:
                info = "Mode Auto: Stockfish vs Stockfish"
            else:
                turn = "Blancs" if self.board.turn == chess.WHITE else "Noirs"
                your_turn = "Votre tour" if self.board.turn == self.player_color else "Tour de Stockfish"
                info = f"Tour des {turn} - {your_turn}"
            if self.board.is_check():
                info += " - ÉCHEC!"
        else:
            info = "Partie terminée"
        self.game_info.config(text=info)

    def add_analysis(self, text):
        self.analysis_text.config(state=tk.NORMAL)
        self.analysis_text.insert(tk.END, text + "\n")
        self.analysis_text.see(tk.END)
        self.analysis_text.config(state=tk.DISABLED)

    def clear_analysis(self):
        self.analysis_text.config(state=tk.NORMAL)
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.config(state=tk.DISABLED)

    # ---------- Undo/Redo ----------
    def undo_move(self):
        if not self.board.move_stack or self.auto_mode:
            return
        mv = self.board.pop()
        self.move_stack_for_redo.append(mv)
        self.last_move = self.board.move_stack[-1] if self.board.move_stack else None
        self.hint_move = None
        self.update_board_display()

    def redo_move(self):
        if not self.move_stack_for_redo or self.auto_mode:
            return
        mv = self.move_stack_for_redo.pop()
        if mv in self.board.legal_moves:
            self.board.push(mv)
            self.last_move = mv
            self.hint_move = None
            self.update_board_display()

    # ---------- PGN ----------
    def export_pgn(self):
        game = chess.pgn.Game()
        game.headers["Event"] = "Training"
        game.headers["Site"] = "Local"
        game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
        game.headers["Round"] = "1"
        game.headers["White"] = "Auto-White" if self.auto_mode else ("You" if self.player_color == chess.WHITE else "Stockfish")
        game.headers["Black"] = "Auto-Black" if self.auto_mode else ("Stockfish" if self.player_color == chess.WHITE else "You")
        node = game
        for mv in self.board.move_stack:
            node = node.add_variation(mv)
        pgn_str = str(game)
        path = filedialog.asksaveasfilename(defaultextension=".pgn", filetypes=[("PGN", "*.pgn")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(pgn_str)
                messagebox.showinfo("Export PGN", "Partie exportée avec succès.")
            except Exception as e:
                messagebox.showerror("Export PGN", f"Erreur: {e}")

    def copy_pgn(self):
        game = chess.pgn.Game(); node = game
        for mv in self.board.move_stack:
            node = node.add_variation(mv)
        self.master.clipboard_clear()
        self.master.clipboard_append(str(game))
        messagebox.showinfo("PGN", "PGN copié dans le presse-papiers.")

    def import_pgn(self):
        if self.auto_mode:
            messagebox.showwarning("Auto", "Désactive le mode Auto avant d'importer.")
            return
        path = filedialog.askopenfilename(filetypes=[("PGN", "*.pgn"), ("Tous", "*.*")])
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                game = chess.pgn.read_game(f)
            self.board.reset()
            for mv in game.mainline_moves():
                self.board.push(mv)
            self.last_move = self.board.move_stack[-1] if self.board.move_stack else None
            self.game_active = not self.board.is_game_over()
            self.hint_move = None
            self.update_board_display()
            self.add_analysis(f"📥 PGN importé ({len(list(game.mainline_moves()))} coups).")
        except Exception as e:
            messagebox.showerror("Import PGN", f"Erreur: {e}")

    # ---------- Horloge ----------
    def start_clock(self):
        self.clock_running = True
        self.tick_clock()

    def stop_clock(self):
        self.clock_running = False
        if self.clock_job:
            try: self.master.after_cancel(self.clock_job)
            except Exception: pass
            self.clock_job = None

    def tick_clock(self):
        if not self.clock_running or not self.game_active:
            return
        if self.board.turn == chess.WHITE:
            self.time_white = max(0, self.time_white - 1)
            if self.time_white == 0:
                self.flag_time(color="white")
        else:
            self.time_black = max(0, self.time_black - 1)
            if self.time_black == 0:
                self.flag_time(color="black")
        self.update_clock_labels()
        self.clock_job = self.master.after(1000, self.tick_clock)

    def flag_time(self, color):
        self.game_active = False
        self.stop_auto_game()
        self.stop_clock()
        messagebox.showinfo("Temps écoulé", f"Temps des {'Blancs' if color=='white' else 'Noirs'} épuisé.")
        self.stats["games"] += 1
        if not self.auto_mode:
            if (color == "white" and self.player_color == chess.WHITE) or (color == "black" and self.player_color == chess.BLACK):
                self.stats["losses"] += 1
                self.stats["current_streak"] = 0
                self.stats["last_result"] = "L"
            else:
                self.stats["wins"] += 1
                self.stats["current_streak"] += 1
                self.stats["best_streak"] = max(self.stats["best_streak"], self.stats["current_streak"])
                self.stats["last_result"] = "W"
        self.stats["last_game_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save_stats()
        self.update_stats_display()

    def update_clock_labels(self):
        def fmt(t): return f"{int(t//60):02d}:{int(t%60):02d}"
        self.clock_white_lbl.config(text=f"Blancs: {fmt(self.time_white)}")
        self.clock_black_lbl.config(text=f"Noirs:  {fmt(self.time_black)}")

    # ---------- Stats ----------
    def update_stats_display(self):
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete(1.0, tk.END)
        s = self.stats
        txt = f"""Parties jouées: {s['games']}
Victoires: {s['wins']}
Défaites: {s['losses']}
Nuls: {s['draws']}"""
        if s['games'] > 0:
            win_rate = (s['wins'] / s['games']) * 100
            txt += f"\nTaux de victoire: {win_rate:.1f}%"
        txt += f"\nMeilleure série: {s['best_streak']}"
        txt += f"\nDernier résultat: {s.get('last_result','')}"
        if s.get('last_game_time'):
            txt += f"\nDernière partie: {s['last_game_time']}"
        self.stats_text.insert(1.0, txt)
        self.stats_text.config(state=tk.DISABLED)

    def load_stats(self):
        try:
            if os.path.exists(self.stats_path):
                with open(self.stats_path, "r", encoding="utf-8") as f:
                    self.stats = json.load(f)
        except Exception:
            pass

    def save_stats(self):
        try:
            with open(self.stats_path, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- Options / thème ----------
    def apply_engine_options(self):
        self.engine_threads = int(self.threads_var.get())
        self.engine_hash_mb = int(self.hash_var.get())
        self.engine_skill = int(self.skill_var.get())
        if self.stockfish_ready:
            self.stockfish.update_engine_options(self.engine_threads, self.engine_hash_mb, self.engine_skill)
        self.update_options_labels()

    def update_options_labels(self):
        eng_path = self.stockfish.path if self.stockfish_ready else "Aucun"
        # Affiche aussi un rappel dans la zone d’analyse pour visibilité
        self.add_analysis(f"⚙️ Threads:{self.engine_threads} Hash:{self.engine_hash_mb}MB Skill:{self.engine_skill} | {os.path.basename(eng_path)}")

    def on_depth_change(self):
        self.analysis_depth = int(self.depth_var.get())

    def toggle_flip(self):
        self.flip_board = self.flip_var.get()
        self.update_board_display()

    def toggle_board_only(self):
        if self.board_only_var.get():
            if self.right_panel.winfo_manager():
                self.right_panel.pack_forget()
            self.master.geometry(self.board_only_geometry)
        else:
            if not self.right_panel.winfo_manager():
                self.right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
            self.master.geometry(self.default_geometry)

    def toggle_theme(self):
        self.theme_dark = not self.theme_dark
        self.colors = self.get_colors(self.theme_dark)
        self.master.configure(bg=self.colors['bg'])
        for w in self.master.winfo_children():
            self._recolor_recursive(w)
        self.update_board_display()
        self.update_clock_labels()
        status_color = '#00ff00' if self.stockfish_ready else '#ff0000'
        self.status_label.config(fg=status_color)

    def _recolor_recursive(self, widget):
        try:
            if isinstance(widget, (tk.Frame, tk.LabelFrame)):
                widget.configure(bg=self.colors['panel'] if widget != self.master.children.get('!frame', None) else self.colors['bg'])
        except Exception:
            pass
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                try: child.configure(bg=self.colors['bg'] if child.master == self.master.children.get('!frame', None) else self.colors['panel'],
                                     fg=self.colors['fg'])
                except: pass
            elif isinstance(child, (tk.Text, scrolledtext.ScrolledText)):
                try: child.configure(bg=self.colors['bg'], fg=self.colors['fg'])
                except: pass
            elif isinstance(child, tk.Canvas):
                try: child.configure(bg=self.colors['bg'])
                except: pass
            self._recolor_recursive(child)

    def choose_stockfish(self):
        p = filedialog.askopenfilename(title="Sélectionner Stockfish",
                                       filetypes=[("Exécutable", "*.exe;*"), ("Tous", "*.*")])
        if not p: return
        try:
            sf = SafeStockfish(path=p)
            if not sf.ready:
                raise RuntimeError("Binaire invalide.")
            self.stockfish = sf
            self.stockfish_ready = True
            self.status_label.config(text="✅ Stockfish connecté", fg="#00ff00")
            self.apply_engine_options()
            self.add_analysis(f"🔗 Stockfish sélectionné: {p}")
        except Exception as e:
            messagebox.showerror("Stockfish", f"Impossible d'initialiser Stockfish:\n{e}")
            self.stockfish_ready = False
            self.status_label.config(text="❌ Erreur Stockfish", fg="#ff0000")

    def disable_inputs(self, disabled: bool):
        state = tk.DISABLED if disabled else tk.NORMAL
        for b in (self.start_btn, self.hint_btn, self.clear_hint_btn, self.resign_btn):
            try: b.config(state=state)
            except: pass

    # ---------- Démarrer/Finir (manuel) ----------
    def start_new_game(self):
        if not self.stockfish_ready:
            messagebox.showerror("Erreur", "Stockfish n'est pas disponible !")
            return
        if self.auto_mode:
            self.stop_auto_game()

        mins = clamp(self.time_minutes_var.get(), 1, 180)
        self.time_white = mins * 60
        self.time_black = mins * 60
        self.increment = clamp(self.increment_var.get(), 0, 60)

        self.board.reset()
        self.selected_square = None
        self.legal_targets_cache = set()
        self.game_active = True
        self.auto_mode = False
        self.player_color = chess.WHITE if self.color_var.get() == "white" else chess.BLACK
        self.difficulty = self.difficulty_var.get()
        self.last_move = None
        self.move_stack_for_redo.clear()
        self.hint_move = None

        self.flip_board = (self.player_color == chess.BLACK)
        self.flip_var.set(self.flip_board)

        self.update_board_display()
        self.add_analysis("🎯 Nouvelle partie (manuelle) commencée!")
        self.disable_inputs(False)
        self.start_clock()

        if self.player_color == chess.BLACK:
            self.master.after(500, self.stockfish_move_once_for_manual)

    def resign_game(self):
        if self.game_active:
            if messagebox.askyesno("Abandon", "Êtes-vous sûr de vouloir abandonner ?"):
                self.game_active = False
                self.stop_clock()
                self.stop_auto_game()
                self.stats["games"] += 1
                self.stats["losses"] += 1
                self.stats["current_streak"] = 0
                self.stats["last_result"] = "L"
                self.stats["last_game_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.save_stats()
                self.update_stats_display()
                self.add_analysis("🏳️ Vous avez abandonné la partie.")
                messagebox.showinfo("Fin de partie", "Vous avez abandonné.")

    def end_game(self):
        was_auto = self.auto_mode
        self.game_active = False
        self.stop_clock()
        self.stop_auto_game()
        result = self.board.result()
        self.stats["games"] += 1
        if not was_auto:
            if result == "1/2-1/2":
                self.stats["draws"] += 1
                msg = "🤝 Match nul!"
                self.stats["last_result"] = "D"
                self.stats["current_streak"] = 0
            elif (result == "1-0" and self.player_color == chess.WHITE) or (result == "0-1" and self.player_color == chess.BLACK):
                self.stats["wins"] += 1
                msg = "🎉 Félicitations! Vous avez gagné!"
                self.stats["last_result"] = "W"
                self.stats["current_streak"] += 1
                self.stats["best_streak"] = max(self.stats["best_streak"], self.stats["current_streak"])
            else:
                self.stats["losses"] += 1
                msg = "😔 Vous avez perdu. Continuez à vous entraîner!"
                self.stats["last_result"] = "L"
                self.stats["current_streak"] = 0
        else:
            msg = f"🤖 Partie auto terminée: {result}"

        if self.board.is_checkmate(): msg += "\n🔥 Échec et mat!"
        elif self.board.is_stalemate(): msg += "\n🚫 Pat (stalemate)"
        self.stats["last_game_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save_stats()
        self.update_stats_display()
        self.add_analysis(f"🏁 FIN DE PARTIE\n{msg}")
        messagebox.showinfo("Fin de partie", msg)

    # ---------- 🤖 Auto: Stockfish vs Stockfish ----------
    def start_auto_game(self):
        if not self.stockfish_ready:
            messagebox.showerror("Erreur", "Stockfish n'est pas disponible !")
            return

        mins = clamp(self.time_minutes_var.get(), 1, 180)
        self.time_white = mins * 60
        self.time_black = mins * 60
        self.increment = clamp(self.increment_var.get(), 0, 60)

        self.board.reset()
        self.selected_square = None
        self.legal_targets_cache = set()
        self.hint_move = None
        self.game_active = True
        self.auto_mode = True
        self.last_move = None
        self.move_stack_for_redo.clear()

        self.auto_white_elo = clamp(self.auto_white_elo_var.get(), 800, 3000)
        self.auto_black_elo = clamp(self.auto_black_elo_var.get(), 800, 3000)

        self.update_board_display()
        self.add_analysis(f"🤖 Partie Auto lancée (Blancs {self.auto_white_elo}, Noirs {self.auto_black_elo})")
        self.disable_inputs(True)
        self.start_clock()
        self.schedule_next_auto_move()

    def stop_auto_game(self):
        self.auto_mode = False
        if self.auto_job:
            try: self.master.after_cancel(self.auto_job)
            except Exception: pass
            self.auto_job = None
        self.disable_inputs(False)

    def schedule_next_auto_move(self):
        self.auto_job = self.master.after(150, self.stockfish_move_once_for_auto)

    def stockfish_move_once_for_auto(self):
        if not self.stockfish_ready or not self.game_active or not self.auto_mode:
            return
        side = self.board.turn
        elo = self.auto_white_elo if side == chess.WHITE else self.auto_black_elo

        def engine_task():
            try:
                self.stockfish.set_elo_rating(elo)
                self.stockfish.set_position([m.uci() for m in self.board.move_stack])
                move_uci = self.stockfish.get_best_move()
                if move_uci:
                    self.engine_queue.put(("engine_move_auto", chess.Move.from_uci(move_uci)))
            except Exception as e:
                self.engine_queue.put(("engine_error", str(e)))
        threading.Thread(target=engine_task, daemon=True).start()

    # ---------- Divers ----------
    def on_close(self):
        self.stop_clock()
        self.stop_auto_game()
        self.master.destroy()


def main():
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


if __name__ == "__main__":
    main()
