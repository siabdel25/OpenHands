# Rapport d'installation et configuration — OpenHands v1.29
> Orchestrateur multi-agents IA pour développement web métier complexe  
> Date : 29 juin 2026 | Instance : http://localhost:3000

---

## 1. Architecture cible

```
┌─────────────────────────────────────────────────────────────────┐
│                    OPENROUTER (API Gateway)                     │
│         Agrégateur multi-modèles · Clé unique · sk-or-v1-...   │
└──────────────┬────────────────┬──────────────────┬─────────────┘
               │                │                  │
    ┌──────────▼──────┐ ┌───────▼───────┐ ┌───────▼──────────┐
    │  DeepSeek v3    │ │  Claude S4.5  │ │  Gemini Flash1.5 │
    │  Architecte     │ │  Chat / Cond. │ │  Dev / Multimod. │
    │  [llm] défaut   │ │  [llm.claude] │ │  [llm.gemini]    │
    └──────────┬──────┘ └───────┬───────┘ └───────┬──────────┘
               │                │                  │
┌──────────────▼────────────────▼──────────────────▼───────────┐
│                    OPENHANDS v1.29                             │
│   Orchestrateur · CodeActAgent · http://localhost:3000        │
│                                                               │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  Sandbox Agent-Server (Docker isolé par session)     │   │
│   │  ghcr.io/openhands/agent-server:1.29.0-python        │   │
│   │  · Terminal bash · Éditeur · Navigateur · Jupyter    │   │
│   └──────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
         │
┌────────▼─────────────────────┐
│  Gemma 4 local (Ollama)      │
│  [llm.gemma_local]           │
│  http://localhost:11434      │
│  Données sensibles / offline │
└──────────────────────────────┘
```

---

## 2. Rôles des modèles

| Modèle | Rôle | Quand l'utiliser |
|--------|------|-----------------|
| **DeepSeek v3** | Architecte / Analyse | Conception, refactoring, revue de code, raisonnement complexe |
| **Claude Sonnet 4.5** | Chat + Condenseur | Conversation longue, cohérence contexte 200k, résumé historique |
| **Gemini Flash 1.5** | Dev rapide + Multimodal | Génération de code, analyse de maquettes (images), tâches rapides |
| **Gemma 4 (local)** | Données sensibles | Code propriétaire, données personnelles, mode offline |

**Sub-agents activé** : OpenHands peut déléguer des sous-tâches en parallèle entre ces modèles selon la complexité de la tâche.

---

## 3. Procédure d'installation complète

### 3.1 Prérequis

- Docker + Docker Compose installés
- Compte OpenRouter avec clé API (`sk-or-v1-...`)
- Port 3000 libre (ou adapter `docker-compose.yml`)

### 3.2 Clone du dépôt

```bash
git clone https://github.com/All-Hands-AI/OpenHands.git /home/django/Depot/www/OpenHandHarnai --depth=1
cd /home/django/Depot/www/OpenHandHarnai
```

### 3.3 Configuration `config.toml`

```bash
cp config.template.toml config.toml
```

Contenu final fonctionnel (`config.toml`) :

```toml
[core]
workspace_base = "./workspace"
default_agent = "CodeActAgent"
max_iterations = 500
runtime = "docker"

[llm]
model = "openrouter/deepseek/deepseek-chat-v3-0324"
api_key = "VOTRE_CLE_OPENROUTER"
temperature = 0.0
max_input_tokens = 64000
max_output_tokens = 8192

[llm.claude]
model = "openrouter/anthropic/claude-sonnet-4-5"
api_key = "VOTRE_CLE_OPENROUTER"
temperature = 0.5
max_input_tokens = 200000
max_output_tokens = 8192

[llm.gemini]
model = "openrouter/google/gemini-flash-1.5"
api_key = "VOTRE_CLE_OPENROUTER"
temperature = 0.0
max_input_tokens = 128000
max_output_tokens = 8192

[llm.gemma_local]
model = "ollama/gemma3:27b"
api_key = "ollama"
base_url = "http://localhost:11434"
temperature = 0.0
max_input_tokens = 8192
max_output_tokens = 4096

[agent]
enable_browsing = true
enable_editor = true
enable_jupyter = true
enable_cmd = true
enable_think = true
enable_finish = true
enable_history_truncation = true

[sandbox]
timeout = 120
user_id = 1000

[condenser]
type = "llm"
llm_config = "claude"
keep_first = 1
max_size = 100

[security]
confirmation_mode = false
```

> **Règle critique** : Ne jamais mettre `base_url` avec un modèle préfixé `openrouter/`.  
> LiteLLM traite alors l'URL comme un proxy interne → erreur 401 `LiteLLM_VerificationTokenTable`.  
> `base_url` uniquement pour les providers locaux (Ollama).

### 3.4 Conflit de port

Port 3000 occupé par une autre app (`as_cmdb_inventory-frontend`) → déplacée sur 3002 :

```yaml
# /home/django/Depot/www/envCMDBInventory/as_cmdb_inventory/docker-compose.yml
ports:
  - "3002:3000"  # était 3000:3000
```

OpenHands reste sur le port par défaut 3000 (important pour le callback MCP sandbox).

### 3.5 `docker-compose.yml` final

```yaml
services:
  openhands:
    build:
      context: ./
      dockerfile: ./containers/app/Dockerfile
    image: openhands:latest
    container_name: openhands-app-${DATE:-}
    environment:
      - AGENT_SERVER_IMAGE_REPOSITORY=${AGENT_SERVER_IMAGE_REPOSITORY:-ghcr.io/openhands/agent-server}
      - AGENT_SERVER_IMAGE_TAG=${AGENT_SERVER_IMAGE_TAG:-1.29.0-python}
      - WORKSPACE_MOUNT_PATH=${WORKSPACE_BASE:-$PWD/workspace}
    ports:
      - "3000:3000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ~/.openhands:/.openhands
      - ${WORKSPACE_BASE:-$PWD/workspace}:/opt/workspace_base
    pull_policy: if_not_present
    stdin_open: true
    tty: true
```

> `pull_policy: if_not_present` remplace `pull_policy: build` pour éviter un rebuild réseau inutile.

### 3.6 Premier démarrage (build ~15 min)

```bash
docker compose up -d
docker compose logs -f  # suivre la progression
```

Étapes du build :
1. Pull images Python 3.13 + Node 25 (~100 MB)
2. Installation dépendances système (apt)
3. `npm ci` frontend React
4. `poetry install` backend Python (~200 secondes)
5. Assemblage image finale (20 étapes Dockerfile)

### 3.7 Configuration UI (prioritaire sur config.toml)

**Important** : `~/.openhands/settings.json` **écrase** `config.toml` pour les settings LLM.  
Après le premier démarrage, configurer via l'UI : **http://localhost:3000/settings**

| Champ | Valeur |
|-------|--------|
| Model | `openrouter/deepseek/deepseek-chat-v3-0324` |
| API Key | `sk-or-v1-...` (nouvelle clé) |
| Base URL | *(vide)* |
| Sub-agents | ✓ activé |
| Enable Switch LLM Tool | ✓ activé |

---

## 4. Problèmes rencontrés et solutions

### P1 — Port 3000 conflit
**Symptôme** : `Bind for 0.0.0.0:3000 failed: port is already allocated`  
**Cause** : `as_cmdb_inventory-frontend-1` occupait le port 3000  
**Fix** : Déplacer l'autre app sur 3002 (moins de changements que de modifier OpenHands)

### P2 — 403 Forbidden sur `/mcp/mcp`
**Symptôme** : `Client error '403 Forbidden' for url 'http://host.docker.internal:3000/mcp/mcp'`  
**Cause** : Les sandboxes agents-server rappellent l'app principale via `host.docker.internal:3000`. Avec OpenHands sur 3001 (temporaire), ils touchaient le mauvais service.  
**Fix** : OpenHands repositionné sur port 3000 (défaut hardcodé dans le code, `SANDBOX_HOST_PORT` env var existe mais non implémentée — seule `SANDBOX_HOST_PORT` legacy dans `config.py` fonctionne)

### P3 — 401 LiteLLM proxy (`LiteLLM_VerificationTokenTable`)
**Symptôme** : `Litellm_proxyException - Authentication Error, Invalid proxy server token passed`  
**Cause 1** : `base_url = "https://openrouter.ai/api/v1"` combiné avec préfixe `openrouter/` → LiteLLM traite l'URL comme un proxy LiteLLM interne  
**Cause 2** : `settings.json` stockait l'ancien modèle `openhands/claude-opus-4-6` avec `base_url = "https://llm-proxy.app.all-hands.dev"` (proxy cloud OpenHands, incompatible self-hosted)  
**Cause 3** : Clé API révoquée (partagée en clair dans la conversation)  
**Fix** :
- Supprimer `base_url` de `config.toml` pour tous les modèles `openrouter/`
- Mettre à jour `settings.json` via l'UI Settings (a priorité sur `config.toml`)
- Régénérer la clé OpenRouter

### P4 — Anciens sandboxes avec ancienne clé
**Symptôme** : Erreur 401 persistante même après changement de clé  
**Cause** : Les conteneurs `oh-agent-server-*` tournaient encore avec l'ancienne clé en mémoire  
**Fix** :
```bash
docker ps -a | grep oh-agent-server | awk '{print $1}' | xargs docker rm -f
```

---

## 5. Configuration multi-modèles opérationnelle

### Dans l'UI — Switch LLM en cours de conversation

Avec `enable_switch_llm_tool = true`, l'agent peut changer de modèle automatiquement selon la tâche. Vous pouvez aussi le demander explicitement :

```
"Pour cette tâche d'architecture, utilise DeepSeek v3"
"Bascule sur Gemini pour analyser cette maquette"
"Utilise Claude pour ce long refactoring"
```

### Profils LLM (Settings > LLM Profiles)

Créez des profils nommés réutilisables :
- **`architecte`** → DeepSeek v3, temperature 0.0
- **`dev`** → Gemini Flash 1.5, temperature 0.2
- **`chat`** → Claude Sonnet, temperature 0.5
- **`local`** → Gemma 4 (Ollama), offline

### Sub-agents activé

Avec `enable_sub_agents = true`, le CodeActAgent peut spawner des sous-agents parallèles :
- Agent A : écrit les tests
- Agent B : implémente le code
- Agent C : génère la documentation

---

## 6. Commandes de maintenance

```bash
# Statut
docker compose ps

# Logs live
docker compose logs -f

# Nettoyer les sandboxes orphelins
docker ps -a | grep oh-agent-server | awk '{print $1}' | xargs docker rm -f

# Redémarrer après modif settings.json ou config.toml
docker compose down && docker compose up -d

# Vérifier la clé OpenRouter
curl -s https://openrouter.ai/api/v1/auth/key \
  -H "Authorization: Bearer VOTRE_CLE" | python3 -m json.tool

# Test appel LLM direct
curl -s https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer VOTRE_CLE" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek/deepseek-chat-v3-0324","messages":[{"role":"user","content":"test"}],"max_tokens":10}' \
  | python3 -m json.tool | grep content
```

---

## 7. Ports utilisés sur la machine hôte

| Port | Service |
|------|---------|
| 3000 | OpenHands (orchestrateur) |
| 3002 | as_cmdb_inventory frontend |
| 8300 | as_cmdb_inventory backend |
| 5433 | PostgreSQL (CMDB) |
| 6380 | Redis (CMDB) |
| 11434 | Ollama (Gemma local) |
| 3xxxx | Sandboxes agents-server (ports dynamiques) |

---

*Rapport généré le 29/06/2026 — OpenHands v1.29.0 · DeepSeek v3 · Claude Sonnet 4.5 · Gemini Flash 1.5 · Gemma 4 local*
