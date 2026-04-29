---
title: "Bienvenue sur 0xmatthieu.dev"
slug: "bienvenue-sur-0xmatthieu-dev"
date: 2026-04-28T07:00:00+02:00
status: published
primary_source: "0xmatthieu.dev"
tags:
  - meta
  - cybersecurite
  - automation
lead: "Premier article du blog. Quelques mots sur ce qu'on va y publier, comment c'est construit, et pourquoi on automatise une partie de la chaine editoriale."
summary: "Lancement du blog cyber de Matthieu Broquard : veille, analyses techniques, retours d'experience AIS et pentest automotive, le tout alimenté par un pipeline agentique avec validation humaine."
sources:
  - title: "Site personnel"
    url: "https://0xmatthieu.dev"
  - title: "GitHub matthieu-rgb"
    url: "https://github.com/matthieu-rgb"
  - title: "ANSSI - Recommandations NIS2"
    url: "https://cyber.gouv.fr/nis2"
---

Ce blog est le compagnon de mon portfolio `0xmatthieu.dev`. On va y traiter trois choses, dans cet ordre de priorite : la veille cyber commentee, des notes techniques issues de mon parcours AIS chez Jedha, et des retours d'experience sur le pentest automotive en route vers mon stage Thales Luxembourg.

L'idee n'est pas de re-emballer des news : c'est de poser un point de vue, de citer mes sources, et de rester court.

## Ce que vous allez trouver ici

> [!quote] Principe directeur du pipeline
> Aucun article n'est publie sans relecture. C'est la difference entre un agent et un robot : la responsabilite editoriale reste humaine.

Trois rubriques, pas de fioritures :

1. **Veille commentee** : 2 a 3 articles par semaine sur des actualites cyber qui meritent un detour. Toujours une recommandation, jamais une simple paraphrase.
2. **Notes techniques** : extraits travailles de mes notes Obsidian (Active Directory, NIS2, OPNsense, Ansible), souvent en mode `pourquoi avant quoi`.
3. **Pentest automotive** : write-ups sur les projets `automotive-redteam` (analyse CAN, fuzzing UDS, bypass SecurityAccess sur RAMN).

## Comment c'est construit

Le blog n'est pas ecrit a la main. Enfin, pas entierement. Voici le pipeline :

> [!info] Architecture en une ligne
> RSS multi-sources -> scoring LLM -> redaction -> revision -> validation Telegram -> publication.

Concretement, un agent Python tourne sur un VPS Hetzner durci (Debian 12, hardening NIS2-compliant). Il lit les flux RSS pertinents pour mon profil, score chaque item via Claude Sonnet 4.6, redige un brouillon, le passe a un editeur, puis m'envoie le tout sur Telegram. Tant que je n'appuie pas sur `Approuver`, rien ne sort.

> [!warning] Validation humaine non negociable
> Aucun article n'est publie sans relecture. C'est la difference entre un agent et un robot : la responsabilite editoriale reste humaine. Les hallucinations sont reelles, les erreurs factuelles aussi.

### Le stack en deux mots

| Composant | Choix | Pourquoi |
|---|---|---|
| LLM | Claude Sonnet 4.6 | Extended thinking pour la redaction, prompt caching pour la facture |
| Hosting | Hetzner CX22 (DE) | RGPD, ~4 EUR/mois, datacenter qualite |
| Provisionnement | Ansible + sops/age | Reutilise le stack Nova Syndicate (projet AIS) |
| Validation | Telegram bot | Inline keyboard, demarrage instantane, zero friction |
| Blog | Markdown -> HTML statique | GitHub Pages, zero ops, performant |

### Un exemple de bloc de code

```python
# Score un item RSS selon mon profil (extrait simplifie)
def score_item(item: RSSItem) -> int:
    prompt = f"""Voici un item RSS : {item.title}
    Resume : {item.summary}
    Score de 0 a 10 selon le profil suivant :
    - Pentest automotive : poids fort
    - NIS2 / AIS : poids fort
    - Active Directory : poids moyen
    Reponse JSON : {{"score": int, "reason": str}}
    """
    return call_claude(prompt).score
```

Pour les commandes shell, c'est aussi clair :

```bash
# Generer un article a partir d'un fichier Markdown
uv run python -m publisher.blog publish articles_src/2026-04-28-bienvenue.md
```

## Pourquoi automatiser

Trois raisons :

- **Discipline editoriale** : un pipeline qui declenche 3x/semaine me force a tenir un rythme que je ne tiendrais pas a la main.
- **Veille active** : la phase de scoring me force a defendre pourquoi telle news vaut un article et telle autre non.
- **Competences AIS** : tout le projet est une demo vivante de NIS2 article 21. Hardening Linux, IaC Ansible, secrets sops, observabilite Wazuh, CI/CD SecDevOps. Le blog est aussi le sujet du projet.

> [!success] Ce que ca produit
> Un article publie ici, un cross-post Medium, un post LinkedIn. Trois canaux, un seul flux source en Markdown, versionne dans Git.

## Ce que vous ne trouverez pas

> [!danger] Pas de complaisance, pas de bullshit
> Pas de "top 10 outils a connaitre", pas de teasing creux, pas de copier-coller de news. Si je n'ai rien a dire, je ne publie pas.

Je revendique aussi quelques **conventions de redaction** :

- Caracteres clavier standard uniquement (pas d'em dash, pas de guillemets courbes, pas d'emojis).
- Sources citees systematiquement, avec lien.
- Espaces insecables FR avant `; : ? !`.
- Pas de listes a tout va : si un paragraphe suffit, c'est un paragraphe.

## La suite

> [!note] Prochains articles
> Premiere semaine : retour sur le projet Nova Syndicate (Phase 1, livree avril 2026), structure d'un DAT NIS2-conforme, et probablement un write-up sur Forest (HTB) avec angle Active Directory.

Si le pipeline tient et que la qualite suit, on monte a 3 articles par semaine. Sinon, on reste a 2. Jamais de quantite au detriment du fond.

Bonne lecture.
