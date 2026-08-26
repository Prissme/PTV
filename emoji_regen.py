"""Outil de régénération d'emojis (gold/rainbow) pour les pets du bot.

Ce script gère désormais les **emojis d'application Discord**
(Application Emojis) et non plus les emojis de serveur (guild emojis).

Pourquoi ce changement ?
-------------------------
- Les emojis de serveur (`guild.create_custom_emoji`) sont liés à UN serveur
  précis (celui défini par `GUILD_ID`) et comptent dans son quota d'emojis
  (50/100/150/250 selon le niveau de boost).
- Les emojis d'application appartiennent au bot lui-même. Ils sont utilisables
  dans N'IMPORTE QUEL serveur où le bot est présent (et même en message privé
  avec le bot), ne dépendent d'aucun quota de serveur, et ne nécessitent donc
  plus de configurer un `GUILD_ID` dédié à l'hébergement des emojis.
- Le format d'utilisation dans les messages reste identique :
  `<:nom:identifiant>`. Aucune modification n'est donc nécessaire côté
  `PET_EMOJIS` / `pet_emoji()` / affichage des pets : seules les valeurs
  (nouveaux identifiants) changent.

Ce module fournit :
- Un rapport (`--report`) listant les pets qui ont un PNG dédié dans
  `./emojis/` et ceux qui n'en ont pas (fonctionne sans token Discord).
- Une commande slash `/regen_emojis` qui régénère les variantes gold et
  rainbow ET (si absent) l'emoji de base, puis les téléverse en tant
  qu'emojis d'application.
- Une commande slash `/list_pet_emojis` qui affiche le même rapport
  directement dans Discord.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("emoji_regen")


BASE_DIR = Path(__file__).resolve().parent
EMOJI_SOURCE_DIR = BASE_DIR / "emojis"
GENERATED_DIR = BASE_DIR / "generated"


# ---------------------------------------------------------------------------
# Résolution des pets connus (source de vérité : config.py) et des PNG
# disponibles dans ./emojis/. Fait ici sans dépendance à discord.py afin que
# le rapport (`--report`) puisse tourner sans token ni connexion réseau.
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Transforme un nom de pet en identifiant court utilisable comme nom d'emoji."""

    return re.sub(r"[^A-Za-z0-9]", "", name)


@dataclass(frozen=True)
class PetEmojiInfo:
    pet_name: str
    slug: str
    png_path: Optional[Path]

    @property
    def has_png(self) -> bool:
        return self.png_path is not None


def _load_pet_names() -> List[Tuple[str, str]]:
    """Retourne [(nom_du_pet, slug_emoji_actuel), ...] à partir de config.py.

    Le slug est celui déjà utilisé dans `PET_EMOJIS` (extrait du mention
    `<:slug:id>` actuel) quand il existe, sinon dérivé automatiquement du nom
    du pet. Cela garde les mêmes noms d'emoji qu'auparavant pendant la
    migration (seuls les IDs changent).
    """

    sys.path.insert(0, str(BASE_DIR))
    from config import PET_DEFINITIONS, PET_EMOJIS  # import tardif, local au repo

    pairs: List[Tuple[str, str]] = []
    seen = set()
    for pet in PET_DEFINITIONS:
        if pet.name in seen:
            continue
        seen.add(pet.name)

        raw = PET_EMOJIS.get(pet.name, "")
        match = re.match(r"<a?:([A-Za-z0-9_]+):(\d+)>", raw)
        slug = match.group(1) if match else _slugify(pet.name)
        pairs.append((pet.name, slug))
    return pairs


def _find_png_for_slug(slug: str) -> Optional[Path]:
    """Cherche un PNG correspondant à un slug, en tolérant quelques variantes
    de nommage historiques (ex: 'RT' -> fichier 'R-T.png')."""

    if not EMOJI_SOURCE_DIR.exists():
        return None

    candidates = [slug, "R-T" if slug == "RT" else None]
    for candidate in filter(None, candidates):
        direct = EMOJI_SOURCE_DIR / f"{candidate}.png"
        if direct.exists():
            return direct

    # Recherche insensible à la casse et aux tirets/underscores en dernier recours.
    normalized_target = re.sub(r"[^a-z0-9]", "", slug.lower())
    for png in EMOJI_SOURCE_DIR.glob("*.png"):
        normalized_candidate = re.sub(r"[^a-z0-9]", "", png.stem.lower())
        if normalized_candidate == normalized_target:
            return png
    return None


def build_pet_emoji_report() -> List[PetEmojiInfo]:
    """Construit, pour chaque pet connu de `config.py`, l'état de son PNG."""

    report = []
    for pet_name, slug in _load_pet_names():
        report.append(PetEmojiInfo(pet_name=pet_name, slug=slug, png_path=_find_png_for_slug(slug)))
    return report


def format_report_text(report: Sequence[PetEmojiInfo]) -> str:
    with_png = [info for info in report if info.has_png]
    without_png = [info for info in report if not info.has_png]

    lines = [
        f"✅ Pets AVEC un PNG dédié ({len(with_png)}) :",
        *(f"  - {info.pet_name}  →  {info.png_path.name}" for info in with_png),
        "",
        f"❌ Pets SANS PNG dédié ({len(without_png)}) :",
        *(f"  - {info.pet_name}  (slug attendu: {info.slug}.png)" for info in without_png),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Génération des variantes gold / rainbow (inchangé, indépendant de Discord)
# ---------------------------------------------------------------------------

def interpolate_color(color_a: Sequence[int], color_b: Sequence[int], factor: float) -> Tuple[int, int, int, int]:
    """Retourne la couleur interpolée entre `color_a` et `color_b`."""

    return tuple(
        int(round(a + (b - a) * factor))
        for a, b in zip(color_a, color_b)
    )  # type: ignore[return-value]


def create_diagonal_gradient(size: Tuple[int, int], colors: Sequence[Sequence[int]]) -> Image.Image:
    """Crée un dégradé diagonal multi-arrêts."""

    width, height = size
    gradient = Image.new("RGBA", size)
    pixels = gradient.load()

    if pixels is None:
        return gradient

    if len(colors) < 2:
        raise ValueError("Le dégradé nécessite au moins deux couleurs")

    segments = len(colors) - 1
    max_distance = width + height

    for y in range(height):
        for x in range(width):
            position = (x + y) / max_distance
            position = max(0.0, min(0.9999, position))
            scaled = position * segments
            index = min(int(scaled), segments - 1)
            factor = scaled - index
            color = interpolate_color(colors[index], colors[index + 1], factor)
            pixels[x, y] = color

    return gradient


def create_shine_overlay(size: Tuple[int, int], *, opacity: int = 160) -> Image.Image:
    """Crée une zone de reflets doux pour dynamiser l'emoji."""

    width, height = size
    shine = Image.new("RGBA", size, (255, 255, 255, 0))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)

    ellipse_box = (
        -int(width * 0.35),
        -int(height * 0.6),
        int(width * 1.2),
        int(height * 0.8),
    )
    draw.ellipse(ellipse_box, fill=opacity)

    shine.putalpha(mask)
    return shine


def apply_gold_effect(image: Image.Image) -> Image.Image:
    """Applique un effet doré chaleureux sur l'image."""

    base = image.convert("RGBA")
    width, height = base.size

    gold_gradient = create_diagonal_gradient(
        base.size,
        (
            (180, 110, 30, 255),
            (255, 188, 66, 255),
            (255, 239, 158, 255),
            (255, 255, 255, 255),
        ),
    )

    warmer = ImageEnhance.Color(base).enhance(0.85)
    contrasted = ImageEnhance.Contrast(warmer).enhance(1.35)
    brightened = ImageEnhance.Brightness(contrasted).enhance(1.1)

    blended = ImageChops.screen(brightened, gold_gradient)

    glow_radius = max(width, height) // 32 or 1
    glow_layer = blended.filter(ImageFilter.GaussianBlur(radius=glow_radius))
    glow_overlay = Image.new("RGBA", base.size, (255, 214, 142, 90))
    glow_overlay = ImageChops.screen(glow_overlay, gold_gradient)

    combined = Image.alpha_composite(blended, glow_overlay)
    combined = ImageChops.screen(combined, glow_layer)

    shine = create_shine_overlay(base.size, opacity=140)
    combined = Image.alpha_composite(combined, shine)
    return combined


def apply_rainbow_effect(image: Image.Image) -> Image.Image:
    """Applique un effet arc-en-ciel vibrant sur l'image."""

    base = image.convert("RGBA")
    width, height = base.size

    rainbow_gradient = create_diagonal_gradient(
        base.size,
        (
            (255, 76, 80, 255),
            (255, 166, 0, 255),
            (255, 235, 59, 255),
            (76, 175, 80, 255),
            (33, 150, 243, 255),
            (156, 39, 176, 255),
        ),
    )

    saturated = ImageEnhance.Color(base).enhance(1.9)
    contrasted = ImageEnhance.Contrast(saturated).enhance(1.25)

    blended = ImageChops.screen(contrasted, rainbow_gradient)

    sheen_radius = max(width, height) // 28 or 1
    sheen = rainbow_gradient.filter(ImageFilter.GaussianBlur(radius=sheen_radius))
    blended = ImageChops.screen(blended, sheen)

    shine = create_shine_overlay(base.size, opacity=120)
    combined = Image.alpha_composite(blended, shine)
    return combined


@dataclass(slots=True)
class GeneratedEmoji:
    """Représente un emoji généré et prêt à être téléversé en tant
    qu'emoji d'application (nom déjà résolu, sans suffixe de guilde)."""

    emoji_name: str
    file_path: Path
    image_bytes: bytes


def generate_variants_for_image(slug: str, path: Path) -> Iterable[GeneratedEmoji]:
    """Génère l'emoji de base ainsi que les variantes gold et rainbow."""

    GENERIC_VARIANTS = (
        ("gold", apply_gold_effect),
        ("rainbow", apply_rainbow_effect),
    )

    with Image.open(path) as base_image:
        base_image = base_image.convert("RGBA")
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

        # Emoji de base (identique au PNG source, juste normalisé en PNG RGBA)
        base_output_path = GENERATED_DIR / f"{slug}.png"
        base_image.save(base_output_path, format="PNG")
        base_buffer = io.BytesIO()
        base_image.save(base_buffer, format="PNG")
        yield GeneratedEmoji(emoji_name=slug, file_path=base_output_path, image_bytes=base_buffer.getvalue())

        for variant_name, processor in GENERIC_VARIANTS:
            result = processor(base_image.copy())
            output_path = GENERATED_DIR / f"{slug}_{variant_name}.png"
            result.save(output_path, format="PNG")
            buffer = io.BytesIO()
            result.save(buffer, format="PNG")
            yield GeneratedEmoji(
                emoji_name=f"{slug}_{variant_name}",
                file_path=output_path,
                image_bytes=buffer.getvalue(),
            )


def generate_all_emojis(entries: Iterable[PetEmojiInfo]) -> List[GeneratedEmoji]:
    """Génère toutes les variantes pour l'ensemble des pets ayant un PNG."""

    generated: List[GeneratedEmoji] = []
    for info in entries:
        if not info.has_png:
            continue
        try:
            generated.extend(list(generate_variants_for_image(info.slug, info.png_path)))
        except Exception:
            logger.exception("Échec du traitement de %s", info.png_path)
    return generated


# ---------------------------------------------------------------------------
# Emojis d'APPLICATION Discord (et non plus de serveur).
#
# discord.py 2.4 (version épinglée dans requirements.txt) n'expose pas encore
# de wrapper haut niveau pour ces endpoints : on passe donc par de simples
# requêtes HTTP directes à l'API Discord (`/applications/{app_id}/emojis`),
# en réutilisant la session HTTP authentifiée du bot (`bot.http`).
# Docs : https://discord.com/developers/docs/resources/emoji#application-emojis
# ---------------------------------------------------------------------------

API_BASE = "https://discord.com/api/v10"


def _data_uri(image_bytes: bytes, mime: str = "image/png") -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class ApplicationEmojiClient:
    """Petit client HTTP pour gérer les emojis d'application d'un bot."""

    def __init__(self, bot, application_id: int) -> None:
        self._bot = bot
        self._application_id = application_id

    async def _request(self, method: str, path: str, **kwargs):
        # Réutilise la session aiohttp interne du bot (déjà authentifiée avec
        # `Bot <token>` et gérant proprement le rate-limit global de Discord).
        session = self._bot.http._HTTPClient__session  # type: ignore[attr-defined]
        headers = {"Authorization": f"Bot {self._bot.http.token}"}
        async with session.request(method, f"{API_BASE}{path}", headers=headers, **kwargs) as resp:
            if resp.content_type == "application/json":
                payload = await resp.json()
            else:
                payload = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"{method} {path} -> {resp.status}: {payload}")
            return payload

    async def list_emojis(self) -> List[dict]:
        payload = await self._request("GET", f"/applications/{self._application_id}/emojis")
        return payload.get("items", [])  # type: ignore[union-attr]

    async def create_emoji(self, name: str, image_bytes: bytes) -> dict:
        return await self._request(  # type: ignore[return-value]
            "POST",
            f"/applications/{self._application_id}/emojis",
            json={"name": name, "image": _data_uri(image_bytes)},
        )

    async def delete_emoji(self, emoji_id: int) -> None:
        await self._request("DELETE", f"/applications/{self._application_id}/emojis/{emoji_id}")

    async def upsert_emoji(self, name: str, image_bytes: bytes, existing_by_name: dict) -> Tuple[dict, bool]:
        """Crée l'emoji, ou le recrée si un emoji du même nom existe déjà.

        L'API Discord ne permet de modifier que le *nom* d'un emoji
        d'application (pas son image) via PATCH : pour changer l'image on
        supprime donc l'ancien puis on recrée. Retourne (emoji, updated).
        """

        current = existing_by_name.get(name)
        if current is not None:
            await self.delete_emoji(int(current["id"]))
            created = await self.create_emoji(name, image_bytes)
            return created, True
        created = await self.create_emoji(name, image_bytes)
        return created, False


async def upload_emojis(client: ApplicationEmojiClient, emojis: Sequence[GeneratedEmoji]) -> Tuple[int, int]:
    """Crée ou remplace les emojis d'application correspondants."""

    existing = {emoji["name"]: emoji for emoji in await client.list_emojis()}
    created = 0
    updated = 0

    for emoji in emojis:
        try:
            _, was_update = await client.upsert_emoji(emoji.emoji_name, emoji.image_bytes, existing)
            if was_update:
                updated += 1
                print(f"♻️ updated application emoji {emoji.emoji_name}")
            else:
                created += 1
                print(f"✅ uploaded application emoji {emoji.emoji_name}")
        except Exception as exc:
            raise RuntimeError(f"Impossible de téléverser {emoji.emoji_name}: {exc}") from exc
    return created, updated


# ---------------------------------------------------------------------------
# Bot / commandes slash (import discord.py différé pour permettre `--report`
# sans dépendance réseau ni token).
# ---------------------------------------------------------------------------

def _build_bot():
    import discord
    from discord import app_commands
    from discord.ext import commands

    class EmojiRegeneration(commands.Cog):
        """Cog contenant les commandes slash de gestion des emojis d'application."""

        def __init__(self, bot: commands.Bot) -> None:
            self.bot = bot

        @app_commands.command(
            name="regen_emojis",
            description="Génère et téléverse les emojis (base/gold/rainbow) en tant qu'emojis d'application",
        )
        async def regen_emojis(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "🔄 Génération des emojis en cours (emojis d'application, plus de serveur)...",
                ephemeral=True,
            )

            try:
                report = await asyncio.to_thread(build_pet_emoji_report)
                source_entries = [info for info in report if info.has_png]
                if not source_entries:
                    await interaction.followup.send(
                        "Aucun fichier PNG trouvé dans ./emojis/.", ephemeral=True
                    )
                    return

                generated_emojis = await asyncio.to_thread(generate_all_emojis, source_entries)
                if not generated_emojis:
                    await interaction.followup.send(
                        "Une erreur est survenue lors de la régénération des emojis.",
                        ephemeral=True,
                    )
                    return

                app_info = await self.bot.application_info()
                client = ApplicationEmojiClient(self.bot, app_info.id)
                created, updated = await upload_emojis(client, generated_emojis)

                missing = [info for info in report if not info.has_png]
                summary = [f"✅ {created + updated} emojis d'application générés et téléversés."]
                if missing:
                    summary.append(
                        f"⚠️ {len(missing)} pet(s) sans PNG dédié (emoji inchangé) : "
                        + ", ".join(info.pet_name for info in missing[:15])
                        + ("…" if len(missing) > 15 else "")
                    )
            except Exception:
                print("Erreur lors de la régénération des emojis :")
                print(traceback.format_exc())
                await interaction.followup.send(
                    "Une erreur est survenue lors de la régénération des emojis.",
                    ephemeral=True,
                )
                return

            await interaction.followup.send("\n".join(summary), ephemeral=True)

        @app_commands.command(
            name="list_pet_emojis",
            description="Liste les pets avec/sans PNG d'emoji dédié",
        )
        async def list_pet_emojis(self, interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            report = await asyncio.to_thread(build_pet_emoji_report)
            text = format_report_text(report)
            # Discord limite un message à 2000 caractères : on découpe si besoin.
            chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [text]
            for chunk in chunks:
                await interaction.followup.send(f"```{chunk}```", ephemeral=True)

    class EmojiRegenBot(commands.Bot):
        """Bot Discord minimaliste dédié à la régénération d'emojis d'application."""

        def __init__(self) -> None:
            intents = discord.Intents.none()
            intents.guilds = True
            super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)

        async def setup_hook(self) -> None:  # pragma: no cover - cycle de vie Discord
            await self.add_cog(EmojiRegeneration(self))
            # Les emojis d'application ne sont pas liés à un serveur : on
            # synchronise les commandes slash globalement. Si un GUILD_ID est
            # fourni (optionnel), on synchronise aussi localement pour un
            # déploiement instantané pendant les tests.
            await self.tree.sync()
            guild_id_env = os.getenv("GUILD_ID")
            if guild_id_env:
                try:
                    guild = discord.Object(id=int(guild_id_env))
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.info("Commandes également synchronisées pour la guilde %s (mode test)", guild_id_env)
                except ValueError:
                    logger.warning("GUILD_ID invalide, synchronisation locale ignorée")
            logger.info("Commandes synchronisées globalement (emojis d'application)")

        async def on_ready(self) -> None:  # pragma: no cover - callback Discord
            assert self.user is not None
            logger.info("Connecté en tant que %s (%s)", self.user, self.user.id)

    return EmojiRegenBot


def _run_bot() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    discord_token = os.getenv("DISCORD_TOKEN")
    if not discord_token:
        raise RuntimeError("DISCORD_TOKEN doit être défini dans le fichier .env")

    EmojiRegenBot = _build_bot()
    bot = EmojiRegenBot()
    bot.run(discord_token)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="store_true",
        help="Affiche uniquement le rapport pets-avec/sans-PNG (aucune connexion Discord requise) puis quitte.",
    )
    args = parser.parse_args()

    if args.report:
        report = build_pet_emoji_report()
        print(format_report_text(report))
        return

    _run_bot()


if __name__ == "__main__":
    main()
