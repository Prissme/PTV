"""Outil de régénération d'emojis gold et rainbow pour Discord."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

# Chargement de la configuration depuis le fichier .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("emoji_regen")


BASE_DIR = Path(__file__).resolve().parent
EMOJI_SOURCE_DIR = BASE_DIR / "emojis"
GENERATED_DIR = BASE_DIR / "generated"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_ENV = os.getenv("GUILD_ID")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN doit être défini dans le fichier .env")

if not GUILD_ID_ENV:
    raise RuntimeError("GUILD_ID doit être défini dans le fichier .env")

try:
    TARGET_GUILD_ID = int(GUILD_ID_ENV)
except ValueError as exc:
    raise RuntimeError("GUILD_ID doit être un entier valide") from exc


@dataclass(slots=True)
class GeneratedEmoji:
    """Représente un emoji généré et prêt à être téléversé."""

    base_name: str
    variant: str
    file_path: Path
    image_bytes: bytes

    @property
    def emoji_name(self) -> str:
        slug = slugify(self.base_name)
        return f"{slug}_{self.variant}"


def slugify(name: str) -> str:
    """Transforme un nom d'emoji en identifiant Discord valide."""

    lowered = name.lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", lowered)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "emoji"


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

    # Reflet elliptique pour simuler une lumière venant du dessus gauche
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

    # Ajustements de couleur et de contraste
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


def list_png_sources() -> List[Path]:
    """Retourne la liste des fichiers PNG dans le dossier ./emojis/."""

    if not EMOJI_SOURCE_DIR.exists():
        return []

    return [
        path
        for path in sorted(EMOJI_SOURCE_DIR.iterdir())
        if path.is_file() and path.suffix.lower() == ".png"
    ]


def generate_variants_for_image(path: Path) -> Iterable[GeneratedEmoji]:
    """Génère les variantes gold et rainbow pour une image donnée."""

    base_name = path.stem
    GENERIC_VARIANTS = (
        ("gold", apply_gold_effect),
        ("rainbow", apply_rainbow_effect),
    )

    with Image.open(path) as base_image:
        base_image = base_image.convert("RGBA")
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        for variant_name, processor in GENERIC_VARIANTS:
            result = processor(base_image.copy())
            output_path = GENERATED_DIR / f"{base_name}_{variant_name}.png"
            result.save(output_path, format="PNG")
            buffer = io.BytesIO()
            result.save(buffer, format="PNG")
            buffer.seek(0)
            yield GeneratedEmoji(
                base_name=base_name,
                variant=variant_name,
                file_path=output_path,
                image_bytes=buffer.getvalue(),
            )


def generate_all_emojis(paths: Iterable[Path]) -> List[GeneratedEmoji]:
    """Génère toutes les variantes pour l'ensemble des images sources."""

    generated: List[GeneratedEmoji] = []
    for path in paths:
        try:
            generated.extend(list(generate_variants_for_image(path)))
        except Exception:
            logger.exception("Échec du traitement de %s", path)
    return generated


async def upload_emojis(guild: discord.Guild, emojis: Sequence[GeneratedEmoji]) -> Tuple[int, int]:
    """Crée ou met à jour les emojis sur le serveur Discord."""

    existing = {emoji.name: emoji for emoji in await guild.fetch_emojis()}
    created = 0
    updated = 0

    for emoji in emojis:
        target_name = emoji.emoji_name
        try:
            if target_name in existing:
                discord_emoji = existing[target_name]
                await discord_emoji.edit(image=emoji.image_bytes, name=target_name)
                updated += 1
                print(f"♻️ updated {target_name}")
            else:
                await guild.create_custom_emoji(name=target_name, image=emoji.image_bytes)
                created += 1
                print(f"✅ uploaded {target_name}")
        except discord.HTTPException as exc:
            raise RuntimeError(f"Impossible de téléverser {target_name}: {exc}") from exc
    return created, updated


class EmojiRegeneration(commands.Cog):
    """Cog contenant la commande slash /regen_emojis."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="regen_emojis", description="Génère et téléverse les emojis gold/rainbow")
    async def regen_emojis(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans un serveur.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔄 Génération des emojis en cours...", ephemeral=True
        )

        guild = interaction.guild
        assert guild is not None

        try:
            source_images = await asyncio.to_thread(list_png_sources)
            if not source_images:
                await interaction.followup.send(
                    "Aucun fichier PNG trouvé dans ./emojis/.", ephemeral=True
                )
                return

            generated_emojis = await asyncio.to_thread(generate_all_emojis, source_images)
            if not generated_emojis:
                print("Aucune variante générée pour les images sources disponibles.")
                await interaction.followup.send(
                    "Une erreur est survenue lors de la régénération des emojis.",
                    ephemeral=True,
                )
                return

            created, updated = await upload_emojis(guild, generated_emojis)
        except Exception:
            print("Erreur lors de la régénération des emojis :")
            print(traceback.format_exc())
            await interaction.followup.send(
                "Une erreur est survenue lors de la régénération des emojis.",
                ephemeral=True,
            )
            return

        total = created + updated
        await interaction.followup.send(
            f"✅ {total} emojis générés et téléversés avec succès.", ephemeral=True
        )


class EmojiRegenBot(commands.Bot):
    """Bot Discord minimaliste dédié à la régénération d'emojis."""

    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)

    async def setup_hook(self) -> None:  # pragma: no cover - cycle de vie Discord
        await self.add_cog(EmojiRegeneration(self))
        guild = discord.Object(id=TARGET_GUILD_ID)
        await self.tree.sync(guild=guild)
        logger.info("Commandes synchronisées pour la guilde %s", TARGET_GUILD_ID)

    async def on_ready(self) -> None:  # pragma: no cover - callback Discord
        assert self.user is not None
        logger.info("Connecté en tant que %s (%s)", self.user, self.user.id)


def main() -> None:
    bot = EmojiRegenBot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
