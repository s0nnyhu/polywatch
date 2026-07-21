"""Client de l'API de données Polymarket pour récupérer l'activité d'un utilisateur."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Tous les types d'activité connus.
ALL_TYPES = ("TRADE", "SPLIT", "MERGE", "REDEEM", "REWARD", "CONVERSION")


@dataclass
class Activity:
    """Une activité on-chain d'un utilisateur Polymarket."""

    proxy_wallet: str
    timestamp: int
    tx_hash: str
    type: str  # TRADE | SPLIT | MERGE | REDEEM | REWARD | CONVERSION
    side: str  # BUY | SELL (seulement pour TRADE)
    size: float
    usdc_size: float
    price: float
    title: str
    outcome: str
    slug: str
    event_slug: str
    name: str
    pseudonym: str

    @property
    def username(self) -> str:
        """Username Polymarket, avec repli sur le pseudonyme puis l'adresse courte."""
        if self.name:
            return self.name
        if self.pseudonym:
            return self.pseudonym
        return f"{self.proxy_wallet[:6]}…{self.proxy_wallet[-4:]}"

    @property
    def uid(self) -> str:
        """Identifiant unique et stable d'une activité (dédoublonnage)."""
        return (
            f"{self.tx_hash}:{self.proxy_wallet}:{self.type}:"
            f"{self.side}:{self.outcome}:{self.timestamp}"
        )

    @property
    def market_url(self) -> str:
        slug = self.event_slug or self.slug
        return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    @classmethod
    def from_activity(cls, item: dict) -> "Activity":
        return cls(
            proxy_wallet=str(item.get("proxyWallet", "")).lower(),
            timestamp=int(item.get("timestamp", 0)),
            tx_hash=str(item.get("transactionHash", "")),
            type=str(item.get("type", "")).upper(),
            side=str(item.get("side", "")).upper(),
            size=float(item.get("size", 0) or 0),
            usdc_size=float(item.get("usdcSize", 0) or 0),
            price=float(item.get("price", 0) or 0),
            title=str(item.get("title", "")),
            outcome=str(item.get("outcome", "")),
            slug=str(item.get("slug", "")),
            event_slug=str(item.get("eventSlug", "")),
            name=str(item.get("name", "")),
            pseudonym=str(item.get("pseudonym", "")),
        )


class PolymarketClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polywatch/1.0"})

    def resolve_username(self, username: str) -> str | None:
        """Résout un username Polymarket en adresse de proxy wallet.

        Retourne l'adresse (0x...) ou None si introuvable.
        """
        params = {
            "q": username,
            "limit_per_type": 10,
            "search_profiles": "true",
        }
        try:
            resp = self.session.get(
                f"{GAMMA_API}/public-search", params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Erreur de résolution du username '%s' : %s", username, exc)
            return None

        profiles = data.get("profiles", []) if isinstance(data, dict) else []

        # On exige une correspondance EXACTE du nom (insensible à la casse) pour
        # éviter qu'un username erroné ne tombe sur un profil au hasard (fuzzy match).
        for profile in profiles:
            if str(profile.get("name", "")).lower() == username.lower():
                wallet = profile.get("proxyWallet")
                if wallet:
                    return str(wallet).lower()
        return None

    def fetch_activities(
        self, address: str, types: tuple[str, ...] | None = None, limit: int = 100
    ) -> list[Activity]:
        """Récupère les dernières activités d'un utilisateur.

        `types` filtre les types à conserver (par défaut : tous).
        Retourne une liste triée du plus ancien au plus récent.
        """
        allowed = tuple(t.upper() for t in (types or ALL_TYPES))
        params = {
            "user": address,
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        try:
            resp = self.session.get(
                f"{DATA_API}/activity", params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("Erreur API Polymarket pour %s : %s", address, exc)
            return []
        except ValueError as exc:
            logger.warning("Réponse JSON invalide pour %s : %s", address, exc)
            return []

        if not isinstance(data, list):
            return []

        activities = [
            Activity.from_activity(item)
            for item in data
            if str(item.get("type", "")).upper() in allowed
        ]
        activities.sort(key=lambda a: a.timestamp)
        return activities
