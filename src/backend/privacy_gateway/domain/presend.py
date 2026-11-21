import re


def literal_pattern(value: str) -> str:
    value = value.strip()
    if not value:
        return r"(?!)"
    return (
        (r"(?<!\w)" if re.match(r"\w", value[0]) else "")
        + r"\s*".join(map(re.escape, value.split()))
        + (r"(?!\w)" if re.match(r"\w", value[-1]) else "")
    )
