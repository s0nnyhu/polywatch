"""Boucle principale de surveillance des traders Polymarket."""

from __future__ import annotations

import logging
import time

from .config import Config, User
from .polymarket import MyMarkets, PolymarketClient
from .state import State
from .telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = PolymarketClient()
        self.state = State(config.state_file)
        # Cache des notifiers Telegram, indexé par (bot_token, chat_id).
        self._notifiers: dict[tuple[str, str], TelegramNotifier] = {}
        # Cache de mes propres marchés (positions ouvertes), rafraîchi avec TTL.
        self._my_markets = MyMarkets()
        self._my_markets_at = 0.0
        self._resolve_usernames()

    def _refresh_my_markets(self) -> None:
        """Rafraîchit le cache de mes positions si le TTL est dépassé."""
        if not self.config.my_wallet:
            return
        now = time.time()
        if self._my_markets and now - self._my_markets_at < self.config.my_markets_ttl:
            return
        markets = self.client.fetch_positions(self.config.my_wallet)
        # On ne remplace le cache que si la requête a renvoyé quelque chose, pour
        # éviter d'effacer mes marchés sur une erreur réseau/rate limit ponctuelle.
        if markets:
            self._my_markets = markets
        self._my_markets_at = now

    def _notifier_for(self, user: User) -> TelegramNotifier:
        """Retourne le notifier propre à l'utilisateur, ou le bot global par défaut."""
        token = user.bot_token or self.config.telegram_bot_token
        chat_id = user.chat_id or self.config.telegram_chat_id
        key = (token, chat_id)
        if key not in self._notifiers:
            self._notifiers[key] = TelegramNotifier(token, chat_id)
        return self._notifiers[key]

    def _resolve_usernames(self) -> None:
        """Résout les usernames en adresses de wallet ; écarte ceux introuvables."""
        active = []
        for user in self.config.users:
            if not user.resolved:
                wallet = self.client.resolve_username(user.identifier)
                if wallet:
                    user.address = wallet
                    logger.info(
                        "Username '%s' résolu → %s", user.identifier, wallet
                    )
                else:
                    logger.warning(
                        "Username '%s' introuvable, il sera ignoré.", user.identifier
                    )
                    continue
            active.append(user)
        self.config.users = active

    def run_once(self) -> int:
        """Un cycle de vérification pour tous les utilisateurs.

        Retourne le nombre de notifications envoyées.
        """
        now = int(time.time())
        cutoff = now - self.config.lookback_seconds
        sent = 0

        self._refresh_my_markets()

        for user in self.config.users:
            activities = self.client.fetch_activities(
                user.address, types=self.config.activity_types
            )
            if not activities:
                continue

            # Premier démarrage : on marque l'historique sans notifier
            if not self.state.has_user(user.address):
                self.state.seed(user.address, [a.uid for a in activities])
                logger.info(
                    "Initialisation de %s (%d activités historiques ignorées)",
                    user.display,
                    len(activities),
                )
                continue

            for activity in activities:
                if self.state.is_known(user.address, activity.uid):
                    continue
                if activity.timestamp < cutoff:
                    # Trop ancien : on marque comme vu sans notifier
                    self.state.mark(user.address, activity.uid)
                    continue
                if activity.usdc_size < self.config.min_usdc:
                    self.state.mark(user.address, activity.uid)
                    continue
                # Seuil de prix sur les ACHATS : on ne notifie que si prix <= threshold
                if (
                    activity.type == "TRADE"
                    and activity.side == "BUY"
                    and activity.price > user.threshold
                ):
                    self.state.mark(user.address, activity.uid)
                    continue

                # On marque AVANT de notifier pour garantir la déduplication même
                # si l'envoi ou le log échoue (évite tout renvoi en boucle).
                self.state.mark(user.address, activity.uid)
                mine = self._my_markets.contains(activity)
                if self._notifier_for(user).notify_activity(activity, mine=mine):
                    sent += 1
                    logger.info(
                        "Notifié : %s %s $%.2f @ %.3f sur %s",
                        activity.username,
                        activity.side or activity.type,
                        activity.usdc_size,
                        activity.price,
                        activity.title or activity.type,
                    )

        self.state.save()
        return sent

    def run(self) -> None:
        logger.info(
            "Démarrage de la surveillance de %d utilisateur(s), types=%s, intervalle %ds",
            len(self.config.users),
            ",".join(self.config.activity_types),
            self.config.poll_interval,
        )
        # On instancie les notifiers puis on vérifie chaque bot unique une seule fois.
        for user in self.config.users:
            self._notifier_for(user)
        for notifier in self._notifiers.values():
            if not notifier.check():
                logger.warning(
                    "Un bot Telegram ne répond pas. Vérifie le token correspondant."
                )

        while True:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - on ne veut jamais crasher la boucle
                logger.exception("Erreur inattendue durant le cycle : %s", exc)
            time.sleep(self.config.poll_interval)
