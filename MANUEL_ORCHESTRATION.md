# Manuel d'utilisation — Orchestration multi-modèles avec Claude Code en maître

> Instance : http://localhost:3000 | Dernière mise à jour : 02 juillet 2026
> Contexte technique détaillé : `RAPPORT_ORCHESTRATION_LLM.md` | Pièges : `CLAUDE.md`

---

## 1. Le principe : maître / esclaves

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (MAÎTRE)                                       │
│  - comprend le besoin, découpe en sous-tâches               │
│  - délègue via scripts/route_llm.py                         │
│  - relit, corrige, intègre, committe                        │
└──────────────────────┬──────────────────────────────────────┘
                       │  scripts/route_llm.py --task "..." --wait
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  OpenHands (API REST, port 3000)                            │
│  - active le profil LLM choisi                              │
│  - crée un sandbox Docker (oh-agent-server-*)               │
│  - exécute l'agent CodeActAgent avec le modèle esclave      │
└──────┬──────────────┬──────────────┬────────────────────────┘
       ▼              ▼              ▼
   gemma4 (local)  deepseek (cloud)  GML-5 / claude / gemini (cloud)
   confidentiel    volume pas cher   raisonnement
   coût: $0        ~$0.005-0.05      plus cher
```

**Qui fait quoi :**

| Rôle | Acteur | Tâches |
|---|---|---|
| Maître | Claude Code | architecture, découpage, review, correction, commits, push |
| Esclave local | `gemma4` (Ollama, RTX 3060) | tâches confidentielles/offline, résumés, texte — coût nul |
| Esclave volume | `deepseek` | code courant, boilerplate, tests, doc de masse |
| Esclave raisonnement | `GML-5` / `claude` | tâches complexes quand le maître veut déléguer quand même |

## 2. Usage quotidien

### Déléguer une tâche (et récupérer le résultat)

```bash
# Routage automatique + attente du résultat final
python3 scripts/route_llm.py --task "Rédige un résumé du module X" --wait

# Avec un dépôt GitHub attaché
python3 scripts/route_llm.py --task "Ajoute des tests au module Y" \
  --repo siabdel25/OpenHands --branch main --wait

# Vérifier le routage sans rien lancer
python3 scripts/route_llm.py --task "..." --dry-run
```

Le routage est automatique par mots-clés :
1. **Confidentialité** (`confidentiel`, `mot de passe`, `pii`, `données client`...) → `gemma4` local. Jamais de repli cloud pour ces tâches.
2. **Complexité** (`architecture`, `refactoring`, `audit`, `migration`...) → `claude`.
3. **Défaut** → `deepseek`.

### Récupérer les fichiers produits par l'esclave

```bash
# Lister les fichiers modifiés dans le sandbox
curl -s -H "Accept: application/json" \
  "http://localhost:3000/api/v1/app-conversations/<conv_id>/git/changes?path=/workspace/project/<repo>"

# Extraire un fichier du sandbox vers le repo local (puis review par le maître)
docker exec <oh-agent-server-xxx> cat /workspace/project/<repo>/<fichier> > <fichier>
```

### Surveillance et maintenance

```bash
curl -s http://localhost:3000/api/v1/settings/profiles | python3 -m json.tool  # profils + actif
docker ps | grep agent-server | wc -l        # compter les sandboxes (fuite au-delà de ~5)
docker stop $(docker ps -q --filter "name=oh-agent-server")   # nettoyage (garder les actifs !)
journalctl -u ollama -f                       # santé Ollama (troncature, GPU)
```

## 3. Les règles d'or du maître

1. **Sous-tâches atomiques.** Les esclaves (surtout gemma4 8B) échouent sur le multi-étapes ("lis X puis écris Y") : ils bavardent au lieu d'agir. Une délégation = une action. Le maître fait le découpage.
2. **L'esclave local est un rédacteur, pas un agent.** Pour la doc : fournir le contenu inline dans le prompt et demander le texte ; le maître écrit le fichier lui-même. Réserver l'agentique multi-outils à deepseek — et vérifier.
3. **Toujours relire avant d'intégrer.** Exemple vécu : le test unitaire généré par deepseek était correct mais contenait un import inutilisé. Review systématique par le maître avant commit.
4. **Jamais de commit/push par l'esclave.** Le git reste dans les mains du maître.
5. **Confidentiel = local, sans exception.** Le routeur l'impose désormais (pas de repli cloud), mais ne pas contourner à la main.
6. **Un seul modèle local à la fois.** gemma4 et qwen se disputent les 12 Go de VRAM ; en parallèle, tout part en CPU (4-5 min/tour).

## 4. Limites connues

- gemma4 : fiable en mono-outil, inconstant au-delà ; qwen2.5-coder:14b : pas de tool-calling natif via Ollama, inutilisable en agent.
- Pas de garde-fou de coût (`max_budget_per_task` non exploité) — surveiller les métriques des conversations.
- Instance sans authentification : ne pas exposer le port 3000 au réseau.
- Erreur transitoire "Git provider authentication issue" au démarrage avec repo : relancer.

## 5. Comparaison avec Hermes Agent (NousResearch)

Question fréquente : "est-ce que ça ressemble à Hermes multi-agent ?" Oui sur l'idée (orchestrer plusieurs LLM), mais les philosophies diffèrent :

| | **Ce setup (Claude Code + OpenHands)** | **Hermes Agent (NousResearch)** |
|---|---|---|
| Nature | Orchestration *ad hoc* pilotée par un maître intelligent (Claude) | Agent personnel *persistant* auto-améliorant, installé et joignable par messagerie |
| Multi-modèles | Profils OpenHands (local + cloud), routage par règles maison | 23+ providers LLM, swarms d'agents autonomes qui coopèrent |
| Mémoire | Aucune côté esclaves ; le maître (Claude Code) porte le contexte | Mémoire durable multi-niveaux, modélisation de l'utilisateur |
| Compétences | Skills OpenHands statiques | Skills auto-créées qui capturent vos workflows |
| Exécution | Sandboxes Docker par conversation | Sandboxé, du VPS à 5$ au serverless |
| Contrôle qualité | Fort : le maître relit tout avant intégration | L'agent s'auto-améliore, moins de supervision humaine par construction |
| Confidentialité | Routage local forcé pour le confidentiel (Ollama) | Possible aussi (backends locaux type Ollama) |

**Lequel est mieux ?** Ça dépend de l'objectif :
- Pour du **développement logiciel supervisé** — produire du code/de la doc qui finit dans un repo git avec review humaine — ce setup est mieux adapté : le maître (Claude Code) est nettement plus capable que les esclaves et garantit la qualité en bout de chaîne. Hermes n'a pas d'équivalent de ce "maître fort".
- Pour un **assistant personnel permanent** (tâches récurrentes, mémoire de vos préférences, joignable par messagerie, qui s'améliore seul), Hermes est conçu pour ça et ce setup ne l'est pas du tout.
- Les deux ne s'excluent pas : Hermes pourrait consommer la même instance Ollama, et ce pipeline pourrait servir d'outil qu'un Hermes appelle.

---

## 6. Référence rapide

| Commande | Effet |
|---|---|
| `route_llm.py --task "..." --dry-run` | Voir le routage sans lancer |
| `route_llm.py --task "..." --wait` | Déléguer + récupérer la réponse |
| `--repo org/repo --branch main` | Attacher un dépôt GitHub |
| `--wait-timeout 900` | Délai max d'attente (défaut 900 s) |
| `--base-url http://...` | Autre instance OpenHands |
