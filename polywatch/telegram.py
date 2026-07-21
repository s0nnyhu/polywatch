"""Envoi de notifications Telegram."""

from __future__ import annotations

import html
import logging

import requests

from .polymarket import Activity

logger = logging.getLogger(__name__)

# Présentation par type d'activité : emoji + libellé.
TYPE_STYLE = {
    "BUY": ("🟢", "ACHAT"),
    "SELL": ("🔴", "VENTE"),
    "SPLIT": ("🧩", "SPLIT (création de parts)"),
    "MERGE": ("🔗", "MERGE (fusion de parts)"),
    "REDEEM": ("💰", "REDEEM (encaissement)"),
    "REWARD": ("🎁", "REWARD (récompense liquidité)"),
    "CONVERSION": ("🔄", "CONVERSION"),
}


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 15) -> None:
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self.session = requests.Session()

    def send(self, text: str) -> bool:
        try:
            resp = self.session.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error("Échec de l'envoi Telegram : %s", exc)
            return False

    def notify_activity(self, activity: Activity) -> bool:
        return self.send(format_activity(activity))

    def check(self) -> bool:
        """Vérifie que le bot et le chat_id sont valides."""
        try:
            resp = self.session.get(f"{self.api_url}/getMe", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json().get("ok", False)
        except requests.RequestException as exc:
            logger.error("Impossible de joindre le bot Telegram : %s", exc)
            return False


def get_bot_username(bot_token: str, timeout: int = 15) -> str:
    """Retourne le @username du bot (ou une chaîne vide en cas d'échec)."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe", timeout=timeout
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("username", "")
    except requests.RequestException:
        return ""


def discover_chats(bot_token: str, timeout: int = 15) -> list[dict]:
    """Découvre les chats connus d'un bot via getUpdates.

    Retourne une liste de dicts {id, type, title} sans doublons. Nécessite que
    quelqu'un ait déjà écrit au bot (DM) ou que le bot soit admin d'un canal actif.
    """
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"allowed_updates": '["message","channel_post","my_chat_member"]'},
            timeout=timeout,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except (requests.RequestException, ValueError) as exc:
        logger.error("Échec de getUpdates : %s", exc)
        return []

    seen: set = set()
    chats: list[dict] = []
    for upd in updates:
        for field in ("message", "channel_post", "edited_message", "my_chat_member"):
            obj = upd.get(field)
            if not obj:
                continue
            chat = obj.get("chat", {})
            cid = chat.get("id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            title = chat.get("title") or chat.get("username") or chat.get("first_name", "")
            chats.append({"id": cid, "type": chat.get("type", ""), "title": title})
    return chats


def format_activity(activity: Activity) -> str:
    # Pour un TRADE, on distingue BUY/SELL ; sinon on prend le type brut.
    key = activity.side if activity.type == "TRADE" else activity.type
    emoji, action = TYPE_STYLE.get(key, ("📌", activity.type or "ACTIVITÉ"))

    label = html.escape(activity.username)
    lines = [f"{emoji} <b>{action}</b> — <b>{label}</b>", ""]

    if activity.title:
        lines.append(f"📊 {html.escape(activity.title)}")

    if activity.type == "TRADE":
        lines.append(f"🎯 Position : <b>{html.escape(activity.outcome or '?')}</b>")
        lines.append(f"💵 Montant : <b>${activity.usdc_size:,.2f}</b>")
        lines.append(f"📈 Prix : {activity.price:.3f}  ·  Taille : {activity.size:,.2f}")
    else:
        lines.append(f"💵 Montant : <b>${activity.usdc_size:,.2f}</b>")

    if activity.market_url != "https://polymarket.com":
        lines.append(f'🔗 <a href="{activity.market_url}">Voir le marché</a>')

    return "\n".join(lines)
