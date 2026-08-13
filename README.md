<!-- mcp-name: io.github.TokDar2410621/publiar -->

# publiar-mcp

**Serveur MCP qui écrit des lead magnets LinkedIn et refuse d'inventer.**

[![publiar-mcp MCP server](https://glama.ai/mcp/servers/TokDar2410621/publiar-mcp/badges/card.svg)](https://glama.ai/mcp/servers/TokDar2410621/publiar-mcp)

> **EN.** MCP server for Publiar. Writes LinkedIn lead magnets that refuse to
> fabricate facts, renders the matching visual, publishes to LinkedIn and tracks
> what happened. 22 tools, stdio transport. Generated posts are in French. MIT.

Demande un post LinkedIn à un LLM, il te sort « après 7 semaines de tests
intensifs, +340 % d'engagement ». Tu n'as rien testé pendant 7 semaines et le
chiffre sort de nulle part. Tu le publies, quelqu'un demande la source, et tu
n'en as pas.

Ce serveur applique six règles bloquantes avant d'écrire une ligne. Pas de
chiffre sans source déclarée, pas de marque non citée, pas de client anonyme
fictif, pas de durée de test imaginaire, pas d'années d'expérience sorties du
chapeau, et pas de ressource promise dont l'URL ne répond pas. Si tu ne l'as
pas dit, ça n'existe pas.

## Installation

```bash
pip install publiar-mcp
```

## Configuration

Génère une clé sur [publiar.app/profile](https://publiar.app/profile), section
« Clés MCP ». Elle n'est affichée qu'une fois.

**Claude Desktop**, `%APPDATA%\Claude\claude_desktop_config.json` sur Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` sur macOS :

```json
{
  "mcpServers": {
    "publiar": {
      "command": "publiar-mcp",
      "env": {
        "PUBLIAR_API_KEY": "mcp_pub_xxxxx",
        "PUBLIAR_API_URL": "https://api.publiar.app/api"
      }
    }
  }
}
```

**Cursor**, `~/.cursor/mcp.json`, même format. **Claude Code** :

```bash
claude mcp add publiar -e PUBLIAR_API_KEY=mcp_pub_xxxxx -- publiar-mcp
```

Redémarre complètement l'agent pour qu'il charge le serveur.

## Les 22 outils

### Commencer

| Outil | Ce qu'il fait |
|---|---|
| `get_brief` | LE point de départ : la méthode complète (règles R1-R8, jury de relecture, hook), l'archétype déduit, les 5 voisins du corpus, ta mémoire et tes ressources, en un appel |

### Écrire et rendre

| Outil | Ce qu'il fait |
|---|---|
| `generate_lead_magnet` | Post LinkedIn + `visual_spec`, à partir d'entrées structurées. Refuse si la matière manque. |
| `generate_pair` | Générateur de paires, motifs couplés et porte de preuve |
| `render_visual` | Rend un `visual_spec` en PNG 1080×1080, 8 archétypes |
| `render_gif` | Rend un GIF animé, publiable tel quel sur LinkedIn |
| `prepare_visual_base` | Fond cinématique généré, pour l'archétype `dark_thumbnail` |

### Publier

| Outil | Ce qu'il fait |
|---|---|
| `add_resource` | Héberge la ressource promise (markdown → page publique `/r/slug`), vues comptées |
| `list_resources` | Tes ressources hébergées et combien de commentateurs ont ouvert le lien |
| `publish_lead_magnet` | Publie texte + visuel sur LinkedIn. Deux phases : aperçu, puis confirmation. |
| `update_post` | Réécrit le texte d'un post déjà en ligne. L'URN, la date et l'engagement survivent. |
| `register_published` | Enregistre un post publié pour le suivi |

### Mesurer et relancer

| Outil | Ce qu'il fait |
|---|---|
| `list_published` | Tes lead magnets publiés et leurs statistiques |
| `get_published_detail` | Détail d'un post, engagements et statut des DM |
| `set_published_status` | `active`, `paused`, `completed`, `error` |
| `toggle_published_dm` | Active ou coupe le DM automatique sur un post |
| `paste_comments` | Analyse des commentaires collés, prépare les DM à envoyer |
| `mark_engagement_sent` | Marque un DM comme envoyé |
| `poll_published_now` | Récolte via l'API. Renvoie vide, voir Limitations. |

### Mémoire et corpus

| Outil | Ce qu'il fait |
|---|---|
| `add_memory` | Écrit dans ta mémoire. `source` et `source_date` obligatoires. |
| `list_corpus` | Les 45 lead magnets de référence analysés |
| `find_similar_corpus` | Recherche sémantique dans le corpus, pondérée par l'engagement |
| `whoami` | Vérifie l'authentification |

## Les 8 archétypes visuels

Le serveur déduit l'archétype depuis ta matière, tu n'as pas à le choisir.

| Archétype | Déclencheur |
|---|---|
| `tool_pairing` | 2 ou 3 outils combinés |
| `benchmark_table` | comparaison chiffrée de modèles ou de produits |
| `dark_thumbnail` | annonce de produit ou de lancement |
| `youtube_thumbnail` | audience grand public, titre fort |
| `selfie_workspace` | une photo de toi est fournie |
| `system_workflow_screenshot` | un screenshot de workflow est fourni |
| `file_tree_diagram` | une arborescence de stack ou de skills |
| `agent_role_diagram` | une équipe de 3 à 5 agents |

Une preuve réelle uploadée bat toujours une preuve générée. Si tu fournis une
photo, l'archétype est `selfie_workspace`, même si tu as aussi nommé deux outils.

## Exemple

> Génère un lead magnet sur Claude + Notion pour automatiser ma prise de notes.
> J'ai mesuré 3 h gagnées par jour depuis 2 mois. CTA : NOTION.

L'agent appelle `generate_lead_magnet` et te rend le post, les hashtags et le
`visual_spec`. `render_visual` en fait un PNG, `publish_lead_magnet` l'envoie.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `PUBLIAR_API_KEY` | — | **Obligatoire.** Clé `mcp_pub_...` créée sur publiar.app/profile |
| `PUBLIAR_API_URL` | `https://api.publiar.app/api` | Endpoint REST |
| `PUBLIAR_TIMEOUT` | `60` | Délai HTTP en secondes |
| `PUBLIAR_LOG_LEVEL` | `INFO` | Niveau de log sur stderr |

## Architecture

```
   Agent IA           publiar-mcp            API Publiar
   Claude Desktop  →  ce paquet,        →    Django,
   Cursor             processus stdio        api.publiar.app
                          │                       │
                          │  Bearer mcp_pub_…     │
                          └───────────────────────┘
                              HTTPS, JSON et NDJSON
```

Aucune logique métier ici. Ce paquet est un adaptateur de protocole ; tout vit
dans le backend qui sert déjà publiar.app.

## Sécurité

Les clés sont stockées hachées en SHA-256 côté serveur, la valeur brute n'est
montrée qu'à la création. Chaque clé est révocable depuis publiar.app/profile.
`last_used_at` est suivi pour repérer les clés dormantes. Le préfixe
`mcp_pub_` reste visible pour identifier une clé sans la révéler.

## Limitations connues

`poll_published_now` renverra toujours vide. Lire les commentaires d'un post
personnel exige la permission LinkedIn `r_member_social`, que LinkedIn classe
en accès privé et n'accorde plus. Utilise `paste_comments` : tu colles les
commentaires, le serveur fait le reste.

`generate_lead_magnet` consomme de l'inférence côté serveur.

Les envois d'images pour `selfie_workspace` et `system_workflow_screenshot` ne
passent pas encore par le MCP. L'agent doit fournir le base64 dans le spec.

## Licence

MIT.
