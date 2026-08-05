"""Strip leaked tool-call markup out of model-written free text.

When the model writes a long string argument it occasionally emits its own
tool-call syntax inside the value. The live ADANIPORTS journal entry ended up
holding a literal:

    ...hit 50% of the time in ~2 days.",
    <parameter name="stop_loss_price">1451.74 | WHY NOW: ...

Two things went wrong there and both matter. The stored text became unreadable,
and the stop the model was trying to pass never arrived as an argument — so the
system silently fell back to the mode default and managed the position against
levels the model never chose.

We cannot recover the lost argument after the fact, but we can keep the
corruption out of anything a human or a later cycle reads back.
"""

from __future__ import annotations

import re

# Tool-call scaffolding in any of the shapes we have seen leak.
_MARKUP = re.compile(
    r"""</?(?:antml:)?(?:parameter|invoke|function_calls|function_results)\b[^>]*>""",
    re.IGNORECASE,
)
# A stray quote-comma-newline right where the model tried to close an argument.
_STRAY_ARG_BREAK = re.compile(r'",\s*(?=\n|$)')


def clean_model_text(text: str | None, *, max_len: int | None = None) -> str:
    """Return `text` with tool-call markup removed and whitespace tidied."""
    if not text:
        return ""
    out = _MARKUP.sub(" ", str(text))
    out = _STRAY_ARG_BREAK.sub(" ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if max_len and len(out) > max_len:
        out = out[:max_len].rstrip() + "…"
    return out


def looks_corrupted(text: str | None) -> bool:
    """True when text carries leaked markup — worth logging, not just cleaning.

    A leak means an argument the model intended to pass was probably swallowed,
    so the caller may be running on defaults without knowing it.
    """
    return bool(text) and bool(_MARKUP.search(str(text)))
