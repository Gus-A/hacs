"""Mixin classes."""
# pylint: disable=too-few-public-methods
from __future__ import annotations
from typing import TYPE_CHECKING

from .share import get_hacs

if TYPE_CHECKING:
    from .hacsbase.hacs import Hacs


class HacsMixin:
    """Mixin to provide 'self.hacs' to classes."""

    hacs: Hacs = get_hacs()
