"""Tkinter-based graphical user interface for the Stockfish trainer."""

import json
import os
import queue
import threading
from datetime import datetime
from io import BytesIO

import chess
import chess.pgn
import chess.svg
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import cairosvg

from .engine import SafeStockfish
from .utils import MINIMAL_OPENINGS, clamp, load_training_openings

class ChessGUI:
    BOARD_SIZE = 640
    PADDING = 40
    CANVAS_SIZE = BOARD_SIZE + 2 * PADDING

    def __init__(self, master):
        self.master = master
        self.master.title("🏆 Entraîneur d'Échecs (SVG HD)")
        self.master.geometry("1320x860")
        self.theme_dark = True
        self.colors = self.get_colors(self.theme_dark)
        self.master.configure(bg=self.colors['bg'])
        self._theme_widgets = []

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

        # Entraînement ouvertures
        self.training_mode_var = tk.BooleanVar(value=False)
        self.training_opening_var = tk.StringVar(value="Libre")
        self.training_description_var = tk.StringVar(value="Mode libre sans séquence imposée.")
        self.training_status_var = tk.StringVar(value="Mode libre")
        self.training_lines = self.load_default_openings()
        if self.training_lines and self.training_opening_var.get() not in self.training_lines:
            first_opening = next(iter(self.training_lines))
            self.training_opening_var.set(first_opening)
            description = self.training_lines[first_opening].get("description", "")
            self.training_description_var.set(description)
        self.training_line = []
        self.training_index = 0
        self.training_active = False

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

    def configure_ttk_styles(self):
        """Configure ttk components so they follow the current palette."""
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            # Le thème peut déjà être appliqué sur certaines plateformes.
            pass
        style.configure("TNotebook", background=self.colors['panel'], borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=self.colors['panel'],
                        foreground=self.colors['fg'],
                        padding=(12, 6))
        style.map("TNotebook.Tab",
                  background=[("selected", self.colors['accent'])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TFrame", background=self.colors['panel'])
        style.configure("TLabel", background=self.colors['panel'], foreground=self.colors['fg'])
        self._ttk_style = style

    def register_widget(self, widget, palette='bg', include_fg=False):
        """Enregistre un widget pour la mise à jour automatique des couleurs."""
        self._theme_widgets.append((widget, palette, include_fg))

    def apply_theme_to_widgets(self):
        for widget, palette, include_fg in self._theme_widgets:
            bg_color = self.colors['bg'] if palette == 'bg' else self.colors['panel']
            try:
                widget.configure(bg=bg_color)
            except Exception:
                pass
            if include_fg:
                try:
                    widget.configure(fg=self.colors['fg'])
                except Exception:
                    pass
            if isinstance(widget, (tk.Text, scrolledtext.ScrolledText)):
                try:
                    widget.configure(bg=bg_color, fg=self.colors['fg'], insertbackground=self.colors['fg'])
                except Exception:
                    pass
            if isinstance(widget, tk.Canvas):
                try:
                    widget.configure(bg=bg_color)
                except Exception:
                    pass
            if isinstance(widget, tk.Scale):
                try:
                    widget.configure(troughcolor='#555555' if self.theme_dark else '#d0d0d0',
                                     highlightbackground=bg_color,
                                     activebackground=self.colors['accent'])
                except Exception:
                    pass
            if isinstance(widget, (tk.Radiobutton, tk.Checkbutton)):
                try:
                    widget.configure(selectcolor=bg_color, activebackground=bg_color)
                except Exception:
                    pass
            if isinstance(widget, tk.Spinbox):
                try:
                    widget.configure(readonlybackground=bg_color, highlightbackground=bg_color)
                except Exception:
                    pass
            if isinstance(widget, tk.Button):
                try:
                    widget.configure(activebackground=self.colors['accent'])
                except Exception:
                    pass

    # ---------- UI ----------

    def setup_ui(self):
        self.configure_ttk_styles()
        self.register_widget(self.master, 'bg')

        main = tk.Frame(self.master, bg=self.colors['bg'])
        self.register_widget(main, 'bg')
        main.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        self.main_frame = main

        left = tk.Frame(main, bg=self.colors['bg'])
        self.register_widget(left, 'bg')
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_frame = left

        self.header_frame = tk.Frame(left, bg=self.colors['bg'])
        self.register_widget(self.header_frame, 'bg')
        self.header_frame.pack(fill=tk.X)

        self.title_label = tk.Label(self.header_frame,
                                    text="♟️ ENTRAÎNEUR D'ÉCHECS",
                                    font=("Arial", 20, "bold"),
                                    fg=self.colors['fg'],
                                    bg=self.colors['bg'])
        self.register_widget(self.title_label, 'bg', include_fg=True)
        self.title_label.pack(anchor='w')

        status_color = '#00ff00' if self.stockfish_ready else '#ff0000'
        status_text = ("✅ Stockfish connecté"
                       if self.stockfish_ready else "❌ Erreur Stockfish (Options > Choisir Stockfish)")
        self.status_label = tk.Label(self.header_frame,
                                     text=status_text,
                                     font=("Arial", 10),
                                     fg=status_color,
                                     bg=self.colors['bg'])
        self.register_widget(self.status_label, 'bg', include_fg=True)
        self.status_label.pack(anchor='w', pady=(6, 0))

        self.board_frame = tk.Frame(left, bg=self.colors['bg'])
        self.register_widget(self.board_frame, 'bg')
        self.board_frame.pack(pady=(12, 0))

        self.canvas = tk.Canvas(self.board_frame,
                                width=self.CANVAS_SIZE,
                                height=self.CANVAS_SIZE,
                                bg=self.colors['bg'],
                                highlightthickness=0,
                                cursor="hand2")
        self.register_widget(self.canvas, 'bg')
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-3>", self.on_right_click)

        self.info_frame = tk.Frame(left, bg=self.colors['bg'])
        self.register_widget(self.info_frame, 'bg')
        self.info_frame.pack(fill=tk.X, pady=(12, 0))

        self.game_info = tk.Label(self.info_frame,
                                  text="Nouvelle partie",
                                  font=("Arial", 12),
                                  fg=self.colors['fg'],
                                  bg=self.colors['bg'])
        self.register_widget(self.game_info, 'bg', include_fg=True)
        self.game_info.pack(anchor='w')

        self.eval_label = tk.Label(self.info_frame,
                                   text="= 0.00",
                                   fg=self.colors['fg'],
                                   bg=self.colors['bg'],
                                   font=("Arial", 12, "bold"))
        self.register_widget(self.eval_label, 'bg', include_fg=True)
        self.eval_label.pack(anchor='w', pady=(4, 0))

        self.actions_frame = tk.Frame(left, bg=self.colors['bg'])
        self.register_widget(self.actions_frame, 'bg')
        self.actions_frame.pack(fill=tk.X, pady=(12, 0))
        self.actions_frame.columnconfigure((0, 1), weight=1)

        self.start_btn = None
        self.hint_btn = tk.Button(self.actions_frame,
                                  text="💡 Indice",
                                  command=self.show_hint,
                                  bg='#2196F3',
                                  fg='white',
                                  font=("Arial", 10, "bold"),
                                  relief=tk.FLAT,
                                  padx=8,
                                  pady=6)
        self.hint_btn.grid(row=0, column=0, sticky="ew", padx=4, pady=2)

        self.clear_hint_btn = tk.Button(self.actions_frame,
                                        text="🧽 Effacer",
                                        command=self.clear_hint,
                                        bg='#607D8B',
                                        fg='white',
                                        font=("Arial", 10, "bold"),
                                        relief=tk.FLAT,
                                        padx=8,
                                        pady=6)
        self.clear_hint_btn.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        self.secondary_actions_frame = tk.Frame(left, bg=self.colors['bg'])
        self.register_widget(self.secondary_actions_frame, 'bg')
        self.secondary_actions_frame.pack(fill=tk.X, pady=(4, 0))
        self.secondary_actions_frame.columnconfigure((0, 1), weight=1)

        undo_btn = tk.Button(self.secondary_actions_frame,
                             text="↩️ Annuler",
                             command=self.undo_move,
                             relief=tk.FLAT,
                             bg=self.colors['panel'],
                             fg=self.colors['fg'],
                             padx=6,
                             pady=6)
        self.register_widget(undo_btn, 'panel', include_fg=True)
        undo_btn.grid(row=0, column=0, sticky="ew", padx=4, pady=2)

        redo_btn = tk.Button(self.secondary_actions_frame,
                             text="↪️ Refaire",
                             command=self.redo_move,
                             relief=tk.FLAT,
                             bg=self.colors['panel'],
                             fg=self.colors['fg'],
                             padx=6,
                             pady=6)
        self.register_widget(redo_btn, 'panel', include_fg=True)
        redo_btn.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        self.history_frame = tk.LabelFrame(left,
                                           text="📜 Historique de la partie",
                                           fg=self.colors['fg'],
                                           bg=self.colors['panel'],
                                           font=("Arial", 11, "bold"))
        self.register_widget(self.history_frame, 'panel', include_fg=True)
        self.history_frame.pack(fill=tk.BOTH, expand=False, pady=(12, 0))

        self.moves_text = scrolledtext.ScrolledText(self.history_frame,
                                                    height=6,
                                                    bg=self.colors['panel'],
                                                    fg=self.colors['fg'],
                                                    font=("Consolas", 10),
                                                    state=tk.DISABLED)
        self.register_widget(self.moves_text, 'panel', include_fg=True)
        self.moves_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.history_actions = tk.Frame(self.history_frame, bg=self.colors['panel'])
        self.register_widget(self.history_actions, 'panel')
        self.history_actions.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.history_actions.columnconfigure((0, 1, 2), weight=1)

        self.import_pgn_btn = tk.Button(self.history_actions,
                                        text="📥 Importer PGN",
                                        command=self.import_pgn,
                                        relief=tk.FLAT,
                                        bg=self.colors['panel'],
                                        fg=self.colors['fg'])
        self.register_widget(self.import_pgn_btn, 'panel', include_fg=True)
        self.import_pgn_btn.grid(row=0, column=0, sticky="ew", padx=4)

        self.export_pgn_btn = tk.Button(self.history_actions,
                                        text="📤 Exporter",
                                        command=self.export_pgn,
                                        relief=tk.FLAT,
                                        bg=self.colors['panel'],
                                        fg=self.colors['fg'])
        self.register_widget(self.export_pgn_btn, 'panel', include_fg=True)
        self.export_pgn_btn.grid(row=0, column=1, sticky="ew", padx=4)

        self.copy_pgn_btn = tk.Button(self.history_actions,
                                      text="📋 Copier",
                                      command=self.copy_pgn,
                                      relief=tk.FLAT,
                                      bg=self.colors['panel'],
                                      fg=self.colors['fg'])
        self.register_widget(self.copy_pgn_btn, 'panel', include_fg=True)
        self.copy_pgn_btn.grid(row=0, column=2, sticky="ew", padx=4)

        right = tk.Frame(main, bg=self.colors['panel'], width=420)
        self.register_widget(right, 'panel')
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(16, 0))
        right.pack_propagate(False)
        self.right_frame = right

        notebook = ttk.Notebook(right)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.sidebar_notebook = notebook

        self.game_tab = tk.Frame(notebook, bg=self.colors['panel'])
        self.register_widget(self.game_tab, 'panel')
        notebook.add(self.game_tab, text="Partie")

        player_frame = tk.LabelFrame(self.game_tab,
                                     text="Configuration du joueur",
                                     fg=self.colors['fg'],
                                     bg=self.colors['panel'],
                                     font=("Arial", 11, "bold"))
        self.register_widget(player_frame, 'panel', include_fg=True)
        player_frame.pack(fill=tk.X, padx=12, pady=(12, 8))

        player_label = tk.Label(player_frame, text="Votre couleur", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(player_label, 'panel', include_fg=True)
        player_label.pack(anchor='w', padx=8, pady=(4, 0))

        color_row = tk.Frame(player_frame, bg=self.colors['panel'])
        self.register_widget(color_row, 'panel')
        color_row.pack(anchor='w', padx=8, pady=(2, 8))
        self.color_var = tk.StringVar(value="white")
        white_radio = tk.Radiobutton(color_row, text="Blancs", variable=self.color_var, value="white",
                                     fg=self.colors['fg'], bg=self.colors['panel'], selectcolor=self.colors['panel'])
        self.register_widget(white_radio, 'panel', include_fg=True)
        white_radio.pack(side=tk.LEFT, padx=(0, 12))
        black_radio = tk.Radiobutton(color_row, text="Noirs", variable=self.color_var, value="black",
                                     fg=self.colors['fg'], bg=self.colors['panel'], selectcolor=self.colors['panel'])
        self.register_widget(black_radio, 'panel', include_fg=True)
        black_radio.pack(side=tk.LEFT)

        difficulty_label = tk.Label(player_frame, text="Difficulté (ELO)", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(difficulty_label, 'panel', include_fg=True)
        difficulty_label.pack(anchor='w', padx=8)
        self.difficulty_var = tk.IntVar(value=1500)
        difficulty_scale = tk.Scale(player_frame,
                                    from_=800,
                                    to=3000,
                                    orient=tk.HORIZONTAL,
                                    variable=self.difficulty_var,
                                    bg=self.colors['panel'],
                                    fg=self.colors['fg'],
                                    highlightbackground=self.colors['panel'],
                                    troughcolor='#555555' if self.theme_dark else '#d0d0d0')
        self.register_widget(difficulty_scale, 'panel', include_fg=True)
        difficulty_scale.pack(fill=tk.X, padx=8, pady=(4, 8))

        training_frame = tk.LabelFrame(player_frame,
                                       text="Mode ouverture",
                                       fg=self.colors['fg'],
                                       bg=self.colors['panel'],
                                       font=("Arial", 11, "bold"))
        self.register_widget(training_frame, 'panel', include_fg=True)
        training_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

        training_toggle = tk.Checkbutton(training_frame,
                                         text="Activer l'entraînement d'ouverture",
                                         variable=self.training_mode_var,
                                         command=self.on_training_toggle,
                                         fg=self.colors['fg'],
                                         bg=self.colors['panel'],
                                         selectcolor=self.colors['panel'])
        self.register_widget(training_toggle, 'panel', include_fg=True)
        training_toggle.pack(anchor='w', padx=8, pady=(6, 4))

        combo_row = tk.Frame(training_frame, bg=self.colors['panel'])
        self.register_widget(combo_row, 'panel')
        combo_row.pack(fill=tk.X, padx=8, pady=(0, 6))

        combo_label = tk.Label(combo_row,
                               text="Séquence :",
                               fg=self.colors['fg'],
                               bg=self.colors['panel'])
        self.register_widget(combo_label, 'panel', include_fg=True)
        combo_label.pack(side=tk.LEFT)

        self.training_combo = ttk.Combobox(combo_row,
                                           state="readonly",
                                           values=list(self.training_lines.keys()),
                                           textvariable=self.training_opening_var)
        self.training_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        try:
            self.training_combo.current(list(self.training_lines.keys()).index(self.training_opening_var.get()))
        except ValueError:
            self.training_combo.current(0)
        self.training_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_training_choice())

        self.training_desc_label = tk.Label(training_frame,
                                            textvariable=self.training_description_var,
                                            wraplength=280,
                                            justify=tk.LEFT,
                                            fg=self.colors['fg'],
                                            bg=self.colors['panel'],
                                            font=("Arial", 9))
        self.register_widget(self.training_desc_label, 'panel', include_fg=True)
        self.training_desc_label.pack(fill=tk.X, padx=8, pady=(0, 6))

        self.training_status_label = tk.Label(training_frame,
                                              textvariable=self.training_status_var,
                                              fg=self.colors['accent'],
                                              bg=self.colors['panel'],
                                              font=("Arial", 10, "italic"))
        self.register_widget(self.training_status_label, 'panel', include_fg=True)
        self.training_status_label.pack(fill=tk.X, padx=8, pady=(0, 6))

        self.on_training_choice()

        clock_frame = tk.LabelFrame(self.game_tab,
                                    text="Horloge",
                                    fg=self.colors['fg'],
                                    bg=self.colors['panel'],
                                    font=("Arial", 11, "bold"))
        self.register_widget(clock_frame, 'panel', include_fg=True)
        clock_frame.pack(fill=tk.X, padx=12, pady=8)

        self.start_game_tab_btn = tk.Button(self.game_tab,
                                            text="🚀 Lancer la partie",
                                            command=self.start_new_game,
                                            bg='#4CAF50',
                                            fg='white',
                                            font=("Arial", 11, "bold"),
                                            relief=tk.FLAT,
                                            padx=10,
                                            pady=10)
        self.start_game_tab_btn.pack(fill=tk.X, padx=20, pady=(4, 16))

        self.resign_btn = tk.Button(self.game_tab,
                                    text="🏳️ Abandonner",
                                    command=self.resign_game,
                                    bg='#f44336',
                                    fg='white',
                                    font=("Arial", 11, "bold"),
                                    relief=tk.FLAT,
                                    padx=10,
                                    pady=10)
        self.resign_btn.pack(fill=tk.X, padx=20, pady=(0, 12))

        self.clock_white_lbl = tk.Label(clock_frame,
                                        text="Blancs: 05:00",
                                        fg=self.colors['fg'],
                                        bg=self.colors['panel'],
                                        font=("Consolas", 12, "bold"))
        self.register_widget(self.clock_white_lbl, 'panel', include_fg=True)
        self.clock_white_lbl.pack(fill=tk.X, padx=8, pady=(8, 2))

        self.clock_black_lbl = tk.Label(clock_frame,
                                        text="Noirs:  05:00",
                                        fg=self.colors['fg'],
                                        bg=self.colors['panel'],
                                        font=("Consolas", 12, "bold"))
        self.register_widget(self.clock_black_lbl, 'panel', include_fg=True)
        self.clock_black_lbl.pack(fill=tk.X, padx=8, pady=(0, 8))

        timer_row = tk.Frame(clock_frame, bg=self.colors['panel'])
        self.register_widget(timer_row, 'panel')
        timer_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        minutes_label = tk.Label(timer_row, text="Minutes:", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(minutes_label, 'panel', include_fg=True)
        minutes_label.pack(side=tk.LEFT)
        self.time_minutes_var = tk.IntVar(value=5)
        minutes_spin = tk.Spinbox(timer_row, from_=1, to=180, textvariable=self.time_minutes_var, width=5,
                                  bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(minutes_spin, 'panel', include_fg=True)
        minutes_spin.pack(side=tk.LEFT, padx=6)
        increment_label = tk.Label(timer_row, text="Incr (s):", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(increment_label, 'panel', include_fg=True)
        increment_label.pack(side=tk.LEFT, padx=(12, 0))
        self.increment_var = tk.IntVar(value=0)
        increment_spin = tk.Spinbox(timer_row, from_=0, to=60, textvariable=self.increment_var, width=5,
                                    bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(increment_spin, 'panel', include_fg=True)
        increment_spin.pack(side=tk.LEFT, padx=6)

        self.analysis_tab = tk.Frame(notebook, bg=self.colors['panel'])
        self.register_widget(self.analysis_tab, 'panel')
        notebook.add(self.analysis_tab, text="Analyse")

        analysis_header = tk.Frame(self.analysis_tab, bg=self.colors['panel'])
        self.register_widget(analysis_header, 'panel')
        analysis_header.pack(fill=tk.X, padx=12, pady=(12, 0))
        depth_label = tk.Label(analysis_header, text="Profondeur d'analyse", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(depth_label, 'panel', include_fg=True)
        depth_label.pack(anchor='w')
        self.depth_var = tk.IntVar(value=self.analysis_depth)
        depth_scale = tk.Scale(self.analysis_tab,
                               from_=8,
                               to=30,
                               orient=tk.HORIZONTAL,
                               variable=self.depth_var,
                               bg=self.colors['panel'],
                               fg=self.colors['fg'],
                               highlightbackground=self.colors['panel'],
                               troughcolor='#555555' if self.theme_dark else '#d0d0d0',
                               command=lambda _: self.on_depth_change())
        self.register_widget(depth_scale, 'panel', include_fg=True)
        depth_scale.pack(fill=tk.X, padx=12, pady=(6, 12))

        self.analysis_text = scrolledtext.ScrolledText(self.analysis_tab,
                                                       height=12,
                                                       bg=self.colors['panel'],
                                                       fg=self.colors['fg'],
                                                       font=("Consolas", 9),
                                                       state=tk.DISABLED)
        self.register_widget(self.analysis_text, 'panel', include_fg=True)
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        analysis_buttons = tk.Frame(self.analysis_tab, bg=self.colors['panel'])
        self.register_widget(analysis_buttons, 'panel')
        analysis_buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        analysis_buttons.columnconfigure((0, 1), weight=1)

        analyze_btn = tk.Button(analysis_buttons,
                                text="📈 Analyser la position",
                                command=self.analyze_position,
                                bg=self.colors['accent'],
                                fg='white',
                                font=("Arial", 10, "bold"),
                                relief=tk.FLAT,
                                padx=6,
                                pady=6)
        analyze_btn.grid(row=0, column=0, sticky="ew", padx=4)

        clear_btn = tk.Button(analysis_buttons,
                              text="🧹 Effacer",
                              command=self.clear_analysis,
                              bg='#607D8B',
                              fg='white',
                              font=("Arial", 10, "bold"),
                              relief=tk.FLAT,
                              padx=6,
                              pady=6)
        clear_btn.grid(row=0, column=1, sticky="ew", padx=4)

        self.engine_tab = tk.Frame(notebook, bg=self.colors['panel'])
        self.register_widget(self.engine_tab, 'panel')
        notebook.add(self.engine_tab, text="Moteur")

        engine_options = tk.LabelFrame(self.engine_tab,
                                       text="Paramètres Stockfish",
                                       fg=self.colors['fg'],
                                       bg=self.colors['panel'],
                                       font=("Arial", 11, "bold"))
        self.register_widget(engine_options, 'panel', include_fg=True)
        engine_options.pack(fill=tk.X, padx=12, pady=(12, 8))

        threads_row = tk.Frame(engine_options, bg=self.colors['panel'])
        self.register_widget(threads_row, 'panel')
        threads_row.pack(fill=tk.X, padx=8, pady=4)
        threads_label = tk.Label(threads_row, text="Threads", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(threads_label, 'panel', include_fg=True)
        threads_label.pack(side=tk.LEFT)
        self.threads_var = tk.IntVar(value=2)
        threads_spin = tk.Spinbox(threads_row, from_=1, to=16, textvariable=self.threads_var, width=5,
                                  command=self.apply_engine_options,
                                  bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(threads_spin, 'panel', include_fg=True)
        threads_spin.pack(side=tk.LEFT, padx=6)

        hash_label = tk.Label(threads_row, text="Hash (MB)", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(hash_label, 'panel', include_fg=True)
        hash_label.pack(side=tk.LEFT, padx=(12, 0))
        self.hash_var = tk.IntVar(value=128)
        hash_spin = tk.Spinbox(threads_row, from_=16, to=4096, textvariable=self.hash_var, width=6,
                               command=self.apply_engine_options,
                               bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(hash_spin, 'panel', include_fg=True)
        hash_spin.pack(side=tk.LEFT, padx=6)

        skill_row = tk.Frame(engine_options, bg=self.colors['panel'])
        self.register_widget(skill_row, 'panel')
        skill_row.pack(fill=tk.X, padx=8, pady=4)
        skill_label = tk.Label(skill_row, text="Skill (0-20)", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(skill_label, 'panel', include_fg=True)
        skill_label.pack(side=tk.LEFT)
        self.skill_var = tk.IntVar(value=20)
        skill_spin = tk.Spinbox(skill_row, from_=0, to=20, textvariable=self.skill_var, width=5,
                                command=self.apply_engine_options,
                                bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(skill_spin, 'panel', include_fg=True)
        skill_spin.pack(side=tk.LEFT, padx=6)

        appearance_row = tk.Frame(engine_options, bg=self.colors['panel'])
        self.register_widget(appearance_row, 'panel')
        appearance_row.pack(fill=tk.X, padx=8, pady=6)
        self.flip_var = tk.BooleanVar(value=self.flip_board)
        flip_check = tk.Checkbutton(appearance_row,
                                    text="Inverser l'échiquier",
                                    var=self.flip_var,
                                    command=self.toggle_flip,
                                    fg=self.colors['fg'],
                                    bg=self.colors['panel'],
                                    selectcolor=self.colors['panel'])
        self.register_widget(flip_check, 'panel', include_fg=True)
        flip_check.pack(side=tk.LEFT)

        theme_btn = tk.Button(appearance_row,
                              text="🎨 Thème clair/sombre",
                              command=self.toggle_theme,
                              bg=self.colors['panel'],
                              fg=self.colors['fg'],
                              relief=tk.FLAT,
                              padx=6,
                              pady=6)
        self.register_widget(theme_btn, 'panel', include_fg=True)
        theme_btn.pack(side=tk.RIGHT)

        engine_buttons = tk.Frame(self.engine_tab, bg=self.colors['panel'])
        self.register_widget(engine_buttons, 'panel')
        engine_buttons.pack(fill=tk.X, padx=12, pady=(0, 8))
        engine_buttons.columnconfigure(0, weight=1)

        choose_btn = tk.Button(engine_buttons,
                               text="📁 Choisir Stockfish",
                               command=self.choose_stockfish,
                               bg=self.colors['panel'],
                               fg=self.colors['fg'],
                               relief=tk.FLAT,
                               padx=6,
                               pady=6)
        self.register_widget(choose_btn, 'panel', include_fg=True)
        choose_btn.grid(row=0, column=0, sticky="ew", padx=4)

        self.auto_tab = tk.Frame(notebook, bg=self.colors['panel'])
        self.register_widget(self.auto_tab, 'panel')
        notebook.add(self.auto_tab, text="Mode auto")

        auto_frame = tk.LabelFrame(self.auto_tab,
                                   text="Stockfish vs Stockfish",
                                   fg=self.colors['fg'],
                                   bg=self.colors['panel'],
                                   font=("Arial", 11, "bold"))
        self.register_widget(auto_frame, 'panel', include_fg=True)
        auto_frame.pack(fill=tk.X, padx=12, pady=(12, 8))

        auto_row = tk.Frame(auto_frame, bg=self.colors['panel'])
        self.register_widget(auto_row, 'panel')
        auto_row.pack(fill=tk.X, padx=8, pady=4)
        auto_white_label = tk.Label(auto_row, text="ELO Blancs:", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(auto_white_label, 'panel', include_fg=True)
        auto_white_label.pack(side=tk.LEFT)
        self.auto_white_elo_var = tk.IntVar(value=self.auto_white_elo)
        auto_white_spin = tk.Spinbox(auto_row, from_=800, to=3000, textvariable=self.auto_white_elo_var, width=6,
                                     bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(auto_white_spin, 'panel', include_fg=True)
        auto_white_spin.pack(side=tk.LEFT, padx=6)

        auto_black_label = tk.Label(auto_row, text="ELO Noirs:", fg=self.colors['fg'], bg=self.colors['panel'])
        self.register_widget(auto_black_label, 'panel', include_fg=True)
        auto_black_label.pack(side=tk.LEFT, padx=(12, 0))
        self.auto_black_elo_var = tk.IntVar(value=self.auto_black_elo)
        auto_black_spin = tk.Spinbox(auto_row, from_=800, to=3000, textvariable=self.auto_black_elo_var, width=6,
                                     bg=self.colors['panel'], fg=self.colors['fg'])
        self.register_widget(auto_black_spin, 'panel', include_fg=True)
        auto_black_spin.pack(side=tk.LEFT, padx=6)

        auto_buttons = tk.Frame(self.auto_tab, bg=self.colors['panel'])
        self.register_widget(auto_buttons, 'panel')
        auto_buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        auto_buttons.columnconfigure((0, 1), weight=1)

        start_auto_btn = tk.Button(auto_buttons,
                                   text="🚀 Lancer",
                                   command=self.start_auto_game,
                                   bg='#8E24AA',
                                   fg='white',
                                   font=("Arial", 10, "bold"),
                                   relief=tk.FLAT,
                                   padx=6,
                                   pady=6)
        start_auto_btn.grid(row=0, column=0, sticky="ew", padx=4)

        stop_auto_btn = tk.Button(auto_buttons,
                                  text="⏹️ Arrêter",
                                  command=self.stop_auto_game,
                                  bg='#B71C1C',
                                  fg='white',
                                  font=("Arial", 10, "bold"),
                                  relief=tk.FLAT,
                                  padx=6,
                                  pady=6)
        stop_auto_btn.grid(row=0, column=1, sticky="ew", padx=4)

        self.stats_tab = tk.Frame(notebook, bg=self.colors['panel'])
        self.register_widget(self.stats_tab, 'panel')
        notebook.add(self.stats_tab, text="Statistiques")

        stats_frame = tk.LabelFrame(self.stats_tab,
                                    text="Bilan des parties",
                                    fg=self.colors['fg'],
                                    bg=self.colors['panel'],
                                    font=("Arial", 11, "bold"))
        self.register_widget(stats_frame, 'panel', include_fg=True)
        stats_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self.stats_text = tk.Text(stats_frame,
                                  height=10,
                                  bg=self.colors['panel'],
                                  fg=self.colors['fg'],
                                  font=("Arial", 10),
                                  state=tk.DISABLED)
        self.register_widget(self.stats_text, 'panel', include_fg=True)
        self.stats_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.apply_theme_to_widgets()

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
        self.master.bind("<Escape>", self.on_escape)


    # ---------- Entraînement d'ouvertures ----------

    def load_default_openings(self):
        openings = load_training_openings(fallback=MINIMAL_OPENINGS)
        if not openings:
            return {key: dict(value) for key, value in MINIMAL_OPENINGS.items()}
        return openings

    def on_training_toggle(self):
        if not self.training_mode_var.get():
            self.training_status_var.set("Mode libre")
            self.reset_training_state()
        else:
            self.on_training_choice()

    def on_training_choice(self):
        name = self.training_opening_var.get()
        data = self.training_lines.get(name, {})
        description = data.get("description", "Mode libre sans séquence imposée.")
        self.training_description_var.set(description)
        if (not self.training_mode_var.get()) or not data.get("moves"):
            self.training_status_var.set("Mode libre")
            return
        recommended = data.get("recommended_color")
        if recommended:
            self.training_status_var.set(f"Séquence prête (couleur conseillée : {recommended}).")
        else:
            self.training_status_var.set("Séquence prête.")

    def reset_training_state(self):
        self.training_line = []
        self.training_index = 0
        self.training_active = False

    def initialize_training_line(self, name):
        self.reset_training_state()
        data = self.training_lines.get(name, {})
        moves_san = data.get("moves", [])
        if not moves_san:
            self.training_status_var.set("Mode libre")
            return False
        preview_board = chess.Board()
        parsed_moves = []
        try:
            for san in moves_san:
                move = preview_board.parse_san(san)
                parsed_moves.append(move)
                preview_board.push(move)
        except ValueError as exc:
            self.training_status_var.set("Séquence invalide")
            self.add_analysis(f"❌ Séquence invalide ({name}): {exc}")
            messagebox.showerror("Entraînement ouverture", f"Séquence invalide pour {name}: {exc}")
            return False
        self.training_line = parsed_moves
        self.training_index = 0
        self.training_active = True
        self.update_training_progress()
        return True

    def update_training_progress(self):
        if not self.training_active:
            return
        total = len(self.training_line)
        done = min(self.training_index, total)
        self.training_status_var.set(f"Progression: {done}/{total} coups")

    def play_training_moves_until_player_turn(self):
        if not self.training_active:
            return
        while self.training_active and self.training_index < len(self.training_line) and self.board.turn != self.player_color:
            expected = self.training_line[self.training_index]
            if expected not in self.board.legal_moves:
                self.finish_training_mode(success=False, reason="séquence non disponible depuis cette position")
                return
            if self.increment > 0:
                if self.board.turn == chess.WHITE:
                    self.time_white += self.increment
                else:
                    self.time_black += self.increment
            self.board.push(expected)
            self.last_move = expected
            self.training_index += 1
        self.update_training_progress()
        self.update_board_display()
        self.update_clock_labels()
        if self.training_active and (self.board.is_game_over() or self.training_index >= len(self.training_line)):
            self.finish_training_mode(success=True)

    def finish_training_mode(self, success=True, reason=None):
        if not self.training_active and not success:
            self.training_status_var.set("Séquence interrompue")
            return
        if not self.training_active and success:
            self.training_status_var.set(f"Ligne terminée ({len(self.training_line)} coups) ✅")
            return
        self.training_active = False
        total = len(self.training_line)
        if success:
            self.training_status_var.set(f"Ligne terminée ({total} coups) ✅")
            message = f"🎉 Ligne d'ouverture terminée ({self.training_opening_var.get()})."
            self.add_analysis(message)
            try:
                messagebox.showinfo("Entraînement ouverture", message)
            except Exception:
                pass
            if (self.stockfish_ready and not self.board.is_game_over() and not self.auto_mode
                    and self.board.turn != self.player_color):
                self.master.after(600, self.stockfish_move_once_for_manual)
        else:
            info = "⚠️ Séquence interrompue."
            if reason:
                info = f"⚠️ Séquence interrompue: {reason}."
            self.training_status_var.set("Séquence interrompue")
            self.add_analysis(info)
            try:
                messagebox.showwarning("Entraînement ouverture", info)
            except Exception:
                pass

    # ---------- Coordonnées & mapping ----------
    def get_board_padding(self):
        return self.PADDING

    def get_canvas_size(self):
        pad = self.get_board_padding()
        return self.BOARD_SIZE + 2 * pad

    def square_to_coords(self, square):
        file = chess.square_file(square)  # 0..7
        rank = chess.square_rank(square)  # 0..7
        if self.flip_board:
            file = 7 - file
            rank = 7 - rank
        sq_size = self.BOARD_SIZE // 8
        pad = self.get_board_padding()
        x = pad + file * sq_size
        y = pad + (7 - rank) * sq_size
        return x, y

    def square_center(self, square):
        x, y = self.square_to_coords(square)
        sq = self.BOARD_SIZE // 8
        return x + sq/2, y + sq/2

    def coords_to_square(self, x, y):
        pad = self.get_board_padding()
        if not (pad <= x < pad + self.BOARD_SIZE and
                pad <= y < pad + self.BOARD_SIZE):
            return None
        sq_size = self.BOARD_SIZE // 8
        file = (x - pad) // sq_size
        rank_from_top = (y - pad) // sq_size
        rank = 7 - rank_from_top
        if self.flip_board:
            file = 7 - file
            rank = 7 - rank
        return chess.square(int(file), int(rank))

    # ---------- Rendu plateau ----------
    def render_board_image(self):
        orientation = chess.BLACK if self.flip_board else chess.WHITE
        svg = chess.svg.board(
            board=self.board,
            lastmove=self.last_move,
            size=self.BOARD_SIZE,
            coordinates=False,
            squares=None,
            orientation=orientation
        )
        png_bytes = cairosvg.svg2png(bytestring=svg.encode('utf-8'),
                                     output_width=self.BOARD_SIZE, output_height=self.BOARD_SIZE)
        return Image.open(BytesIO(png_bytes))

    def draw_coordinates(self):
        self.canvas.delete("coord")
        sq = self.BOARD_SIZE // 8
        pad = self.get_board_padding()
        # Files
        for i in range(8):
            file_idx = i if not self.flip_board else 7 - i
            letter = chr(ord('a') + file_idx)
            cx = pad + i * sq + sq/2
            cy = pad + self.BOARD_SIZE + 16
            self.canvas.create_text(cx, cy, text=letter, fill=self.colors['coord'],
                                    font=("Consolas", 12, "bold"), tags="coord")
        # Rangs
        for i in range(8):
            rank_idx = 8 - i if not self.flip_board else i + 1
            cx = pad - 16
            cy = pad + i * sq + sq/2
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
        size = self.get_canvas_size()
        pad = self.get_board_padding()
        bg_color = self.colors['bg']
        self.canvas.config(width=size, height=size)
        self.canvas.configure(bg=bg_color)
        self.canvas.delete("board")
        self.canvas.create_rectangle(0, 0, size, size, fill=bg_color, width=0, tags="board")
        self.canvas.create_image(pad, pad, anchor="nw", image=self.tk_img, tags="board")
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
            allowed_color = self.player_color
            if piece and piece.color == allowed_color:
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

        allowed_color = self.player_color
        if piece and piece.color == allowed_color:
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
        training_player_move = False
        if self.training_active and self.board.turn == self.player_color:
            expected = self.training_line[self.training_index] if self.training_index < len(self.training_line) else None
            if expected is None:
                self.finish_training_mode(success=True)
                if (self.stockfish_ready and not self.board.is_game_over() and not self.auto_mode
                        and self.board.turn != self.player_color):
                    self.master.after(120, self.stockfish_move_once_for_manual)
                return
            if move != expected:
                try:
                    expected_san = self.board.san(expected)
                except Exception:
                    expected_san = expected.uci()
                messagebox.showwarning("Entraînement", f"Ce coup n'est pas dans la séquence choisie. Coup attendu : {expected_san}.")
                self.update_training_progress()
                return
            training_player_move = True

        if self.board.turn == chess.WHITE and self.increment > 0:
            self.time_white += self.increment
        elif self.board.turn == chess.BLACK and self.increment > 0:
            self.time_black += self.increment
        self.board.push(move)
        self.last_move = move
        self.move_stack_for_redo.clear()
        self.hint_move = None
        if training_player_move:
            self.training_index += 1
            self.update_training_progress()
        self.update_board_display()

        if self.training_active:
            if self.board.is_game_over():
                self.finish_training_mode(success=True)
                return
            self.play_training_moves_until_player_turn()
            if self.training_active:
                self.disable_inputs(False)
            return

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

    def toggle_theme(self):
        self.theme_dark = not self.theme_dark
        self.colors = self.get_colors(self.theme_dark)
        self.configure_ttk_styles()
        self.master.configure(bg=self.colors['bg'])
        self.apply_theme_to_widgets()
        self.update_board_display()
        self.update_clock_labels()
        status_color = '#00ff00' if self.stockfish_ready else '#ff0000'
        self.status_label.config(fg=status_color)

    def on_escape(self, event=None):
        self.selected_square = None
        self.legal_targets_cache = set()
        self.update_board_display()

    def _recolor_recursive(self, widget):
        """Compatibilité : applique la palette actuelle aux widgets."""
        self.apply_theme_to_widgets()

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
        for b in (self.start_btn, self.start_game_tab_btn, self.hint_btn, self.clear_hint_btn, self.resign_btn):
            try: b.config(state=state)
            except: pass

    # ---------- Démarrer/Finir (manuel) ----------
    def start_new_game(self):
        selected_opening = self.training_opening_var.get()
        opening_data = self.training_lines.get(selected_opening, {})
        training_requested = self.training_mode_var.get() and bool(opening_data.get("moves"))
        if not self.stockfish_ready and not training_requested:
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
        self.reset_training_state()

        training_active = False
        if training_requested:
            if self.initialize_training_line(selected_opening):
                training_active = True
                desc = opening_data.get("description", "")
                recap = f"📘 Séquence: {selected_opening}"
                if desc:
                    recap += f"\n📝 {desc}"
                recommended = opening_data.get("recommended_color")
                if recommended:
                    recap += f"\n🎯 Couleur conseillée: {recommended}"
                self.add_analysis(recap)
            else:
                training_active = False
                self.training_mode_var.set(False)
                self.training_status_var.set("Mode libre")
        else:
            self.training_status_var.set("Mode libre")

        self.flip_board = (self.player_color == chess.BLACK)
        self.flip_var.set(self.flip_board)

        self.update_board_display()
        self.add_analysis("🎯 Nouvelle partie (manuelle) commencée!")
        self.disable_inputs(False)
        self.start_clock()

        if training_active:
            self.play_training_moves_until_player_turn()
        elif self.player_color == chess.BLACK:
            self.master.after(500, self.stockfish_move_once_for_manual)

    def resign_game(self):
        if self.game_active:
            if messagebox.askyesno("Abandon", "Êtes-vous sûr de vouloir abandonner ?"):
                self.game_active = False
                self.stop_clock()
                self.stop_auto_game()
                self.training_active = False
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
        self.training_active = False
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
