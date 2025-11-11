from __future__ import annotations

from typing import Optional

VALID_COLOR_IDS = {str(i) for i in range(1, 12)}

_COLOR_NAME_TO_ID = {
    # Official Google Calendar names
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
    # Common English aliases
    "purple": "3",
    "violet": "3",
    "pink": "4",
    "rose": "4",
    "yellow": "5",
    "orange": "6",
    "teal": "7",
    "cyan": "7",
    "gray": "8",
    "grey": "8",
    "black": "8",
    "blue": "9",
    "navy": "9",
    "green": "10",
    "emerald": "10",
    "olive": "10",
    "red": "11",
    "crimson": "11",
    "scarlet": "11",
    # Chinese aliases
    "葡萄": "3",
    "紫色": "3",
    "紫": "3",
    "粉色": "4",
    "粉": "4",
    "玫红": "4",
    "黄色": "5",
    "黄": "5",
    "橙色": "6",
    "橘色": "6",
    "橘": "6",
    "橙": "6",
    "青色": "7",
    "青": "7",
    "蓝色": "9",
    "蓝": "9",
    "绿色": "10",
    "绿": "10",
    "灰色": "8",
    "灰": "8",
    "黑色": "8",
    "黑": "8",
    "红色": "11",
    "红": "11",
}


def normalize_color_hint(value: Optional[str]) -> Optional[str]:
    """Normalize user/model-provided color hints into a valid Google Calendar colorId."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Direct numeric match
    if text in VALID_COLOR_IDS:
        return text

    # Extract digits (handles inputs like "#11" or "color_07")
    digits_only = "".join(ch for ch in text if ch.isdigit())
    if digits_only:
        try:
            normalized_digits = str(int(digits_only))
        except ValueError:
            normalized_digits = ""
        if normalized_digits in VALID_COLOR_IDS:
            return normalized_digits

    lowered = text.lower()
    if lowered in _COLOR_NAME_TO_ID:
        return _COLOR_NAME_TO_ID[lowered]

    if lowered.endswith("色"):
        base = lowered[:-1]
        if base in _COLOR_NAME_TO_ID:
            return _COLOR_NAME_TO_ID[base]

    return None
