import re
from typing import Any

import pandas as pd


def raw_text_process_v1(text: Any) -> str:
    """Normalize raw text content for storage."""
    if text is None:
        return ""
    try:
        missing = pd.isna(text)
    except Exception:
        missing = False
    if isinstance(missing, bool) and missing:
        return ""

    if not isinstance(text, str):
        text = str(text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
