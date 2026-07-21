"""Chargement de la configuration et de la liste des utilisateurs à surveiller."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .polymarket import ALL_TYPES


ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_address(value: str) -> bool:
    return bool(ADDRESS_RE.match(value.strip()))


@dataclass
class User:
    """Un trader Polymarket à surveiller.

    `identifier` : soit une adresse de wallet (0x...), soit un username Polymarket.
    Les usernames sont résolus automatiquement en adresse au démarrage.
    `threshold` : seuil de prix pour les ACHATS (BUY). Par défaut 1.0 → tous les
    achats passent. Si fixé à 0.92, seuls les achats au prix <= 0.92 notifient.
    """

    identifier: str
    threshold: float = 1.0
    address: str = ""
    bot_token: str = ""
    chat_id: str = ""

    def __post_init__(self) -> None:
        self.identifier = self.identifier.strip()
        if is_address(self.identifier):
            self.address = self.identifier.lower()

    @property
    def resolved(self) -> bool:
        return bool(self.address)

    @property
    def display(self) -> str:
        if not is_address(self.identifier):
            return self.identifier
        addr = self.address or self.identifier
        return f"{addr[:6]}…{addr[-4:]}"


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    users: list[User] = field(default_factory=list)
    poll_interval: int = 30
    min_usdc: float = 0.0
    state_file: str = "state.json"
    lookback_seconds: int = 3600
    activity_types: tuple[str, ...] = ALL_TYPES

    @classmethod
    def load(
        cls,
        users_file: str | None = None,
        users_inline: list[str] | None = None,
        env_file: str | None = None,
    ) -> "Config":
        """Charge la config depuis le .env et la liste d'utilisateurs.

        La liste des utilisateurs peut venir :
        - d'un fichier JSON (--users), ou
        - d'adresses passées en ligne de commande (--address), ou
        - de la variable d'environnement POLYWATCH_USERS (adresses séparées par des virgules).
        """
        load_dotenv(env_file) if env_file else load_dotenv()

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        users = _resolve_users(users_file, users_inline)
        if not users:
            raise ValueError(
                "Aucun utilisateur à surveiller. Fournis --users <fichier.json>, "
                "--address <adresse> ou la variable POLYWATCH_USERS."
            )

        # Le bot global n'est requis que si un utilisateur n'a pas son propre bot/canal.
        needs_global = any(not (u.bot_token and u.chat_id) for u in users)
        if needs_global and (not token or not chat_id):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID doivent être définis dans le .env "
                "(ou chaque utilisateur doit avoir son propre bot_token et chat_id)."
            )

        poll_interval = int(os.getenv("POLYWATCH_POLL_INTERVAL", "30"))
        min_usdc = float(os.getenv("POLYWATCH_MIN_USDC", "0"))
        state_file = os.getenv("POLYWATCH_STATE_FILE", "state.json")
        lookback = int(os.getenv("POLYWATCH_LOOKBACK_SECONDS", "3600"))
        activity_types = _resolve_activity_types(os.getenv("POLYWATCH_ACTIVITY_TYPES"))

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            users=users,
            poll_interval=poll_interval,
            min_usdc=min_usdc,
            state_file=state_file,
            lookback_seconds=lookback,
            activity_types=activity_types,
        )


def _expand_env(value: str) -> str:
    """Résout une valeur `$NOM_VARIABLE` depuis l'environnement.

    Permet de garder les tokens dans le .env plutôt qu'en clair dans users.json.
    Une valeur littérale (sans `$`) est renvoyée telle quelle.
    """
    value = (value or "").strip()
    if value.startswith("$"):
        return os.getenv(value[1:], "").strip()
    return value


def _resolve_activity_types(raw: str | None) -> tuple[str, ...]:
    """Détermine les types d'activité à surveiller (par défaut : tous)."""
    if not raw or raw.strip().lower() in ("all", "*", ""):
        return ALL_TYPES
    requested = [t.strip().upper() for t in raw.split(",") if t.strip()]
    valid = tuple(t for t in requested if t in ALL_TYPES)
    return valid or ALL_TYPES


def _resolve_users(
    users_file: str | None, users_inline: list[str] | None
) -> list[User]:
    users: list[User] = []
    seen: set[str] = set()

    def add(
        identifier: str,
        threshold: float = 1.0,
        bot_token: str = "",
        chat_id: str = "",
    ) -> None:
        ident = identifier.strip()
        key = ident.lower()
        if ident and key not in seen:
            seen.add(key)
            users.append(
                User(
                    identifier=ident,
                    threshold=threshold,
                    bot_token=_expand_env(bot_token),
                    chat_id=_expand_env(chat_id),
                )
            )

    if users_file:
        path = Path(users_file)
        if not path.exists():
            raise FileNotFoundError(f"Fichier utilisateurs introuvable : {users_file}")
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            if isinstance(entry, str):
                add(entry)
            elif isinstance(entry, dict):
                # accepte "address" ou "username" comme identifiant
                ident = entry.get("address") or entry.get("username", "")
                add(
                    ident,
                    float(entry.get("threshold", 1.0)),
                    str(entry.get("bot_token", "")),
                    str(entry.get("chat_id", "")),
                )

    if users_inline:
        for ident in users_inline:
            add(ident)

    env_users = os.getenv("POLYWATCH_USERS", "")
    if env_users:
        for ident in env_users.split(","):
            add(ident)

    return users
