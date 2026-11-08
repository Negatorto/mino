from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence


class FileStatus(str, Enum):
    """Comparison status values displayed in the results Treeview."""

    DIFFERENT = "DIFFERENT"
    IDENTICAL = "IDENTICAL"
    ONLY_ON_TEST = "ONLY ON TEST"
    ONLY_ON_PROD = "ONLY ON PROD"


KEEP_CURRENT = "<Keep Current>"
DEFAULT_NOTE_KEYWORD = "MINO"
DEFAULT_SSH_PORT = 22

# Centralized color palettes for Light and Dark modes (shared across Treeview and DiffWindow)
THEME_PALETTES: dict[str, dict[str, dict[str, str]]] = {
    "Dark": {
        "treeview": {
            "bg": "#2B2B2B",
            "fg": "#E0E0E0",
            "heading_bg": "#343638",
            "selected_bg": "#1F538D",
        },
        "tags": {
            "different_bg": "#5D0000",
            "different_fg": "#FFD0D0",
            "solo_test_bg": "#003D5D",
            "solo_test_fg": "#C0E8FF",
            "solo_prod_bg": "#004D00",
            "solo_prod_fg": "#D0FFD0",
            "identical_fg": "#707070",
            "blank_bg": "#252525",
            "inline_bg": "#7A7A00",
            "inline_fg": "#FFFFE0",
            "gutter_bg": "#202020",
            "gutter_fg": "#909090",
        },
    },
    "Light": {
        "treeview": {
            "bg": "#FFFFFF",
            "fg": "#101010",
            "heading_bg": "#EAEAEA",
            "selected_bg": "#3B8ED0",
        },
        "tags": {
            "different_bg": "#FFC0C0",
            "different_fg": "#8B0000",
            "solo_test_bg": "#C0E8FF",
            "solo_test_fg": "#003A59",
            "solo_prod_bg": "#C0FFC0",
            "solo_prod_fg": "#004D00",
            "identical_fg": "#888888",
            "blank_bg": "#FAFAFA",
            "inline_bg": "#FFFF99",
            "inline_fg": "#4D4D00",
            "gutter_bg": "#EAEAEA",
            "gutter_fg": "#707070",
        },
    },
}


def normalize_octal_display(raw_value: Any) -> str:
    """
    Normalizes an octal permission value coming from Tcl/Tk Treeview
    (which coerces numeric strings like '0755' or '75' to int) back to a clean string.
    Preserves transition strings like '644 -> 755' and placeholders like '?'.
    """
    text = str(raw_value).strip()
    if not text or text == "?":
        return text or "?"
    if " -> " in text:
        left, right = text.split(" -> ", 1)
        return f"{normalize_octal_display(left)} -> {normalize_octal_display(right)}"
    if text.isdigit() and len(text) < 3:
        return text.zfill(3)
    return text


def split_transition(value: str) -> tuple[str, str]:
    """Splits a 'TEST -> PROD' transition string into (test_val, prod_val)."""
    if " -> " in value:
        left, right = value.split(" -> ", 1)
        return left, right
    return value, value


def format_transition(val1: str, val2: str) -> str:
    """Formats two metadata values, collapsing them if identical."""
    return val1 if val1 == val2 else f"{val1} -> {val2}"


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """Structured representation of a row in the comparison results Treeview."""

    status: str
    relative_path: str
    note: str = ""
    owner_group: str = ""
    symbolic_perms: str = ""
    octal_perms: str = ""

    def to_tree_values(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.status,
            self.relative_path,
            self.note,
            self.owner_group,
            self.symbolic_perms,
            self.octal_perms,
        )

    @classmethod
    def from_tree_values(cls, values: Sequence[Any]) -> ComparisonRow:
        if len(values) >= 6:
            status, rel_path, note, owner_group, sym_perms, oct_perms = values[:6]
        elif len(values) == 5:
            status, rel_path, owner_group, sym_perms, oct_perms = values
            note = ""
        else:
            raise ValueError(f"Unexpected Treeview row format (got {len(values)} columns): {values!r}")

        return cls(
            status=str(status),
            relative_path=str(rel_path),
            note=str(note),
            owner_group=str(owner_group),
            symbolic_perms=str(sym_perms),
            octal_perms=normalize_octal_display(oct_perms),
        )

    @property
    def can_diff(self) -> bool:
        return self.status in (FileStatus.DIFFERENT.value, FileStatus.IDENTICAL.value)

    @property
    def can_sync_to_prod(self) -> bool:
        return self.status in (FileStatus.DIFFERENT.value, FileStatus.ONLY_ON_TEST.value)

    @property
    def can_sync_to_test(self) -> bool:
        return self.status in (FileStatus.DIFFERENT.value, FileStatus.ONLY_ON_PROD.value)

    @property
    def exists_on_test(self) -> bool:
        return self.status in (
            FileStatus.DIFFERENT.value,
            FileStatus.IDENTICAL.value,
            FileStatus.ONLY_ON_TEST.value,
        )

    @property
    def exists_on_prod(self) -> bool:
        return self.status in (
            FileStatus.DIFFERENT.value,
            FileStatus.IDENTICAL.value,
            FileStatus.ONLY_ON_PROD.value,
        )

    def split_owners(self) -> tuple[str, str]:
        return split_transition(self.owner_group)

    def split_symbolic_perms(self) -> tuple[str, str]:
        return split_transition(self.symbolic_perms)

    def primary_owner_and_group(self) -> tuple[str, str]:
        """Extracts (owner, group) from the primary side of owner_group."""
        primary, _ = split_transition(self.owner_group)
        if ":" in primary:
            owner, group = primary.split(":", 1)
            return owner, group
        return primary, ""

    def primary_octal_perms(self) -> str:
        primary, _ = split_transition(self.octal_perms)
        return normalize_octal_display(primary)

