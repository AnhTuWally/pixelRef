"""The words pixelRef uses to warn about plugins, scripts and packages.

Kept in one place so the Plugins window, the approval dialog, the Scripts menu
and ``docs/security.md`` say the same thing. The guide is the Help browser's
``guide:security`` page.
"""

from __future__ import annotations

GUIDE = "guide:security"

SHORT = ("Plugins and scripts run with the same access as you: they can read and change your files, "
         "use the network and start other programs. Only install them from people you trust.")

FIRST_TIME_TITLE = "Before you add plugins"
FIRST_TIME = (
    "Everything in pixelRef is a plugin, and third-party plugins are Python code that runs inside pixelRef "
    "with your permissions. A plugin can do anything you can: read your files (including your boards and "
    "anything else in your user folder), send them over the internet, or start other programs.\n\n"
    "pixelRef won't run a new or changed plugin until you approve it, checks its files every time it starts, "
    "and reports what third-party plugins do — but once approved, a plugin isn't sandboxed. "
    "Only approve plugins from people you trust."
)

ATTENTION = "{count} plugin{s} need{verb} your review before {pronoun} can run."

APPROVAL_RISK = (
    "Approving lets this plugin's code run inside pixelRef with your permissions: it can read and change "
    "your files, use the network and start programs. pixelRef can't sandbox it. Only approve it if you "
    "trust who made it and where you got it."
)
APPROVAL_UNSIGNED = ("It isn't signed by a publisher you trust, so pixelRef can't tell who made it: "
                     "the author named in its manifest is just what the plugin says about itself.")
APPROVAL_CHANGED = "Its files changed since you approved it. Approve only if you expected this update."
APPROVAL_INVALID = (
    "Its signature does NOT match its files: someone may have changed the plugin after it was signed. "
    "Don't approve it unless you know why."
)
APPROVAL_INVALID_CONFIRM = "I understand the signature is invalid and still trust this plugin"
APPROVAL_NO_PERMISSIONS = ("It declares no permissions. If it does more than it says (writes files, uses the "
                           "network…), the activity monitor will show it on its page.")

# What each declared permission lets a plugin do, in plain words.
PERMISSION_RISKS = {
    "network": ("Network", "connects to other computers — it could send your files or data elsewhere"),
    "subprocess": ("Start programs", "runs other programs, which aren't limited by anything pixelRef checks"),
    "native_code": ("Native code", "calls operating-system functions directly (ctypes), out of the monitor's sight"),
    "write_files": ("Write files", "creates or changes files outside pixelRef's own data"),
    "read_sensitive": ("Read sensitive files", "reads places that hold secrets: SSH keys, browser profiles, "
                                               "cloud credentials"),
    "dynamic_code": ("Run code from text", "builds and runs code at run time, which makes it hard to review"),
}

TRUST_HELP = {
    "builtin": "Part of pixelRef.",
    "verified": "Signed by a publisher on your trusted list, and its files match the signature.",
    "trusted": "You approved exactly these files. Any change needs approving again.",
    "session": "Its folder is set to trust everything (a developer folder or --trust-plugin-path).",
    "unverified": "Not reviewed: its code hasn't run. Review it before switching it on.",
    "changed": "Its files changed since you approved it: it won't run until you review it again.",
    "invalid": "Its signature doesn't match its files: it may have been tampered with.",
    "blocked": "It can't be used (see its page for why).",
}

ADD_SOURCE = (
    "Plugins in this folder will be listed in Window → Plugins…. New ones stay off until you approve them, "
    "but once approved they run with your permissions.\n\nOnly add folders whose contents you trust, and "
    "that other people can't change."
)
TRUST_ALL = (
    "Every plugin in this folder will run without asking — including ones added or changed later, by anyone "
    "who can write to the folder. Use this only for your own plugin development folder.\n\n"
    "Type the folder's name ({name}) to confirm:"
)
TRUST_ALL_BANNER = "A plugin folder is set to trust everything: {paths}"
MONITOR = ("Watch what third-party plugins do (activity monitor). It reports files written, network use, "
           "programs started and more on each plugin's page. It's a warning light, not a sandbox: a plugin "
           "determined to hide can get past it.")
MONITOR_NOTHING = "Nothing noticed so far (this doesn't mean the plugin is safe)."

PACKAGES = ("Python packages are code too: installing one runs its setup, and plugins that import it run its "
            "code. Install only packages you know, from the Python Package Index or a file you trust.")

SCRIPTS = ("Scripts run with full access to your computer. Only run scripts you wrote or got from someone "
           "you trust.")

REVIEW_TOAST = "{count} plugin{s} need{verb} your review"


def attention(count: int) -> str:
    one = count == 1
    return ATTENTION.format(count=count, s="" if one else "s", verb="s" if one else "", pronoun="it" if one else "they")


def review_toast(count: int) -> str:
    one = count == 1
    return REVIEW_TOAST.format(count=count, s="" if one else "s", verb="s" if one else "")
