"""Exception types raised by the kernel."""

from __future__ import annotations


class SceneError(ValueError):
    """Invalid document operation (unknown node, bad attribute, cycle…)."""


class CommandError(RuntimeError):
    """A command could not run (unknown command, bad arguments…)."""


class PluginError(RuntimeError):
    """A plugin could not be discovered, resolved or activated."""
