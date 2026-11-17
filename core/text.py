"""Text normalization shared by the whole core (D4 rules 3 and 5)."""

import unicodedata


def normalize(text: str) -> str:
    """Return `text` without accents, case or surrounding spaces.

    NFKD splits "á" into "a" + a combining accent; dropping the combining marks leaves "a".
    `casefold` is a stronger `lower` for comparisons. Example: "  Ángel " -> "angel".
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return without_marks.casefold().strip()
