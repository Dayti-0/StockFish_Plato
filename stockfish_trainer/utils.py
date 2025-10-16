"""Utility helpers for the Stockfish trainer application."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Union


MINIMAL_OPENINGS: Dict[str, Dict[str, object]] = {
    "Libre": {
        "variations": [],
        "description": "Mode libre sans séquence imposée.",
        "recommended_color": None,
    }
}


def clamp(v, vmin, vmax):
    return max(vmin, min(v, vmax))


_PathLike = Union[str, Path]


def _coerce_path(json_path: Optional[Union[_PathLike, Iterable[_PathLike]]]) -> Path:
    if json_path is None:
        return Path(__file__).resolve().parent / "data" / "openings.json"

    if isinstance(json_path, (str, Path)):
        return Path(json_path)

    parts = [str(part) for part in json_path]
    if not parts:
        raise ValueError("Le chemin JSON fourni est vide.")
    return Path(Path(parts[0]), *parts[1:])


def load_training_openings(
    json_path: Optional[Union[_PathLike, Iterable[_PathLike]]] = None,
    fallback: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> Dict[str, Dict[str, object]]:
    """Load the training openings from a JSON file.

    Parameters
    ----------
    json_path:
        Path to the JSON file containing openings definitions. When ``None`` the
        default ``stockfish_trainer/data/openings.json`` file is used.
    fallback:
        Optional fallback dictionary returned when the JSON file is missing or
        invalid. Defaults to :data:`MINIMAL_OPENINGS`.

    Returns
    -------
    dict
        Dictionary keyed by opening name with ``variations`` definitions, plus
        ``description`` and ``recommended_color`` metadata. Each variation
        contains a ``name`` and a ``moves`` list (SAN strings).
    """

    fallback_data: Mapping[str, Mapping[str, object]] = fallback or MINIMAL_OPENINGS

    try:
        path = _coerce_path(json_path)
        with path.open("r", encoding="utf-8") as handle:
            raw_data = json.load(handle)

        if not isinstance(raw_data, dict):
            raise ValueError("Le fichier d'ouvertures doit contenir un objet JSON.")

        normalized: Dict[str, Dict[str, object]] = {}
        for name, entry in raw_data.items():
            if not isinstance(entry, dict):
                raise ValueError(f"Entrée invalide pour '{name}'.")

            variations_data = entry.get("variations")
            if variations_data is None and "moves" in entry:
                variations_data = [
                    {
                        "name": entry.get("variation_name"),
                        "moves": entry.get("moves", []),
                    }
                ]

            if variations_data is None:
                variations_data = []

            if not isinstance(variations_data, list):
                raise ValueError(
                    f"Les variations pour '{name}' doivent être fournies sous forme de liste."
                )

            normalized_variations = []
            for idx, variation in enumerate(variations_data):
                if variation is None:
                    continue
                if isinstance(variation, list):
                    variation_entry = {"moves": variation}
                elif isinstance(variation, dict):
                    variation_entry = variation
                else:
                    raise ValueError(
                        f"Variation invalide à l'indice {idx} pour '{name}'."
                    )

                moves = variation_entry.get("moves", [])
                if moves is None:
                    moves = []
                if not isinstance(moves, list):
                    raise ValueError(
                        f"La séquence de coups pour la variation {idx + 1} de '{name}' doit être une liste."
                    )
                normalized_moves = [str(move) for move in moves]

                var_name = variation_entry.get("name")
                if var_name is None or str(var_name).strip() == "":
                    var_name = f"Variante {idx + 1}"
                else:
                    var_name = str(var_name)

                normalized_variations.append({
                    "name": var_name,
                    "moves": normalized_moves,
                })

            recommended_color = entry.get("recommended_color")
            if recommended_color is not None and not isinstance(
                recommended_color, str
            ):
                recommended_color = str(recommended_color)

            normalized[name] = {
                "variations": normalized_variations,
                "description": str(entry.get("description", "")),
                "recommended_color": recommended_color,
            }

        if not normalized:
            return deepcopy({key: dict(value) for key, value in fallback_data.items()})

        return normalized

    except (OSError, json.JSONDecodeError, ValueError) as exc:  # pragma: no cover - UI feedback
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Ouvertures",
                "Impossible de charger le fichier d'ouvertures:\n" f"{exc}",
            )
        except Exception:
            # In case the GUI is not available yet (e.g. during CLI usage)
            pass

        return deepcopy({key: dict(value) for key, value in fallback_data.items()})
