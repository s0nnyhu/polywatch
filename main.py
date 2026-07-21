#!/usr/bin/env python3
"""Polywatch — surveille des traders Polymarket et notifie les achats/ventes sur Telegram.

Exemples :
    python main.py --users users.json
    python main.py --address 0x9d84ce0306f8551e02efef1680475fc0f1dc1344
    python main.py --users users.json --once      # un seul cycle (test)
    python main.py --test                          # envoie un message de test Telegram
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from polywatch.config import Config
from polywatch.monitor import Monitor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Surveillance de traders Polymarket")
    parser.add_argument(
        "--users", "-u", help="Fichier JSON contenant la liste des utilisateurs"
    )
    parser.add_argument(
        "--address",
        "-a",
        action="append",
        default=[],
        help="Adresse à surveiller (peut être répété)",
    )
    parser.add_argument("--env", help="Chemin vers un fichier .env spécifique")
    parser.add_argument(
        "--once", action="store_true", help="Exécute un seul cycle puis s'arrête"
    )
    parser.add_argument(
        "--test", action="store_true", help="Envoie un message de test sur Telegram"
    )
    parser.add_argument(
        "--discover-chat-ids",
        action="store_true",
        help="Découvre les chat_id des bots (via getUpdates) et les écrit dans le .env",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Logs détaillés (DEBUG)"
    )
    return parser.parse_args()


def _set_env_var(env_path: Path, name: str, value: str) -> None:
    """Crée ou met à jour une variable dans le fichier .env."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    prefix = f"{name}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def discover_chat_ids(users_file: str | None, env_file: str | None) -> int:
    """Découvre les chat_id de chaque bot et les enregistre dans le .env."""
    from polywatch.telegram import discover_chats, get_bot_username
    from polywatch.config import _expand_env

    env_path = Path(env_file) if env_file else Path(".env")
    load_dotenv(env_path)

    path = Path(users_file) if users_file else Path("users.json")
    if not path.exists():
        print(f"Fichier utilisateurs introuvable : {path}", file=sys.stderr)
        return 1

    entries = json.loads(path.read_text(encoding="utf-8"))
    found_any = False
    missing: list[str] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("username") or entry.get("address", "?")
        token_ref = str(entry.get("bot_token", ""))
        chat_ref = str(entry.get("chat_id", ""))
        token = _expand_env(token_ref)
        if not token:
            continue

        bot = get_bot_username(token)
        chats = discover_chats(token)
        print(f"\n● {name}  (bot @{bot or '?'})")
        if not chats:
            print("   ⚠️  aucun chat détecté — écris un message au bot (ou ajoute-le")
            print("       comme admin d'un canal, puis poste un message), puis relance.")
            missing.append(name)
            continue

        for c in chats:
            print(f"   chat_id={c['id']}  type={c['type']}  ({c['title']})")

        # Écriture auto si le champ chat_id référence une variable $VAR encore vide.
        if chat_ref.startswith("$") and not _expand_env(chat_ref):
            var = chat_ref[1:]
            chosen = chats[0]["id"]
            _set_env_var(env_path, var, str(chosen))
            print(f"   ✅ {var}={chosen} écrit dans {env_path}")
            found_any = True

    print()
    if found_any:
        print("Chat IDs enregistrés. Tu peux lancer la surveillance.")
    if missing:
        print("En attente d'un premier message pour : " + ", ".join(missing))
    return 0


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.discover_chat_ids:
        return discover_chat_ids(args.users, args.env)

    if args.test:
        from polywatch.telegram import TelegramNotifier

        load_dotenv(args.env) if args.env else load_dotenv()
        notifier = TelegramNotifier(
            os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
        )
        ok = notifier.send("✅ <b>Polywatch</b> est bien connecté à Telegram.")
        print("Message de test envoyé." if ok else "Échec de l'envoi du message.")
        return 0 if ok else 1

    try:
        config = Config.load(
            users_file=args.users, users_inline=args.address, env_file=args.env
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 1

    monitor = Monitor(config)

    if args.once:
        sent = monitor.run_once()
        print(f"Cycle terminé. {sent} notification(s) envoyée(s).")
        return 0

    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\nArrêt demandé. Au revoir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
