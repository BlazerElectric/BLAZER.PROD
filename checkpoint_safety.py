"""Protect Gemini workflows from accidental rebuilds when checkpoints are missing."""

import logging
import os


def require_checkpoint(path: str, allow_new: bool) -> None:
    if os.path.exists(path):
        return
    if not allow_new:
        raise FileNotFoundError(
            f"Checkpoint {path!r} is missing. Restore it from a trusted backup or "
            "pass --allow-new-checkpoint to start a new checkpoint; this may incur Gemini costs."
        )
    logging.getLogger(__name__).warning(
        "Starting a new checkpoint at %s; unprocessed products may incur Gemini costs.", path
    )
