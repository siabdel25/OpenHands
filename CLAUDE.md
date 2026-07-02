# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication

Réponses courtes. Pas de résumé en fin de message. Pas de narration des étapes intermédiaires. Economie de tokens.

## Instance locale

- URL : http://localhost:3000
- CMDB frontend (autre app) : http://localhost:3002
- Config LLM réelle : `/.openhands/settings.json` **dans le conteneur** (profils, gérés via UI ou API) — voir section Configuration LLM
- Workspace agent : `./workspace/`

## Stack

Python monorepo (Poetry/uv). Backend FastAPI + Uvicorn. Frontend React (`frontend/`). SDK séparé (`openhands-sdk`, `openhands-agent-server`) installé via Poetry depuis PyPI.

```
openhands/
  app_server/     ← FastAPI app, routes, MCP, sandbox orchestration
  runtime/        ← exécution code dans sandbox
  agent/          ← logique agent (CodeActAgent)
  llm/            ← abstraction LLM (LiteLLM)
```

## Docker

```bash
docker compose up -d          # démarrer
docker compose down           # arrêter
docker compose logs -f        # logs live
docker compose ps             # statut
```

Pas de rebuild nécessaire pour les changements de `docker-compose.yml` — juste `down && up -d`.

> **Fuite de sandboxes** : chaque conversation crée un conteneur `oh-agent-server-*` qui ne s'arrête jamais. Leur accumulation sature la RAM (UI en "connection failed"). Nettoyage : `docker stop` des sandboxes obsolètes (`docker ps | grep agent-server`).

## Variables d'environnement critiques

| Var | Effet |
|-----|-------|
| `SANDBOX_HOST_PORT` | Port hôte que le sandbox utilise pour callback MCP (défaut 3000) |
| `SANDBOX_CONTAINER_URL_PATTERN` | URL pattern pour accéder aux ports exposés du sandbox |
| `RUNTIME` | `docker` (défaut), `local`, `process`, `remote` |

> `OH_SANDBOX_HOST_PORT` est documenté dans le code mais **non implémenté** — utiliser `SANDBOX_HOST_PORT`.

## Piège LiteLLM / OpenRouter

Ne pas mettre `base_url` avec le préfixe `openrouter/` — LiteLLM traite alors l'URL comme un proxy LiteLLM interne et rejette la clé en 401 (`Litellm_proxyException / LiteLLM_VerificationTokenTable`). Laisser `base_url` uniquement pour les providers locaux (Ollama).

## Configuration LLM

> ⚠️ `config.toml` (racine du repo) **n'est pas monté dans le conteneur et n'est jamais lu**. Ses sections `[llm.*]` sont de la documentation d'intention, pas de la config active.

La config réelle vit dans `/.openhands/settings.json` (dans le conteneur), sous forme de **profils** nommés gérés via l'UI (Settings → LLM) ou l'API REST :

- `GET /api/v1/settings/profiles` — lister profils + profil actif
- `POST /api/v1/settings/profiles/{name}` — créer/mettre à jour
- `POST /api/v1/settings/profiles/{name}/activate` — activer

OpenHands utilise LiteLLM en interne — model IDs au format LiteLLM (`openrouter/provider/model`, `openai/<modele-ollama>` pour Ollama via `base_url http://host.docker.internal:11434/v1/`).

Orchestration : `scripts/route_llm.py --task "..." [--wait]` route vers le bon profil (confidentiel → `gemma4` local, complexe → `claude`, défaut → `deepseek`) et, avec `--wait`, imprime la réponse finale de l'agent. Détails : `RAPPORT_ORCHESTRATION_LLM.md`.

## Pièges modèles locaux (Ollama)

- **Troncature de contexte** : le prompt système OpenHands fait ~18-26k tokens ; le défaut Ollama (`n_ctx=4096`) le tronque → le modèle "perd" le harnais (répond en chatbot ou imite les tool-calls en JSON texte). Fix : `Environment="OLLAMA_CONTEXT_LENGTH=24576"` dans l'override systemd d'Ollama. Le champ `litellm_extra_body.options.num_ctx` est **ignoré** par l'endpoint OpenAI-compatible `/v1`.
- **CUDA cassé (erreur 999)** alors que `nvidia-smi` fonctionne : module `nvidia_uvm` corrompu. Fix : `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm && sudo systemctl restart ollama`.
- **Tool-calling** : `gemma4:e4b` a un vrai function-calling natif via Ollama ; `qwen2.5-coder:14b` non (JSON en texte brut) — ne pas l'utiliser comme agent.
- Les vraies erreurs LLM apparaissent dans les logs des sandboxes `oh-agent-server-*`, pas dans ceux d'`openhands-app-`.

## MCP

Le serveur MCP interne est monté sur `/mcp/mcp`. Les sandboxes agents-server s'y connectent via `host.docker.internal:{SANDBOX_HOST_PORT}`. Tout conflit de port sur l'hôte casse ce callback.

## Makefile

```bash
make build        # build complet
make lint         # ruff + mypy
make test         # pytest
make test-single TEST=tests/path/test_file.py::test_name
```
