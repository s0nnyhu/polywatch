"""Persistance de l'état : mémorise les trades déjà notifiés pour éviter les doublons."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_IDS_PER_USER = 500


class State:
    """Garde en mémoire les identifiants des trades déjà vus, par utilisateur."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._seen: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._seen = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                logger.warning("Impossible de lire %s : %s", self.path, exc)
                self._seen = {}

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._seen, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Impossible d'écrire %s : %s", self.path, exc)

    def is_known(self, address: str, uid: str) -> bool:
        return uid in self._seen.get(address, [])

    def mark(self, address: str, uid: str) -> None:
        ids = self._seen.setdefault(address, [])
        if uid not in ids:
            ids.append(uid)
            if len(ids) > MAX_IDS_PER_USER:
                del ids[:-MAX_IDS_PER_USER]

    def has_user(self, address: str) -> bool:
        return address in self._seen

    def seed(self, address: str, uids: list[str]) -> None:
        """Initialise un utilisateur sans notifier (premier démarrage)."""
        self._seen[address] = uids[-MAX_IDS_PER_USER:]
