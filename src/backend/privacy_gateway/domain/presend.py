import re
from collections.abc import Iterable


def literal_pattern(value: str) -> str:
    value = value.strip()
    if not value:
        return r"(?!)"
    return (
        (r"(?<!\w)" if re.match(r"\w", value[0]) else "")
        + r"\s*".join(map(re.escape, value.split()))
        + (r"(?!\w)" if re.match(r"\w", value[-1]) else "")
    )


def scrub_literals(text: str, literals: Iterable[tuple[str, str]]) -> tuple[str, list[str]]:
    hits = []
    for value, category in sorted(literals, key=lambda item: len(item[0]), reverse=True):
        value = value.strip()
        if not value:
            continue
        pattern = literal_pattern(value)

        def replacement(_: re.Match[str], category: str = category) -> str:
            return f"[{category.upper()}]"

        text, count = re.subn(pattern, replacement, text, flags=re.IGNORECASE)
        hits.extend([category] * count)
    return text, hits
