# Rapport d'expérience — Orchestration multi-modèles (cloud + local) avec le harnais OpenHands

> Instance : http://localhost:3000 | Période : 01–02 juillet 2026 | Conteneur : `openhands-app-`

---

## 1. Résumé exécutif

Objectif : router automatiquement chaque tâche vers le modèle LLM le plus adapté (confidentialité, complexité, coût) parmi un panel mixte cloud (OpenRouter) + local (Ollama), sans intervention manuelle dans l'UI.

Résultat : un script d'orchestration (`scripts/route_llm.py`) route la tâche selon des règles simples, active le profil LLM correspondant via l'API OpenHands, démarre la conversation, et retombe sur un profil de secours en cas d'échec. Testé de bout en bout sur un vrai dépôt (`siabdel25/OpenHands`) : le pipeline clone le repo, exécute l'agent avec le modèle choisi (`deepseek`), et produit un commit de code fonctionnel pour ~$0.045.

Enseignement principal : la configuration LLM réelle de cette instance ne vit **pas** dans `config.toml` (fichier non monté dans le conteneur) mais dans `/.openhands/settings.json`, sous forme de *profils* nommés gérés par API REST. Toute tentative d'orchestration doit passer par cette API, pas par le fichier de config du dépôt.

---

## 2. Point de départ : diagnostic Gemma3 local

Symptôme initial : Gemma3 (27b, via Ollama) répondait avec son prompt système par défaut ("je suis Gemma de Google DeepMind") au lieu de suivre le harnais agent OpenHands — comme s'il "ignorait" les outils et instructions fournis.

Deux corrections apportées dans `config.toml` (fichier du dépôt) :

| Paramètre | Avant | Après | Raison |
|---|---|---|---|
| `base_url` | `http://localhost:11434` | `http://host.docker.internal:11434` | Depuis le conteneur, `localhost` pointe vers lui-même, pas vers l'hôte où tourne Ollama |
| `max_input_tokens` | `8192` | `32768` | Le system prompt OpenHands + définitions d'outils (bash, editor, browsing, jupyter...) pouvaient dépasser 8k tokens et être tronqués, faisant perdre au modèle les instructions du harnais |

Après `docker compose down && up -d`, le modèle a bien répondu dans le rôle attendu de l'agent.

**Correction ultérieure du diagnostic** (voir §3) : ces deux corrections dans `config.toml` n'étaient en réalité pas la cause du fix, puisque ce fichier n'est jamais lu par le conteneur. Le vrai correctif se trouvait ailleurs dans la stack (Ollama écoutant sur l'interface réseau du conteneur, DNS OpenRouter). `config.toml` reste néanmoins pertinent comme documentation d'intention pour un déploiement futur qui le monterait réellement.

---

## 3. Découverte architecturale : `config.toml` n'est jamais lu

En creusant l'API de création de conversation (`POST /api/v1/app-conversations`), constat clé :

- `config.toml`, avec ses sections `[llm]`, `[llm.claude]`, `[llm.gemini]`, `[llm.gemma_local]`, n'est **pas monté dans le conteneur** `openhands-app-`.
- La configuration réelle vit dans `/.openhands/settings.json`, gérée via l'UI (Settings → LLM) ou l'API `/api/v1/settings/profiles/*`.
- Les profils réellement actifs sur cette instance, listés via `GET /api/v1/settings/profiles`, sont différents des noms imaginés depuis `config.toml` :

| Profil réel | Modèle | Type |
|---|---|---|
| `deepseek` | `openrouter/deepseek/deepseek-chat-v3-0324` | cloud, économique |
| `GML-5` | `openrouter/z-ai/glm-5.1` | cloud, raisonnement |
| `gemma4` | `openai/gemma4:e4b` via `http://host.docker.internal:11434/v1/` | local, confidentiel |
| `openrouter_z-ai_glm-4.7-flash` | `openrouter/z-ai/glm-4.7-flash` | cloud, secours |

Il n'existe **pas** de profil `claude` ou `gemini` sur cette instance malgré leur présence dans `config.toml` — ils n'ont jamais été créés côté `settings.json`.

**Leçon** : toujours vérifier l'état réel via `GET /api/v1/settings/profiles` avant de construire une logique d'orchestration sur la base du fichier de config du dépôt.

---

## 4. Conception de l'orchestrateur

### 4.1 Mécanismes API utilisés

| Endpoint | Rôle |
|---|---|
| `GET /api/v1/settings/profiles` | Lister les profils disponibles et le profil actif |
| `POST /api/v1/settings/profiles/{name}/activate` | Basculer le profil LLM actif |
| `POST /api/v1/app-conversations` | Démarrer une conversation (utilise le profil actif) |
| `POST /app-conversations/{id}/switch_profile` | Changer de modèle en cours de conversation |
| `GET /api/v1/app-conversations/search` | Interroger l'état d'exécution d'une conversation |
| `GET /api/v1/app-conversations/{id}/git/changes?path=...` | Lister les fichiers modifiés dans le sandbox |

### 4.2 Règles de routage (`scripts/route_llm.py`)

Appliquées dans l'ordre, la première qui matche gagne :

1. **Confidentialité** — mots-clés sensibles (`confidentiel`, `mot de passe`, `données client`, `pii`...) → profil `gemma4` (local, aucune donnée envoyée à OpenRouter).
2. **Complexité** — mots-clés (`architecture`, `refactoring`, `sécurité`, `migration`...) → profil `GML-5` (modèle plus capable pour du raisonnement).
3. **Défaut** — toute autre tâche → profil `deepseek` (le moins cher, suffisant pour du code courant).

En cas d'échec (429, timeout, connexion) sur le profil choisi, le script bascule automatiquement sur la chaîne de secours `["deepseek", "GML-5", "gemma4", "openrouter_z-ai_glm-4.7-flash"]`.

### 4.3 Usage

```bash
python3 scripts/route_llm.py --task "Refactorer le module d'authentification" --dry-run
# → Profil choisi (règle): GML-5

python3 scripts/route_llm.py --task "Corrige ce bug d'affichage" --repo org/repo --branch main
# → Conversation démarrée avec le profil 'deepseek': <conversation_id>
```

---

## 5. Test en conditions réelles

Tâche envoyée : *"Ajoute un test unitaire simple pour scripts/route_llm.py"*, sur le dépôt `siabdel25/OpenHands`, branche `main`.

| Étape | Résultat |
|---|---|
| Règle appliquée | `deepseek` (aucun mot-clé confidentiel/complexe) |
| Activation profil | OK via `/api/v1/settings/profiles/deepseek/activate` |
| Démarrage conversation | OK, sandbox `oh-agent-server-2NVMvxcLbpDMc7fKaSCD6K` créé, repo cloné dans `/workspace/project/OpenHands` |
| Exécution agent | Statut `finished`, coût **$0.045** |
| Fichier produit | `tests/unit/test_route_llm.py` — 3 tests unitaires (`test_confidential_keywords`, `test_complex_keywords`, `test_default_profile`), tous verts après relecture |
| Nettoyage manuel | Suppression d'un import `patch` inutilisé avant commit |

Le fichier généré a été récupéré depuis le sandbox (`docker exec ... cat`), relu, corrigé, puis commité (`589aa39`) et poussé sur le fork `siabdel25/OpenHands`.

---

## 6. Points d'attention pour la suite

- **Pas de profils `claude`/`gemini` actifs** : à créer via `POST /api/v1/settings/profiles/{name}` si on veut les inclure dans la chaîne de routage.
- **Aucune authentification API sur cette instance** (mono-utilisateur local) : le script n'implémente pas de gestion de clé/session. À revoir avant tout déploiement multi-utilisateur ou exposé au réseau.
- **Récupération du diff** : l'endpoint `GET /{conversation_id}/git/diff` a renvoyé une erreur 400 sur un fichier nouvellement ajouté (probablement conçu pour des fichiers déjà trackés) ; `git/changes` fonctionne mais nécessite le chemin absolu réel du repo dans le sandbox (`/workspace/project/<repo>`, pas `/workspace`).
- **Pas de garde-fou coût** : le champ `max_budget_per_task` existe dans le modèle de settings mais n'est pas exploité par `route_llm.py`. À ajouter si le volume de tâches automatisées augmente.
- **Round-robin / répartition de charge** : évoqué comme critère de routage possible mais non implémenté — actuellement seule la chaîne de secours séquentielle gère les échecs, pas la charge en amont.

---

## 7. Fichiers livrés

- `scripts/route_llm.py` — orchestrateur de routage (commit `096e577`)
- `tests/unit/test_route_llm.py` — tests unitaires de la logique de routage (commit `589aa39`)
- Fork de référence : https://github.com/siabdel25/OpenHands (branche `main`)
