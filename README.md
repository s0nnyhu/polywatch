# Polywatch

Surveillance de traders **Polymarket** avec alertes **Telegram** en temps quasi réel sur leurs **achats (BUY)** et **ventes (SELL)**.

L'application interroge périodiquement l'API de données de Polymarket pour chaque adresse surveillée et envoie une notification Telegram dès qu'un nouveau trade apparaît.

## Fonctionnement

- Poll de `https://data-api.polymarket.com/activity` pour chaque utilisateur.
- **Tous les types d'activité** sont surveillés par défaut :
  `TRADE` (BUY/SELL), `SPLIT`, `MERGE`, `REDEEM`, `REWARD`, `CONVERSION`.
- Dédoublonnage via un fichier d'état (`state.json`) : chaque activité n'est notifiée qu'une seule fois.
- Au **premier démarrage**, l'historique existant est marqué comme « déjà vu » (pas de spam) ; seules les nouvelles opérations sont notifiées ensuite.

### Types d'activité

| Type | Description |
| --- | --- |
| `TRADE` | Achat (BUY) ou vente (SELL) de parts sur un marché |
| `SPLIT` | Dépôt d'USDC converti en jeux de parts (Yes + No) |
| `MERGE` | Fusion de parts Yes+No pour récupérer de l'USDC |
| `REDEEM` | Encaissement des gains sur un marché résolu |
| `REWARD` | Récompense de fourniture de liquidité |
| `CONVERSION` | Conversion de parts (marchés à choix multiples) |

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Les identifiants sont lus depuis le fichier `.env` :

```env
TELEGRAM_BOT_TOKEN=xxxxx
TELEGRAM_CHAT_ID=xxxxx
```

Options facultatives (dans `.env`) :

| Variable | Défaut | Description |
| --- | --- | --- |
| `POLYWATCH_POLL_INTERVAL` | `30` | Intervalle entre deux vérifications (secondes) |
| `POLYWATCH_MIN_USDC` | `0` | Montant minimum en USDC pour notifier |
| `POLYWATCH_LOOKBACK_SECONDS` | `3600` | Ancienneté max d'un trade pour être notifié |
| `POLYWATCH_STATE_FILE` | `state.json` | Fichier de persistance |
| `POLYWATCH_USERS` | — | Liste d'adresses séparées par des virgules |
| `POLYWATCH_ACTIVITY_TYPES` | `all` | Types à surveiller, ex. `TRADE,SPLIT,MERGE` (ou `all`) |

## Liste des utilisateurs

On peut désigner un trader par son **adresse de wallet** (`0x...`) **ou** par son
**username Polymarket** : les usernames sont **résolus automatiquement** en adresse
au démarrage (via l'API de recherche de profils Polymarket). Le nom affiché dans les
notifications est toujours le username Polymarket du trader.

Fournie **en paramètre**, de trois façons possibles :

1. **Fichier JSON** (recommandé) — voir `users.example.json`. Chaque entrée est soit
   une simple adresse/username, soit un objet avec un **seuil de prix** (`threshold`) :

```json
[
  "0x9d84ce0306f8551e02efef1680475fc0f1dc1344",
  "weatherstappen",
  { "address": "0x0000000000000000000000000000000000000000", "threshold": 0.92 },
  { "username": "ImJustKen", "threshold": 0.5 }
]
```

- **`threshold`** (optionnel, défaut `1`) : s'applique **aux achats (BUY)**. Avec
  `0.92`, seuls les achats au **prix ≤ 0.92** déclenchent une alerte.
- **`bot_token`** / **`chat_id`** (optionnels) : bot et/ou canal Telegram **dédiés**
  à cet utilisateur. Si absents, on utilise le bot global du `.env`.

### Bot / canal Telegram par utilisateur

Chaque utilisateur peut recevoir ses alertes sur **son propre bot et/ou canal** :

```json
[
  { "username": "ImJustKen", "bot_token": "$KEN_BOT_TOKEN", "chat_id": "$KEN_CHAT_ID" },
  { "username": "weatherstappen", "bot_token": "$WS_BOT_TOKEN", "chat_id": "$WS_CHAT_ID" }
]
```

Pour ne pas écrire les tokens en clair, préfixe la valeur par `$` : elle sera lue
depuis le `.env`. Exemple de `.env` :

```env
KEN_BOT_TOKEN=123456:AA...
KEN_CHAT_ID=844714003
WS_BOT_TOKEN=789012:BB...
WS_CHAT_ID=-1001234567890
```

> `chat_id` accepte un id numérique, un `@nom_de_canal` public, ou un id de canal
> privé (`-100...`). Le bot doit être **admin** du canal pour y publier.
>
> Le bot **global** (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) devient facultatif
> si **tous** les utilisateurs ont leur propre bot et canal.

2. **En ligne de commande** (adresse ou username) :

```bash
python main.py --address 0x9d84ce0306f8551e02efef1680475fc0f1dc1344
python main.py --address weatherstappen
```

3. **Variable d'environnement** `POLYWATCH_USERS` (adresses/usernames séparés par des virgules).

## Utilisation

```bash
# Tester la connexion Telegram
python main.py --test

# Un seul cycle (utile pour tester)
python main.py --users users.json --once

# Surveillance continue
python main.py --users users.json

# Plusieurs adresses en direct
python main.py -a 0xADDR1 -a 0xADDR2
```

## Notes

- L'adresse à surveiller est le **proxy wallet** Polymarket (celui visible dans l'URL du profil `polymarket.com/profile/0x...`).
- Pour tourner en continu sur un serveur, utilise `systemd`, `pm2`, `screen`/`tmux` ou un conteneur.
