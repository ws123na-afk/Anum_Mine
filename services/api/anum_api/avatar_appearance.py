"""The avatar style of an agent or a team member: a visual choice, never a claim about a person.

The headquarters scene draws a seated figure for an agent (or a person) that has a real desk
assignment. How that figure looks is a style the owner may choose; when nothing is chosen the
scene derives a neutral default from the record id on the client. Nothing here is read from a
name, and the style says nothing about the real person or agent: it is a drawing choice, like the
colour of an agent's icon.

Every field is optional (an unset field keeps the default for that one attribute) and every
value comes from a small fixed palette, so a stored style can always be drawn. The palette is
mirrored by ``apps/web/src/lib/hqAvatar.ts`` and ``packages/contracts/src/avatar.ts``; a test
(``tests/test_avatar_appearance.py``) fails when the lists drift apart.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

AvatarStyle = Literal["feminine", "masculine", "neutral"]
SkinTone = Literal["tone_1", "tone_2", "tone_3", "tone_4", "tone_5", "tone_6"]
HairStyle = Literal["bun", "long", "swept", "spiky", "parted"]
HairColor = Literal["black", "dark_brown", "brown", "auburn", "red", "blonde", "grey"]
Outfit = Literal["formal", "smart_casual", "uniform"]
Accent = Literal["navy", "teal", "forest", "crimson", "plum", "amber", "slate", "brass"]

AVATAR_STYLES: tuple[str, ...] = ("feminine", "masculine", "neutral")
SKIN_TONES: tuple[str, ...] = ("tone_1", "tone_2", "tone_3", "tone_4", "tone_5", "tone_6")
HAIR_STYLES: tuple[str, ...] = ("bun", "long", "swept", "spiky", "parted")
HAIR_COLORS: tuple[str, ...] = ("black", "dark_brown", "brown", "auburn", "red", "blonde", "grey")
OUTFITS: tuple[str, ...] = ("formal", "smart_casual", "uniform")
ACCENTS: tuple[str, ...] = ("navy", "teal", "forest", "crimson", "plum", "amber", "slate", "brass")

# Which hair shapes exist for which build (the drawing art decides, see docs/ANUM_MINE_HQ_REALISM_ASSETS.md).
HAIR_BY_STYLE: dict[str, tuple[str, ...]] = {
    "feminine": ("bun", "long", "swept"),
    "masculine": ("spiky", "parted"),
    "neutral": ("spiky", "parted", "bun"),
}


class AvatarAppearance(BaseModel):
    """What the owner chose; ``None`` for a field means "use the default for this one"."""

    model_config = ConfigDict(extra="forbid")

    avatar_style: AvatarStyle | None = None
    skin_tone: SkinTone | None = None
    hair_style: HairStyle | None = None
    hair_color: HairColor | None = None
    outfit: Outfit | None = None
    accent: Accent | None = None

    @model_validator(mode="after")
    def _hair_matches_style(self) -> "AvatarAppearance":
        if self.avatar_style and self.hair_style and self.hair_style not in HAIR_BY_STYLE[self.avatar_style]:
            allowed = ", ".join(HAIR_BY_STYLE[self.avatar_style])
            raise ValueError(f"The {self.avatar_style} avatar style offers these hair styles: {allowed}")
        return self

    def stored(self) -> dict[str, Any] | None:
        """The JSON to store: only the fields that were chosen, or ``None`` when nothing was."""
        chosen = self.model_dump(exclude_none=True)
        return chosen or None


def avatar_from_record(value: Any) -> AvatarAppearance | None:
    """Read a stored value back. A value that no longer validates (an older palette) is dropped, never an error."""
    if not isinstance(value, dict) or not value:
        return None
    try:
        return AvatarAppearance.model_validate(value)
    except ValueError:
        return None
