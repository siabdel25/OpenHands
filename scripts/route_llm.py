#!/usr/bin/env python3
"""Route une tâche vers le profil LLM OpenHands le plus adapté, puis lance la conversation.

Règles (dans l'ordre, la première qui matche gagne) :
  1. Confidentialité : mots-clés sensibles -> gemma_local (offline, Ollama, aucune donnée envoyée à OpenRouter)
  2. Complexité      : mots-clés architecture/refactor/design -> claude (raisonnement)
  3. Défaut          : deepseek (le moins cher, correct pour du code courant)

Si le profil actif échoue au démarrage de la conversation (429/timeout), bascule
sur le profil de secours suivant dans FALLBACK_CHAIN.

Usage:
    python scripts/route_llm.py --task "Refactorer le module auth" --repo my-org/my-repo
    python scripts/route_llm.py --task "..." --dry-run
"""

import argparse
import sys

import requests

DEFAULT_BASE_URL = "http://localhost:3000"

CONFIDENTIAL_KEYWORDS = [
    "confidentiel", "secret", "interne", "rgpd", "mot de passe", "password",
    "credential", "clé api", "api key", "privé", "prive", "client data",
    "données client", "pii",
]

COMPLEX_KEYWORDS = [
    "architecture", "refactor", "refactoring", "conception", "design",
    "migration", "audit", "sécurité", "securite", "review complet",
    "stratégie", "strategie",
]

# Profils réellement définis dans /.openhands/settings.json (GET /api/v1/settings/profiles).
CONFIDENTIAL_PROFILE = "gemma4"
COMPLEX_PROFILE = "GML-5"
DEFAULT_PROFILE = "deepseek"

# Ordre de repli en cas d'échec (429, timeout, connexion) du profil choisi.
FALLBACK_CHAIN = ["deepseek", "GML-5", "gemma4", "openrouter_z-ai_glm-4.7-flash"]


def pick_profile(task: str) -> str:
    lowered = task.lower()
    if any(kw in lowered for kw in CONFIDENTIAL_KEYWORDS):
        return CONFIDENTIAL_PROFILE
    if any(kw in lowered for kw in COMPLEX_KEYWORDS):
        return COMPLEX_PROFILE
    return DEFAULT_PROFILE


def activate_profile(base_url: str, profile: str) -> None:
    resp = requests.post(f"{base_url}/api/v1/settings/profiles/{profile}/activate", timeout=15)
    resp.raise_for_status()


def start_conversation(base_url: str, task: str, repo: str | None, branch: str | None) -> dict:
    body = {
        "initial_message": {
            "content": [{"type": "text", "text": task}],
            "run": True,
        },
        "trigger": "gui",
    }
    if repo:
        body["selected_repository"] = repo
    if branch:
        body["selected_branch"] = branch
    resp = requests.post(f"{base_url}/api/v1/app-conversations", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def route(base_url: str, task: str, repo: str | None, branch: str | None, dry_run: bool) -> None:
    profile = pick_profile(task)
    chain = [profile] + [p for p in FALLBACK_CHAIN if p != profile]

    print(f"Profil choisi (règle): {profile}")
    if dry_run:
        print(f"[dry-run] ordre de repli: {chain}")
        return

    last_error = None
    for candidate in chain:
        try:
            activate_profile(base_url, candidate)
            result = start_conversation(base_url, task, repo, branch)
            print(f"Conversation démarrée avec le profil '{candidate}': {result.get('id', result)}")
            return
        except requests.RequestException as exc:
            print(f"Échec avec le profil '{candidate}': {exc}", file=sys.stderr)
            last_error = exc
            continue

    print("Tous les profils de repli ont échoué.", file=sys.stderr)
    raise SystemExit(1) from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, help="Description de la tâche à confier à l'agent")
    parser.add_argument("--repo", default=None, help="Dépôt sélectionné (org/repo)")
    parser.add_argument("--branch", default=None, help="Branche sélectionnée")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dry-run", action="store_true", help="Affiche le routage sans lancer de conversation")
    args = parser.parse_args()

    route(args.base_url, args.task, args.repo, args.branch, args.dry_run)


if __name__ == "__main__":
    main()
