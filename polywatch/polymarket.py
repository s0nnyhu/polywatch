"""Client de l'API de données Polymarket pour récupérer l'activité d'un utilisateur."""

from __future__ import annotations

import email.utils
import logging
import time
from dataclasses import dataclass, field

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
    condition_id: str = ""  # identifiant du marché (condition on-chain)
    asset: str = ""  # identifiant du token/outcome

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
            condition_id=str(item.get("conditionId", "")).lower(),
            asset=str(item.get("asset", "")).lower(),
        )


@dataclass
class MyMarkets:
    """Marchés dans lesquels *je* suis engagé (positions ouvertes).

    Sert à repérer si l'activité d'un trader surveillé porte sur un marché que
    je détiens déjà. On matche en priorité sur `condition_id`, avec repli sur
    le slug (au cas où l'un des deux manquerait dans la réponse de l'API).
    """

    condition_ids: set[str] = field(default_factory=set)
    slugs: set[str] = field(default_factory=set)

    def contains(self, activity: "Activity") -> bool:
        if activity.condition_id and activity.condition_id in self.condition_ids:
            return True
        if activity.slug and activity.slug in self.slugs:
            return True
        if activity.event_slug and activity.event_slug in self.slugs:
            return True
        return False

    def __bool__(self) -> bool:
        return bool(self.condition_ids or self.slugs)


def _parse_retry_after(value: str) -> float | None:
    """Interprète un header `Retry-After` en secondes d'attente.

    Le header peut être un nombre de secondes ("5") ou une date HTTP
    ("Wed, 21 Oct 2026 07:28:00 GMT"). Retourne None si illisible.
    """
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is not None:
        delay = parsed.timestamp() - time.time()
        return max(delay, 0.0)
    return None


class PolymarketClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polywatch/1.0"})

    def _handle_rate_limit(self, resp: requests.Response, context: str) -> bool:
        """Logue le délai d'attente conseillé si on se prend un 429.

        Retourne True si un rate limit (429) a été détecté.
        """
        if resp.status_code != 429:
            return False
        retry_after = _parse_retry_after(resp.headers.get("Retry-After", ""))
        if retry_after is not None:
            logger.warning(
                "Rate limit Polymarket (429) sur %s — réessayer dans %.0f s "
                "(prochain cycle dans poll_interval s).",
                context,
                retry_after,
            )
        else:
            logger.warning(
                "Rate limit Polymarket (429) sur %s — aucun header Retry-After, "
                "réessai au prochain cycle.",
                context,
            )
        return True

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
            if self._handle_rate_limit(resp, f"/public-search ({username})"):
                return None
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
            if self._handle_rate_limit(resp, f"/activity ({address})"):
                return []
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

    def fetch_positions(self, address: str, limit: int = 500) -> MyMarkets:
        """Récupère mes positions ouvertes et retourne les marchés concernés.

        Seules les positions avec une taille non nulle sont conservées (les
        positions soldées/rachetées sont ignorées). En cas d'erreur, retourne
        un `MyMarkets` vide (aucun marché en commun signalé).
        """
        params = {"user": address, "limit": limit, "sizeThreshold": 0.1}
        try:
            resp = self.session.get(
                f"{DATA_API}/positions", params=params, timeout=self.timeout
            )
            if self._handle_rate_limit(resp, f"/positions ({address})"):
                return MyMarkets()
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("Erreur récupération de mes positions (%s) : %s", address, exc)
            return MyMarkets()
        except ValueError as exc:
            logger.warning("Réponse JSON invalide pour mes positions (%s) : %s", address, exc)
            return MyMarkets()

        if not isinstance(data, list):
            return MyMarkets()

        markets = MyMarkets()
        for item in data:
            if not isinstance(item, dict):
                continue
            if not float(item.get("size", 0) or 0):
                continue
            condition_id = str(item.get("conditionId", "")).lower()
            slug = str(item.get("slug", ""))
            event_slug = str(item.get("eventSlug", ""))
            if condition_id:
                markets.condition_ids.add(condition_id)
            if slug:
                markets.slugs.add(slug)
            if event_slug:
                markets.slugs.add(event_slug)
        return markets
