"""How a value is written into a bundle file: a YAML scalar, a compose value, a
shell word.
"""

import json


def yq(value, ascii=True):
    """A scalar as a double-quoted YAML string, so `latest`, `1337` and `8Gi`
    stay text. None is the empty string."""
    return json.dumps("" if value is None else str(value), ensure_ascii=ascii)


def compose_value(value):
    """A compose value: yq with non-ASCII kept and every `$` doubled.

    Compose interpolates `$VAR` in its own values, so a literal `$` (in a proxy
    password, say) must be `$$` or it is silently substituted; the script's
    `--env` passes the same string through untouched."""
    return yq(value, ascii=False).replace("$", "$$")


def sh_value(value):
    """A value as one shell word, quoted only where it must be so the command
    reads like BlazeMeter's own.
    """
    text = str(value)
    if text and all(c.isalnum() or c in "_-.:/" for c in text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"
