#!/usr/bin/env python3
"""Route une tâche vers le profil LLM OpenHands le plus adapté, puis lance la conversation.

Règles (dans l'ordre, la première qui matche gagne) :
  1. Confidentialité : mots-clés sensibles -> gemma4 (offline, Ollama, aucune donnée envoyée à OpenRouter)
  2. Complexité      : mots-clés architecture/refactor/design -> claude (raisonnement)
  3. Défaut          : deepseek (le moins cher, correct pour du code courant)

Si le profil actif échoue au démarrage de la conversation (429/timeout), bascule
sur le profil de secours suivant dans FALLBACK_CHAIN.

Avec --wait, suit la conversation jusqu'à la fin et imprime la réponse finale de
l'agent (mode maître/esclave : un orchestrateur délègue et moissonne le résultat).

Usage:
    python scripts/route_llm.py --task "Refactorer le module auth" --repo my-org/my-repo
    python scripts/route_llm.py --task "Rédige la doc du module X" --wait
    python scripts/route_llm.py --task "..." --dry-run
"""

import argparse
import sys
import time

import requests

DEFAULT_BASE_URL = "http://localhost:3000"
JSON_HEADERS = {"Accept": "application/json"}

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
COMPLEX_PROFILE = "claude"
DEFAULT_PROFILE = "deepseek"

# Ordre de repli en cas d'échec (429, timeout, connexion) du profil choisi.
FALLBACK_CHAIN = ["deepseek", "GML-5", "claude", "gemini", "gemma4", "openrouter_z-ai_glm-4.7-flash"]

# Profils autorisés pour les tâches confidentielles : locaux uniquement.
# Une tâche confidentielle ne doit JAMAIS être rejouée sur un profil cloud.
LOCAL_PROFILES = {"gemma4", "qwen_local"}

START_TASK_TERMINAL = {"READY", "ERROR"}
EXECUTION_TERMINAL = {"finished", "error", "stuck"}


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


def start_conversation(base_url: str, task: str, repo: str | None, branch: str | None) -> str:
    """Démarre une conversation et retourne l'id de la tâche de démarrage.

    Attention : l'id retourné par POST /app-conversations est un id de *start-task*,
    pas l'id final de conversation (voir wait_for_start_task).
    """
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
    return resp.json()["id"]


def wait_for_start_task(base_url: str, task_id: str, timeout: float = 300) -> str:
    """Attend que la start-task aboutisse et retourne l'app_conversation_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{base_url}/api/v1/app-conversations/start-tasks/search",
            params={"limit": 20}, headers=JSON_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        match = next((t for t in resp.json()["items"] if t["id"] == task_id), None)
        if match and match["status"] in START_TASK_TERMINAL:
            if match["status"] == "ERROR":
                raise RuntimeError(f"Démarrage échoué: {match.get('detail')}")
            return match["app_conversation_id"]
        time.sleep(3)
    raise TimeoutError(f"start-task {task_id} toujours en cours après {timeout}s")


def get_conversation(base_url: str, conversation_id: str) -> dict | None:
    resp = requests.get(
        f"{base_url}/api/v1/app-conversations/search",
        params={"limit": 50}, headers=JSON_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    return next((c for c in resp.json()["items"] if c["id"] == conversation_id), None)


def fetch_agent_events(conversation: dict) -> list[dict]:
    """Récupère tous les events de la conversation via l'API de l'agent-server."""
    url = conversation["conversation_url"]
    headers = {**JSON_HEADERS, "X-Session-API-Key": conversation["session_api_key"]}
    events: list[dict] = []
    page_id = None
    while True:
        params = {"limit": 100}
        if page_id:
            params["page_id"] = page_id
        resp = requests.get(f"{url}/events/search", params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events.extend(data["items"])
        page_id = data.get("next_page_id")
        if not page_id:
            return events


def print_transcript(events: list[dict]) -> None:
    final_answer = None
    for ev in events:
        kind = ev.get("kind")
        if kind == "ActionEvent":
            action = ev.get("action", {})
            print(f"[action] {action.get('kind')}: {str(action)[:150]}")
        elif kind == "MessageEvent" and ev.get("source") == "agent":
            final_answer = "\n".join(
                c.get("text", "") for c in ev["llm_message"]["content"] if c.get("text")
            )
    if final_answer:
        print("\n=== Réponse finale de l'agent ===")
        print(final_answer)
    else:
        print("\n(aucune réponse texte de l'agent)", file=sys.stderr)


def wait_for_result(base_url: str, conversation_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conv = get_conversation(base_url, conversation_id)
        status = (conv or {}).get("execution_status")
        if status in EXECUTION_TERMINAL:
            cost = conv.get("metrics", {}).get("accumulated_cost")
            print(f"Exécution terminée: {status} | coût: ${cost}")
            print_transcript(fetch_agent_events(conv))
            if status != "finished":
                raise SystemExit(1)
            return
        time.sleep(10)
    raise TimeoutError(f"conversation {conversation_id} toujours en cours après {timeout}s")


def route(base_url: str, task: str, repo: str | None, branch: str | None,
          dry_run: bool, wait: bool, wait_timeout: float) -> None:
    profile = pick_profile(task)
    if profile == CONFIDENTIAL_PROFILE:
        # Tâche confidentielle : repli limité aux profils locaux, jamais le cloud.
        chain = [profile] + [p for p in FALLBACK_CHAIN if p in LOCAL_PROFILES and p != profile]
    else:
        chain = [profile] + [p for p in FALLBACK_CHAIN if p != profile]

    print(f"Profil choisi (règle): {profile}")
    if dry_run:
        print(f"[dry-run] ordre de repli: {chain}")
        return

    last_error: Exception | None = None
    for candidate in chain:
        try:
            activate_profile(base_url, candidate)
            task_id = start_conversation(base_url, task, repo, branch)
            conversation_id = wait_for_start_task(base_url, task_id)
            print(f"Conversation démarrée avec le profil '{candidate}': {conversation_id}")
            if wait:
                wait_for_result(base_url, conversation_id, wait_timeout)
            return
        except (requests.RequestException, RuntimeError, TimeoutError) as exc:
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
    parser.add_argument("--wait", action="store_true", help="Attend la fin et imprime la réponse finale de l'agent")
    parser.add_argument("--wait-timeout", type=float, default=900,
                        help="Durée max d'attente du résultat en secondes (défaut 900)")
    args = parser.parse_args()

    route(args.base_url, args.task, args.repo, args.branch, args.dry_run, args.wait, args.wait_timeout)


if __name__ == "__main__":
    main()
