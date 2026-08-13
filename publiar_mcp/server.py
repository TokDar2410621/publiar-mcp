"""Publiar MCP server entrypoint (stdio).

Exposes 15 Publiar tools to MCP-compatible AI agents. Each tool wraps a
REST call to the Publiar backend (https://api.publiar.app/api by default),
authenticated via the MCP API key configured per-user in /profile.

Architecture (deliberately thin):
    AI agent  ──tool call──>  publiar-mcp  ──HTTP Bearer──>  api.publiar.app
                                                                    │
                                                                    └─> Django views

No business logic is duplicated here. The MCP layer is purely a protocol
adapter: MCP tool spec → HTTP request → JSON / NDJSON / PNG bytes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ImageContent,
    TextContent,
    Tool,
)

logger = logging.getLogger("publiar_mcp")


# ── Configuration ──────────────────────────────────────────────────────────

DEFAULT_API_URL = "https://api.publiar.app/api"


class Config:
    def __init__(self) -> None:
        self.api_key = os.environ.get("PUBLIAR_API_KEY", "").strip()
        self.api_url = os.environ.get("PUBLIAR_API_URL", DEFAULT_API_URL).rstrip("/")
        self.timeout = float(os.environ.get("PUBLIAR_TIMEOUT", "60"))

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "PUBLIAR_API_KEY env var manquante. Crée une clé sur "
                "publiar.app/profile (section 'Clés MCP') puis colle-la dans "
                "la config de ton agent MCP."
            )
        if not self.api_key.startswith("mcp_pub_"):
            raise RuntimeError(
                "PUBLIAR_API_KEY invalide — doit commencer par 'mcp_pub_'."
            )


CFG = Config()


# ── HTTP client (lazy, shared) ─────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=CFG.api_url,
            headers={
                "Authorization": f"Bearer {CFG.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "publiar-mcp/0.1.0",
            },
            timeout=CFG.timeout,
        )
    return _client


# ── Helpers ────────────────────────────────────────────────────────────────

async def _request(method: str, path: str, **kwargs) -> Any:
    r = await http().request(method, path, **kwargs)
    if r.status_code >= 400:
        try:
            body = r.json()
        except Exception:
            body = r.text
        raise RuntimeError(f"Publiar API {method} {path} → {r.status_code}: {body}")
    if r.headers.get("content-type", "").startswith("application/x-ndjson"):
        # Stream NDJSON → list of events
        events: list[dict] = []
        for line in r.text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return {"events": events}
    if r.headers.get("content-type", "").startswith("image/"):
        return {"png_base64": base64.b64encode(r.content).decode(), "mime": r.headers["content-type"]}
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def _ok(content: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(content, ensure_ascii=False, indent=2))])


def _err(msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"❌ {msg}")],
        isError=True,
    )


# ── Tool definitions (22 tools) ────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        name="get_brief",
        description=(
            "LE POINT DE DÉPART de tout lead magnet : appelle-le AVANT d'écrire quoi que ce "
            "soit. Un appel rend cinq choses : la méthode Publiar complète (ordre des étapes, "
            "les 8 règles anti-hallucination R1-R8, le jury de relecture scrolleur + lecteur "
            "pressé, la discipline du hook, la structure du post), l'archétype visuel déduit de "
            "la matière déclarée, les 5 posts du corpus gagnant les plus proches du sujet, la "
            "mémoire pertinente de l'utilisateur (voix, décisions, règles apprises) et ses "
            "ressources déjà hébergées avec leurs vues. Suis la méthode rendue : elle est la "
            "référence, y compris face à tes propres habitudes de rédaction. Lecture seule, "
            "rejouable."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query":      {"type": "string", "description": "Description libre de la matière : sujet, outils, chiffre, preuve disponible, ce que l'utilisateur veut promettre."},
                "brands":     {"type": "array", "items": {"type": "string"}, "description": "Outils ou marques de la matière (ex: ['Claude','Notion']). Guident l'archétype et la recherche corpus."},
                "proof_type": {"type": "string", "enum": ["none", "photo_selfie", "screenshot_workflow", "file_tree", "role_list", "benchmark_table", "product_announcement"], "description": "Preuve que l'utilisateur POSSÈDE réellement. Déclarée ici pour déduire l'archétype ; l'asset réel sera exigé à la génération (règle R5). Défaut none."},
                "audience":   {"type": "string", "description": "Public visé. 'grand_public' bascule une annonce produit vers youtube_thumbnail."},
                "chiffre":    {"type": "string", "description": "Le chiffre central s'il existe, avec unité et période (ex: '3h par jour depuis 2 mois'). Sa source sera exigée par R2."},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="generate_lead_magnet",
        description=(
            "Génère un lead magnet LinkedIn complet (post texte + visual_spec) à partir d'un "
            "input structuré (outils utilisés, chiffre + source, type de preuve, etc.). "
            "Stream NDJSON (validation → post → visual_spec → done). Renvoie la liste des "
            "événements parsés."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "outils":        {"type": "array", "items": {"type": "string"}, "description": "Outils utilisés (ex: ['Claude','n8n'])"},
                "chiffre":       {"type": "object", "description": "Résultat chiffré : {value, unit?, timeframe?, source, source_detail?}"},
                "resource_type": {"type": "string", "enum": ["guide_pdf","video_tutorial","bundle_prompts","agents_system","workflow_template","cheat_sheet"], "description": "Nature de la ressource promise en echange du commentaire. Elle doit exister avant la publication, la regle R6 la teste."},
                "cta_keyword":   {"type": "string", "description": "Mot-cle CTA en MAJUSCULES, ex: CLAUDE, MAPS, AGENTS. C'est ce mot que les commentaires devront contenir pour declencher un DM."},
                "proof_type":    {"type": "string", "enum": ["none","photo_selfie","screenshot_workflow","file_tree","role_list","benchmark_table","product_announcement"], "description": "Type de preuve disponible. Determine l'archetype visuel deduit : une preuve reelle uploadee bat toujours une preuve generee."},
                "proof_file_tree":     {"type": "array", "items": {"type": "string"}, "description": "Arborescence, une entree par ligne. Obligatoire si proof_type vaut file_tree, ignore sinon."},
                "proof_roles":         {"type": "array", "items": {"type": "string"}, "description": "Liste de 3 a 5 roles d'agents. Obligatoire si proof_type vaut role_list, ignore sinon."},
                "proof_product_name":  {"type": "string", "description": "Nom du produit annonce. Obligatoire si proof_type vaut product_announcement."},
                "proof_product_link":  {"type": "string", "description": "Lien officiel du produit annonce, utilise avec proof_product_name."},
                "audience":      {"type": "string", "enum": ["grand_public","pro_tech","pro_business"], "description": "Public vise. Bascule l'archetype vers youtube_thumbnail quand grand_public est choisi."},
                "workshop_date": {"type": "string", "description": "Date d'un atelier a annoncer en CTA secondaire, au format libre. Omis, aucun second CTA n'est ecrit."},
                "auto_dm_enabled":   {"type": "boolean", "description": "Prepare le post pour l'envoi automatique des DM. N'arme rien ici : l'armement se fait a l'enregistrement, via register_published."},
                "resource_url":      {"type": "string", "description": "URL de la ressource promise. Testee avant generation : un lien mort annonce a des commentateurs coute plus cher que l'absence de post."},
                "resource_message":  {"type": "string", "description": "Gabarit du DM envoye aux commentateurs. Accepte {name}, {keyword} et {url}."},
            },
            "required": ["outils", "resource_type", "cta_keyword", "proof_type"],
        },
    ),
    Tool(
        name="render_visual",
        description=(
            "Rend un LeadMagnetVisualSpec en PNG (1080x1080 ou ratio adapté). Retourne le PNG "
            "encodé base64 + mime-type. 8 archétypes supportés."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "LeadMagnetVisualSpec — discriminé par archetype"},
            },
            "required": ["spec"],
        },
    ),
    Tool(
        name="render_gif",
        description=(
            "Rend un lead magnet ANIME en GIF (1080x1080, boucle infinie). Recettes : 'tool_pairing"
            "' (brands[], connector, caption_bottom) et 'metric_counter' (value, unit, prefix, labe"
            "l, caption_bottom). PUBLIABLE SUR LINKEDIN, verifie le 2026-08-11 par un aller-retour "
            "reel : un GIF de 39 frames uploade via /rest/images ressort en 39 frames animees. L'ar"
            "tefact servi s'appelle image-shrink_1280 et reste anime, ce nom ne prouve donc aucun a"
            "platissement. La cause des GIF figes d'avant etait /v2/assets, qui n'accepte que jpeg "
            "et png : le format n'etait pas supporte en entree. Seul point non verifie : que le fil"
            " JOUE l'animation a l'affichage, ce qu'un post reel regarde a l'oeil confirmerait. Cou"
            "te cher a rendre (528 Mo de pic memoire), reste sur render_visual quand l'animation n'"
            "apporte rien."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": "Recette d'animation. Deux formes acceptees : tool_pairing avec brands, connector et caption_bottom ; metric_counter avec value, unit, prefix, label et caption_bottom. Toute autre forme est refusee.",
                },
            },
            "required": ["spec"],
        },
    ),
    Tool(
        name="list_corpus",
        description=(
            "Liste le corpus de reference : 45 lead magnets LinkedIn reels, analyses, classes "
            "par archetype visuel et accompagnes de leur engagement mesure. Sert a voir ce qui "
            "a marche avant d'ecrire.\n\n"
            "Quand l'utiliser : parcourir ou filtrer le corpus entier, par exemple pour "
            "inspecter une famille visuelle. Pour trouver les entrees proches d'un sujet "
            "precis, prends find_similar_corpus, qui fait une recherche semantique la ou "
            "celui-ci ne fait qu'un listing trie.\n\n"
            "Rend, par entree : archetype, texte du post, marques citees et engagement. "
            "Lecture seule, aucun effet de bord, rejouable autant que voulu."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "archetype": {
                    "type": "string",
                    "description": "Restreint a un archetype, parmi tool_pairing, benchmark_table, dark_thumbnail, youtube_thumbnail, selfie_workspace, system_workflow_screenshot, file_tree_diagram, agent_role_diagram. Omis, rend tous les archetypes.",
                },
                "order_by": {
                    "type": "string",
                    "enum": ["engagement", "archetype"],
                    "default": "engagement",
                    "description": "engagement trie du plus engageant au moins engageant ; archetype regroupe par famille visuelle. Defaut engagement.",
                },
                "limit": {
                    "type": "integer",
                    "default": 60,
                    "maximum": 200,
                    "description": "Nombre maximal d'entrees rendues. Defaut 60, plafond 200. Le corpus en compte 45, donc le defaut suffit a tout voir.",
                },
            },
        },
    ),
    Tool(
        name="find_similar_corpus",
        description=(
            "RAG retrieval : retourne les top-K lead magnets du corpus les plus similaires à "
            "une requête (texte libre + brands optionnels). Score = embedding cosine + "
            "engagement bump (log10 likes)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "Description libre du lead magnet souhaité"},
                "brands":    {"type": "array", "items": {"type": "string"}, "description": "Brands seed pour booster les matches"},
                "k":         {"type": "integer", "default": 5, "maximum": 20, "description": "Nombre de resultats rendus, du plus proche au moins proche. Defaut 5, plafond 20."},
                "archetype": {"type": "string", "description": "Restreint la recherche a un archetype visuel. Omis, cherche dans tout le corpus."},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="publish_lead_magnet",
        description=(
            "Publie un lead magnet sur LinkedIn depuis l'agent, sans ouvrir la webapp. "
            "DEUX PHASES OBLIGATOIRES. Premier appel sans confirmed : le serveur rend le "
            "visuel et retourne un APERÇU (post_text + png_base64) SANS rien publier. Tu "
            "DOIS alors montrer le texte ET l'image à l'utilisateur et attendre son "
            "accord explicite. Second appel avec confirmed: true : la publication part. "
            "Ne confirme jamais à la place de l'utilisateur, c'est son nom sur le post. "
            "Si resource_url est fourni, il est TESTÉ avant publication (règle R6 : une "
            "ressource morte annoncée à des commentateurs coûte plus cher que l'absence "
            "de post). Retourne le post_urn : enchaîne avec register_published pour "
            "activer le suivi des commentaires et les DM."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content":       {"type": "string", "description": "Le texte du post, prêt à publier"},
                "image_url":     {"type": "string", "description": "URL publique d'une image déjà prête. À PRIVILÉGIER sur image_base64 : 200 Ko font 265 000 caractères une fois encodés."},
                "image_base64":  {"type": "string", "description": "Image déjà prête (PNG/GIF/JPEG, data URI toléré). Prioritaire sur visual_spec."},
                "visual_spec":   {"type": "object", "description": "Spec de l'archétype, rendue en PNG et attachée"},
                "first_comment": {"type": "string", "description": "Commentaire posté juste après (y mettre le lien)"},
                "resource_url":  {"type": "string", "description": "URL de la ressource promise, vérifiée avant publication"},
                "confirmed":     {"type": "boolean", "default": False, "description": "False = aperçu seul. True = publie, UNIQUEMENT après accord explicite de l'utilisateur sur l'aperçu."},
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="update_post",
        description=(
            "Reecrit le texte d'un post LinkedIn DEJA publie. Sert quand le hook est rate ou "
            "qu'une affirmation s'avere fausse : editer bat republier, l'URN, la date et l'enga"
            "gement sont conserves. DEUX PHASES OBLIGATOIRES, comme publish_lead_magnet. Premie"
            "r appel sans confirmed : retourne l'avant et l'apres SANS rien toucher. Montre les"
            " deux a l'utilisateur, puis rappelle avec confirmed: true s'il valide. Le VISUEL n"
            "'est pas modifiable, LinkedIn ne l'autorise pas : l'image publiee reste en place, "
            "seul le texte change. Passe le texte ENTIER, pas un fragment : le champ est rempla"
            "ce, jamais fusionne."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "post_urn":  {"type": "string", "description": "L'URN rendu a la publication, ex: urn:li:share:7493173296780242944"},
                "content":   {"type": "string", "description": "Le nouveau texte COMPLET du post"},
                "confirmed": {"type": "boolean", "description": "false ou absent = apercu seul, rien n'est modifie"},
            },
            "required": ["post_urn", "content"],
        },
    ),
    Tool(
        name="add_resource",
        description=(
            "Heberge la RESSOURCE qu'un lead magnet promet : le guide, la checklist, le bundle "
            "que les commentateurs recevront en DM. Rend une URL publique stable sous le domaine "
            "Publiar, a mettre dans resource_url de register_published et dans le message DM. "
            "Pourquoi heberger ici plutot qu'un lien externe : l'URL est testable par la regle R6 "
            "avant publication, la page compte ses VUES (le premier chiffre de conversion de la "
            "boucle, LinkedIn ne rend pas les clics), et une coquille se corrige en place sans "
            "casser le lien deja envoye. Upsert par slug : rappeler avec le meme slug met a jour "
            "le contenu, l'URL ne change pas. Un slug appartenant a un autre compte est refuse. "
            "Contenu markdown, 100 000 caracteres max, rendu en page lisible."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre de la page publique, jusqu'a 200 caracteres."},
                "body_markdown": {"type": "string", "description": "Le contenu complet en markdown. C'est CE que le commentateur recoit : mets-y la ressource entiere, pas un teaser."},
                "slug": {"type": "string", "description": "Optionnel. Minuscules, chiffres, tirets, 3 a 60 caracteres, pour une URL parlante. Omis, un jeton court non enumerable est genere."},
            },
            "required": ["title", "body_markdown"],
        },
    ),
    Tool(
        name="list_resources",
        description=(
            "Liste les ressources hebergees de l'utilisateur avec leur URL publique et leur "
            "compteur de VUES. C'est ici que se lit la conversion commentaire vers ouverture du "
            "lien, qu'aucun autre outil ne mesure. Lecture seule, sans parametre."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="add_memory",
        description=(
            "Ecrit une entree dans la memoire de l'utilisateur. C'est la voie d'entree du second ce"
            "rveau : ce que tu sais et que Publiar n'a pas vu passer. source et source_date sont OB"
            "LIGATOIRES, sans provenance une note ancienne ressort plus tard dans une phrase au pre"
            "sent. kind parmi brand_voice, past_post, audience, decision, manual. learned_rule et a"
            "nti_pattern sont refuses : ils se derivent des performances mesurees, on ne les declar"
            "e pas. scope cloisonne par projet et filtre le retrieval. La deduplication rend le rej"
            "eu d'une ingestion inoffensif."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content":       {"type": "string", "description": "le texte a memoriser, 20 000 caracteres maximum"},
                "kind":          {"type": "string", "description": "brand_voice | past_post | audience | decision | manual"},
                "source":        {"type": "string", "description": "d'ou ca vient, ex: 'git commit 3d61f4d', 'mesure perso', 'doc officielle'"},
                "source_date":   {"type": "string", "description": "YYYY-MM-DD, ni au futur ni avant 2015"},
                "title":         {"type": "string", "description": "titre optionnel, ameliore le retrieval"},
                "scope":         {"type": "string", "description": "nom de projet optionnel, cloisonne le retrieval"},
                "source_ref":    {"type": "string", "description": "cle technique optionnelle, permet de purger et reinserer"},
            },
            "required": ["content", "kind", "source", "source_date"],
        },
    ),
    Tool(
        name="register_published",
        description=(
            "Fait entrer un post LinkedIn deja en ligne dans le suivi Publiar, pour que ses "
            "commentaires et ses DM aient un enregistrement auquel se rattacher. Ne publie "
            "rien : le post doit exister avant l'appel.\n\n"
            "Quand l'utiliser : juste apres publish_lead_magnet, avec le post_urn qu'il rend, "
            "ou apres une publication faite a la main dans LinkedIn. Sans cet enregistrement, "
            "paste_comments, toggle_published_dm et mark_engagement_sent n'ont aucune cible.\n\n"
            "Effets, a lire avant d'appeler : cree un enregistrement. auto_dm_enabled a true "
            "ARME l'envoi automatique aux commentateurs dont le message contient cta_keyword ; "
            "laisse-le a false tant que resource_message n'a pas ete relu par l'utilisateur. "
            "NON idempotent : rappeler avec le meme post_urn cree un doublon. Exige une cle MCP."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "post_urn": {
                    "type": "string",
                    "description": "URN du post LinkedIn, forme urn:li:share:... ou urn:li:ugcPost:.... C'est la cle du suivi, elle doit correspondre a un post reellement en ligne.",
                },
                "cta_keyword": {
                    "type": "string",
                    "description": "Mot-cle attendu dans les commentaires, en MAJUSCULES. Il sert au matching : un commentaire qui le contient devient un engagement a traiter.",
                },
                "input_payload": {
                    "type": "object",
                    "description": "Entree structuree ayant servi a generer le post, conservee telle quelle pour l'apprentissage. Optionnel.",
                },
                "post_text": {
                    "type": "string",
                    "description": "Texte publie, conserve pour l'avant/apres d'update_post et pour l'analyse. Sans lui, update_post ne peut pas montrer l'etat courant.",
                },
                "visual_spec": {
                    "type": "object",
                    "description": "Spec du visuel attache, conservee pour rejouer ou analyser l'archetype. Optionnel.",
                },
                "archetype": {
                    "type": "string",
                    "description": "Archetype visuel du post, par exemple tool_pairing ou dark_thumbnail. Sert au regroupement des performances par famille.",
                },
                "resource_url": {
                    "type": "string",
                    "description": "URL de la ressource promise dans le post. Elle part dans les DM, donc elle doit repondre.",
                },
                "resource_message": {
                    "type": "string",
                    "description": "Gabarit du DM. Accepte {name}, {keyword} et {url}. C'est ce texte qui partira au nom de l'utilisateur si auto_dm_enabled est arme.",
                },
                "auto_dm_enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "true arme l'envoi automatique des DM des l'enregistrement. Defaut false, volontairement : on n'arme pas un envoi au nom de quelqu'un sans qu'il ait relu le message.",
                },
            },
            "required": ["post_urn", "cta_keyword"],
        },
    ),
    Tool(
        name="list_published",
        description=(
            "Liste tous les lead magnets publies de l'utilisateur, avec pour chacun son URN "
            "LinkedIn, son mot-cle CTA, son archetype, son etat de suivi et ses compteurs "
            "d'engagement.\n\n"
            "Quand l'utiliser : c'est le point d'entree du suivi. Les identifiants numeriques "
            "qu'il rend sont ceux qu'attendent get_published_detail, toggle_published_dm, "
            "set_published_status et paste_comments. Commence toujours par la.\n\n"
            "Sans parametre, rend tout. Lecture seule, aucun effet de bord. Exige une cle MCP, "
            "et ne montre que les posts du compte qui la porte."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_published_detail",
        description=(
            "Rend le detail d'un lead magnet publie et la liste de ses engagements : chaque "
            "commentaire recolte, s'il matche le mot-cle CTA, le DM prepare, et ou en est son "
            "envoi.\n\n"
            "Quand l'utiliser : avant d'envoyer des DM, pour recuperer les messages et les "
            "identifiants d'engagement que mark_engagement_sent attend. list_published donne la "
            "vue d'ensemble ; celui-ci descend dans un seul post.\n\n"
            "Lecture seule, aucun effet de bord. Exige une cle MCP dont le compte possede "
            "l'enregistrement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Identifiant numerique du lead magnet publie, celui que rend list_published. Ce n'est PAS l'URN LinkedIn.",
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="toggle_published_dm",
        description=(
            "Arme ou desarme l'envoi automatique des DM sur un lead magnet publie. Le suivi des "
            "commentaires continue dans les deux cas : seul l'envoi change.\n\n"
            "Quand l'utiliser : desarmer le temps de relire le message qui partira au nom de "
            "l'utilisateur, puis armer une fois ce message valide. Pour arreter le suivi entier "
            "et pas seulement les DM, prends set_published_status avec paused.\n\n"
            "Effets : ecrit un seul champ. Reversible et idempotent. Armer n'envoie RIEN "
            "retroactivement : seuls les commentaires arrives ensuite sont traites. Exige une "
            "cle MCP dont le compte possede l'enregistrement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Identifiant numerique du lead magnet publie, celui que rend list_published. Ce n'est PAS l'URN LinkedIn.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "true arme l'envoi automatique des DM, false le coupe. Aucun defaut, la valeur est obligatoire pour eviter une bascule involontaire.",
                },
            },
            "required": ["id", "enabled"],
        },
    ),
    Tool(
        name="set_published_status",
        description=(
            "Change l'etat de SUIVI d'un lead magnet deja publie. N'affecte que le suivi cote "
            "Publiar : le post reste en ligne sur LinkedIn quoi qu'il arrive, cet outil ne "
            "publie, ne modifie et ne supprime aucun contenu.\n\n"
            "Quand l'utiliser : suspendre la collecte sur un post dont tu ne traites plus les "
            "commentaires (paused), clore une campagne finie (completed), signaler un suivi "
            "casse (error), reprendre plus tard (active).\n\n"
            "Ne pas confondre avec ses voisins : toggle_published_dm coupe l'envoi des DM sans "
            "arreter le suivi ; update_post reecrit le texte du post ; publish_lead_magnet en "
            "cree un. Aucun ne remplace celui-ci.\n\n"
            "Effets : ecrit un seul champ sur l'enregistrement. Reversible, idempotent, "
            "rejouable sans dommage. Exige une cle MCP dont le compte possede l'enregistrement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Identifiant numerique du lead magnet publie, celui que rend list_published. Ce n'est PAS l'URN LinkedIn.",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "error"],
                    "description": "active = suivi en cours ; paused = suspendu ; completed = campagne terminee ; error = suivi casse. La valeur remplace l'ancienne, sans fusion.",
                },
            },
            "required": ["id", "status"],
        },
    ),
    Tool(
        name="paste_comments",
        description=(
            "Sprint E.3' Option B : import des comments LinkedIn collés par l'utilisateur "
            "(formats : tab, pipe, free-form). Parse + matche le CTA + génère les DM "
            "personnalisés prêts à copier-coller manuellement (LinkedIn r_member_social "
            "étant CLOSED, l'envoi auto est impossible)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id":       {"type": "integer", "description": "ID du PublishedLeadMagnet"},
                "raw_text": {"type": "string", "description": "Blob multi-lignes des comments"},
            },
            "required": ["id", "raw_text"],
        },
    ),
    Tool(
        name="mark_engagement_sent",
        description=(
            "Marque un commentaire comme ayant recu son DM. Publiar ne peut pas envoyer les "
            "messages lui-meme : LinkedIn n'expose aucun endpoint public de messagerie 1:1. "
            "L'utilisateur envoie a la main, puis coche ici pour que le compteur suive.\n\n"
            "Quand l'utiliser : juste apres un envoi reel, un appel par commentaire. Ne "
            "l'utilise JAMAIS pour declencher un envoi, il n'en declenche aucun. Pour obtenir "
            "les messages a envoyer, passe d'abord par paste_comments.\n\n"
            "Effets : ecrit un seul champ. Idempotent et reversible, sent a false annule la "
            "marque. Exige une cle MCP dont le compte possede l'engagement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Identifiant numerique de l'ENGAGEMENT, celui que rendent paste_comments et get_published_detail. Ce n'est pas l'id du lead magnet.",
                },
                "sent": {
                    "type": "boolean",
                    "default": True,
                    "description": "true marque le DM comme envoye, false annule la marque. Defaut true, donc omettre revient a marquer envoye.",
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="generate_pair",
        description=(
            "Pipeline v3 legacy : génère 2 variants (hook + image SPEC) couplés via le pair_generator "
            "(coupled motifs + proof gate). Utilise plutôt generate_lead_magnet — celui-ci reste pour "
            "le retrieval explicite via biais/motifs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic":            {"type": "string", "description": "Sujet du post, en texte libre. Seul champ obligatoire."},
                "claim":            {"type": "string", "description": "Affirmation a defendre, si tu en as une precise. Omise, elle est deduite du sujet."},
                "tone":             {"type": "string", "enum": ["professionnel","inspirant","storytelling","educatif","humoristique"], "description": "Registre d'ecriture. Defaut professionnel."},
                "proof_override":   {"type": "string", "enum": ["low","medium","high"], "description": "Force le niveau de preuve exige par la porte de validation, au lieu de le laisser deduire. A n'utiliser que pour tester le comportement de la porte."},
                "user_has_upload":  {"type": "boolean", "default": False, "description": "Declare qu'une preuve reelle a ete fournie, ce qui ouvre les archetypes qui l'exigent. Defaut false."},
            },
            "required": ["topic"],
        },
    ),
    Tool(
        name="prepare_visual_base",
        description=(
            "Pour l'archétype dark_thumbnail : génère via Gemini 2.5 Flash Image un fond "
            "cinematic background à partir d'un base_prompt. Retourne un data: URL prêt à "
            "injecter dans le spec."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "base_prompt": {
                    "type": "string",
                    "maxLength": 800,
                    "description": "Description de la scene a generer, en anglais de preference, 800 caracteres maximum. Decris la lumiere et le cadrage, pas le texte : les mots seront poses par le renderer, pas par le modele d'image.",
                },
            },
            "required": ["base_prompt"],
        },
    ),
    Tool(
        name="poll_published_now",
        description=(
            "Force un poll Community API immédiat sur un PublishedLeadMagnet (legacy, "
            "retournera des engagements vides puisque r_member_social est CLOSED — préférer "
            "paste_comments)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Identifiant numerique du lead magnet publie. L'appel aboutira mais rendra une liste vide, voir la description.",
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="whoami",
        description=(
            "Rend l'identite du compte associe a la cle MCP courante, et son plan.\n\n"
            "Quand l'utiliser : verifier qu'une cle est valide et qu'elle pointe sur le bon "
            "compte, avant de conclure qu'un autre outil echoue pour une raison metier. Un "
            "appel qui echoue ici signale un probleme d'authentification, pas de donnees.\n\n"
            "Sans parametre. Lecture seule, aucun effet de bord, ne revele jamais la cle "
            "elle-meme."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


# ── Tool dispatch ──────────────────────────────────────────────────────────

async def call_tool(name: str, arguments: dict) -> CallToolResult:
    try:
        if name == "get_brief":
            data = await _request("POST", "/linkedin/v3/brief/", json=arguments)
        elif name == "generate_lead_magnet":
            data = await _request("POST", "/linkedin/v3/lead-magnet/generate/", json=arguments)
        elif name == "render_visual":
            r = await http().post("/linkedin/v3/visual/generate/", json={"spec": arguments["spec"]})
            if r.status_code >= 400:
                return _err(f"render_visual {r.status_code}: {r.text[:200]}")
            data = {
                "png_base64":   base64.b64encode(r.content).decode(),
                "mime":         r.headers.get("content-type", "image/png"),
                "bytes_size":   len(r.content),
            }
        elif name == "render_gif":
            r = await http().post("/linkedin/v3/visual/gif/", json={"spec": arguments["spec"]})
            if r.status_code >= 400:
                return _err(f"render_gif {r.status_code}: {r.text[:200]}")
            data = {
                "gif_base64": base64.b64encode(r.content).decode(),
                "mime":       r.headers.get("content-type", "image/gif"),
                "bytes_size": len(r.content),
            }
        elif name == "list_corpus":
            params = {k: v for k, v in arguments.items() if v is not None}
            data = await _request("GET", "/linkedin/v3/corpus/list/", params=params)
        elif name == "find_similar_corpus":
            data = await _request("POST", "/linkedin/v3/corpus/similar/", json=arguments)
        elif name == "publish_lead_magnet":
            data = await _request("POST", "/linkedin/v3/publish/", json=arguments)
        elif name == "update_post":
            data = await _request("POST", "/linkedin/v3/publish/update/", json=arguments)
        elif name == "add_resource":
            data = await _request("POST", "/linkedin/v3/resources/add/", json=arguments)
        elif name == "list_resources":
            data = await _request("GET", "/linkedin/v3/resources/")
        elif name == "add_memory":
            data = await _request("POST", "/linkedin/v3/memory/add/", json=arguments)
        elif name == "register_published":
            data = await _request("POST", "/linkedin/v3/published/register/", json=arguments)
        elif name == "list_published":
            data = await _request("GET", "/linkedin/v3/published/list/")
        elif name == "get_published_detail":
            data = await _request("GET", f"/linkedin/v3/published/{arguments['id']}/")
        elif name == "toggle_published_dm":
            data = await _request("POST", f"/linkedin/v3/published/{arguments['id']}/toggle-dm/", json={"enabled": arguments["enabled"]})
        elif name == "set_published_status":
            data = await _request("POST", f"/linkedin/v3/published/{arguments['id']}/set-status/", json={"status": arguments["status"]})
        elif name == "paste_comments":
            data = await _request("POST", f"/linkedin/v3/published/{arguments['id']}/paste-comments/", json={"raw_text": arguments["raw_text"]})
        elif name == "mark_engagement_sent":
            data = await _request("POST", f"/linkedin/v3/engagements/{arguments['id']}/mark-sent/", json={"sent": arguments.get("sent", True)})
        elif name == "generate_pair":
            data = await _request("POST", "/linkedin/v3/generate-pair/", json=arguments)
        elif name == "prepare_visual_base":
            data = await _request("POST", "/linkedin/v3/visual/prepare-base/", json=arguments)
        elif name == "poll_published_now":
            data = await _request("POST", f"/linkedin/v3/published/{arguments['id']}/poll/")
        elif name == "whoami":
            data = await _request("GET", "/auth/profile/")
        else:
            return _err(f"Tool inconnu: {name}")
        return _ok(data)
    except Exception as e:  # noqa: BLE001
        logger.exception("tool_call_failed name=%s err=%s", name, e)
        return _err(f"{name} a échoué : {e}")


# ── Server setup ───────────────────────────────────────────────────────────

server = Server("publiar")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def handle_call(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    result = await call_tool(name, arguments or {})
    return result.content


# ── Entrypoint ─────────────────────────────────────────────────────────────

async def _run() -> None:
    CFG.validate()
    logger.info(
        "Publiar MCP server starting — api_url=%s, key_prefix=%s",
        CFG.api_url, CFG.api_key[:16] + "…",
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entrypoint for the `publiar-mcp` console script."""
    logging.basicConfig(
        level=os.environ.get("PUBLIAR_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(0)
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
