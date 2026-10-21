"""The Plugins window: every plugin (search, filters, a details page, enable and
disable at runtime, review and approval), the folders plugins come from, and the
Python packages plugins need.

* **Plugins.** A searchable list (by name, author, tags, or anything a plugin
  provides — a command, tool or node type) beside a page about the selected
  plugin: who made it and how pixelRef knows (signature, approval), what it
  declares it does, what the activity monitor noticed it doing, other copies
  of it found in other folders, and its README.
* **Sources.** The folders pixelRef looks in, their trust policy, the trusted
  publishers and the activity monitor switch.
* **Python packages** is for people who have never used pip: plugins that are
  waiting for a package say so, one button installs what they need, and
  packages can be updated or removed without a terminal. The work runs in the
  background (:mod:`pixelref.ui.package_jobs`); pip's own log is one click away.
* **Safety** shows the "Staying safe" guide (``docs/security.md``) in place, so
  the window's "Learn more" links don't send the user to a Help window behind it.

Third-party plugins don't run until the user approves them in the approval
dialog, which says plainly what that means (:mod:`pixelref.ui.security_text`).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.packages import is_frozen, pip_available
from ..core.plugin_audit import KINDS as ACTIVITY_KINDS
from ..core.plugin_sources import POLICIES, POLICY_LABELS, folder_warnings
from ..core.plugins import TRUSTED
from . import security_text as text
from .package_jobs import package_runner

if TYPE_CHECKING:
    from .app import App

ERROR_COLOR = "#d27b7b"
WARN_COLOR = "#e0b341"
OK_COLOR = "#7bbf7b"
LINK_COLOR = "#6fb1ff"  # links on the dark theme
NOTICE_KEY = "plugins/risk_notice_seen"
DEVELOPER_KEY = "plugins/developer_tools"

TRUST_BADGES = {
    "builtin": "Built-in",
    "verified": "✔ Verified",
    "trusted": "● Approved",
    "session": "● Trusted folder",
    "unverified": "? Not reviewed",
    "changed": "⚠ Changed",
    "invalid": "✖ Invalid signature",
    "blocked": "✖ Blocked",
}
CHIPS = (("all", "All"), ("active", "Active"), ("off", "Off"), ("attention", "Needs attention"),
         ("builtin", "Built-in"), ("external", "Third-party"))
COLUMNS = ("Plugin", "Status", "Version", "Source", "Trust")


def security_guide_markdown() -> str:
    """``docs/security.md``, or the first-time notice when the guides aren't installed."""
    from ..core.docs import docs_folder

    folder = docs_folder()
    path = folder / "security.md" if folder is not None else None
    if path is not None and path.is_file():
        return path.read_text(encoding="utf-8")
    return f"# {text.FIRST_TIME_TITLE}\n\n{text.FIRST_TIME}"


class SafetyGuide(QTextBrowser):
    """The "Staying safe" guide, shown in place (the Safety tab, or over the approval dialog)."""

    def __init__(self, app: "App", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_link)
        self.setHtml(render_markdown(security_guide_markdown()))

    def _on_link(self, url: QUrl) -> None:
        target = url.toString()
        if target.startswith(("https://", "http://")):
            QDesktopServices.openUrl(url)
        elif target.endswith(".md") and self.app.session.commands.exists("help.docs"):
            self.app.run_command("help.docs", f"guide:{Path(target).stem}")  # another guide: the Help browser


def show_security_guide(app: "App", parent: QWidget | None = None) -> None:
    """The guide in its own window, for dialogs that sit over the Plugins window."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Staying safe with plugins and scripts")
    dialog.setStyleSheet(app.stylesheet)
    dialog.resize(720, 640)
    layout = QVBoxLayout(dialog)
    layout.addWidget(SafetyGuide(app, dialog), 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def status_text(info: dict[str, Any]) -> str:
    """The Status column: what the plugin is doing, in words."""
    status = info["status"]
    if info["broken"]:
        return "error"
    if info["foundation"]:
        return f"{status} (foundation)"
    if status == "untrusted" or (status == "disabled" and info["trust"] not in TRUSTED):
        return {"changed": "changed: review", "invalid": "invalid signature", "blocked": "blocked"}.get(
            info["trust"], "awaiting review" if info["needs_review"] else "off (not reviewed)")
    return {"disabled": "off", "inactive": "off", "discovered": "off"}.get(status, status)


def needs_attention(info: dict[str, Any]) -> bool:
    activity = info.get("activity") or {}
    return (info["status"] in ("error", "unavailable", "untrusted") or info["needs_review"] or info["broken"]
            or bool(info["conflicts"]) or bool(activity.get("violations")))


def _md(value: Any) -> str:
    """Plugin-supplied text, escaped for Markdown."""
    return re.sub(r"([\\`*_{}\[\]()#+\-!|<>~])", r"\\\1", str(value))


def _code(value: Any) -> str:
    """Plugin-supplied text as a Markdown code span (escapes don't work inside one)."""
    return "`" + str(value).replace("`", "'") + "`"


def _demote(markdown: str) -> str:
    return re.sub(r"(?m)^(#{1,4}) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", markdown)


def _link(url: str) -> str:
    return f"[{_md(url)}]({url})" if url.startswith(("https://", "http://")) else _md(url)


def render_markdown(markdown: str) -> str:
    """Markdown to HTML with raw HTML switched off (READMEs come from plugins)."""
    document = QTextDocument()
    features = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML
    document.setMarkdown(markdown, features)
    return document.toHtml().replace("color:#0000ff;", f"color:{LINK_COLOR};")  # Qt's link blue is unreadable on dark


# ---- The plugin list -------------------------------------------------------------------------------------------


class PluginModel(QAbstractTableModel):
    toggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    @staticmethod
    def checkable(info: dict[str, Any]) -> bool:
        return not info["foundation"] and not info["broken"] and info["trust"] != "blocked"

    @staticmethod
    def is_on(info: dict[str, Any]) -> bool:
        # An unavailable plugin is switched on: it runs once its packages are installed.
        return info["status"] == "active" or (info["status"] == "unavailable" and info["enabled"])

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        info = self.rows[index.row()]
        column = index.column()
        if role == Qt.UserRole:
            return info["id"]
        if role == Qt.DisplayRole:
            if column == 0:
                return ("⚠ " if info["conflicts"] else "") + info["name"]
            if column == 1:
                return status_text(info)
            if column == 2:
                return info["version"]
            if column == 3:
                return info["source"]
            violations = (info.get("activity") or {}).get("violations", 0)
            return TRUST_BADGES.get(info["trust"], info["trust"]) + (f"  ! {violations}" if violations else "")
        if role == Qt.CheckStateRole and column == 0 and self.checkable(info):
            return Qt.Checked if self.is_on(info) else Qt.Unchecked
        if role == Qt.ForegroundRole:
            if column == 1 and (info["status"] == "error" or info["broken"]):
                return QColor(ERROR_COLOR)
            if column in (1, 4) and (info["needs_review"] or info["trust"] in ("changed", "invalid", "blocked")
                                     or info["status"] == "unavailable"):
                return QColor(ERROR_COLOR if info["trust"] in ("invalid", "blocked") else WARN_COLOR)
            if column == 4 and info["trust"] == "verified":
                return QColor(OK_COLOR)
        if role == Qt.ToolTipRole:
            if column == 0:
                return f"{info['id']} · {info['path']}" + (
                    f"\n{len(info['conflicts'])} other cop{'y' if len(info['conflicts']) == 1 else 'ies'} found"
                    if info["conflicts"] else "")
            if column == 1:
                return info["error"] or info["description"]
            if column == 4:
                return text.TRUST_HELP.get(info["trust"], "") + (f"\n{info['trust_reason']}"
                                                                 if info["trust_reason"] else "")
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.isValid() and index.column() == 0 and self.checkable(self.rows[index.row()]):
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:  # noqa: N802
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        on = Qt.CheckState(value) == Qt.Checked if not isinstance(value, bool) else value
        self.toggled.emit(self.rows[index.row()]["id"], on)
        return False  # the window refreshes from the plugin manager


class PluginFilter(QSortFilterProxyModel):
    """Search words (all must match) and a filter chip; with a query, best matches first."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.words: list[str] = []
        self.chip = "all"
        self.haystacks: dict[str, str] = {}
        self.ranks: dict[str, int] = {}

    def set_query(self, words: list[str], ranks: dict[str, int]) -> None:
        self.words, self.ranks = words, ranks
        self.invalidate()

    def set_chip(self, chip: str) -> None:
        self.chip = chip
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        info = self.sourceModel().rows[row]
        chip = self.chip
        if chip == "active" and info["status"] != "active":
            return False
        if chip == "off" and info["status"] == "active":
            return False
        if chip == "attention" and not needs_attention(info):
            return False
        if chip == "builtin" and not info["builtin"]:
            return False
        if chip == "external" and info["builtin"]:
            return False
        if not self.words:
            return True
        haystack = self.haystacks.get(info["id"], "")
        return info["id"] in self.ranks or all(word in haystack for word in self.words)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        if self.words:
            rows = self.sourceModel().rows
            a, b = rows[left.row()]["id"], rows[right.row()]["id"]
            return self.ranks.get(a, 10**6) < self.ranks.get(b, 10**6)
        return left.row() < right.row()


# ---- Approval -------------------------------------------------------------------------------------------------


class ApprovalDialog(QDialog):
    """What approving a plugin means and what this one says about itself."""

    def __init__(self, app: "App", info: dict[str, Any], changed: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Approve “{info['name']}”?")
        self.setStyleSheet(app.stylesheet)
        self.resize(560, 520)
        self.allow_invalid = False
        layout = QVBoxLayout(self)
        risk = QLabel(text.APPROVAL_RISK, self)
        risk.setWordWrap(True)
        risk.setStyleSheet(f"color: {WARN_COLOR}; font-weight: bold;")
        layout.addWidget(risk)
        notes = []
        if info["trust"] == "changed":
            notes.append(text.APPROVAL_CHANGED)
        if info["trust"] == "invalid":
            notes.append(text.APPROVAL_INVALID)
        elif info["signature"]["status"] != "valid":
            notes.append(text.APPROVAL_UNSIGNED)
        for note in notes:
            label = QLabel(note, self)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {ERROR_COLOR};" if info["trust"] == "invalid" else "")
            layout.addWidget(label)
        self.details = QTextBrowser(self)
        self.details.setOpenLinks(False)
        self.details.setHtml(render_markdown(self._summary(info, changed)))
        layout.addWidget(self.details, 1)
        self.confirm_invalid: QCheckBox | None = None
        if info["trust"] == "invalid":
            self.confirm_invalid = QCheckBox(text.APPROVAL_INVALID_CONFIRM, self)
            layout.addWidget(self.confirm_invalid)
        learn = QLabel(f'<a href="guide" style="color: {LINK_COLOR};">Staying safe with plugins and scripts</a>', self)
        learn.linkActivated.connect(lambda _href: show_security_guide(app, self))
        layout.addWidget(learn)
        buttons = QDialogButtonBox(self)
        self.approve_button = buttons.addButton("Approve and switch on", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.Cancel)
        cancel.setDefault(True)  # nothing runs unless the user picks Approve
        cancel.setFocus()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self.confirm_invalid is not None:
            self.approve_button.setEnabled(False)
            self.confirm_invalid.toggled.connect(self.approve_button.setEnabled)

    def accept(self) -> None:
        self.allow_invalid = self.confirm_invalid is not None and self.confirm_invalid.isChecked()
        super().accept()

    @staticmethod
    def _summary(info: dict[str, Any], changed: list[str]) -> str:
        author = info["author"]
        who = author.get("name", "") or "not given"
        if author.get("organization"):
            who += f" ({author['organization']})"
        signature = info["signature"]
        signed = {"valid": f"signed by {signature['signer']} ({signature['key_id']})",
                  "unsigned": "not signed",
                  "invalid": f"INVALID — {signature['problem']}"}.get(signature["status"], signature["status"])
        lines = [f"**{_md(info['name'])}** {_code(info['id'])} version {_md(info['version'])}", "",
                 f"- **Author (as the plugin says):** {_md(who)}",
                 f"- **Signature:** {_md(signed)}",
                 f"- **Folder:** {_md(info['path'])}"]
        if info["homepage"] or info["repository"]:
            lines.append(f"- **Web:** {_link(info['homepage'] or info['repository'])}")
        if info["description"]:
            lines += ["", _md(info["description"])]
        lines += ["", "**It says it will:**", ""]
        if info["permissions"]:
            for key in info["permissions"]:
                label, risk = text.PERMISSION_RISKS.get(key, (key, ""))
                lines.append(f"- **{label}** — {risk}")
        else:
            lines.append(_md(text.APPROVAL_NO_PERMISSIONS))
        if info["python_requires"]:
            lines += ["", "**Python packages it needs** (installed from the internet when you ask):", ""]
            lines += [f"- {_md(req)}" for req in info["python_requires"]]
        if info["overrides"]:
            lines += ["", "**It replaces these from other plugins:** " + _md(", ".join(info["overrides"]))]
        if changed:
            lines += ["", "**Changed since you approved it:**", ""] + [f"- {_code(c)}" for c in changed[:40]]
        return "\n".join(lines)


def review_and_approve(app: "App", plugin_id: str, parent: QWidget | None = None) -> bool:
    """Show the approval dialog; on approval trust the plugin and switch it on."""
    plugins = app.session.plugins
    info = plugins.info(plugin_id)
    if info["trust"] == "blocked":
        QMessageBox.warning(parent, "Plugin blocked", f"“{info['name']}” can't be used: {info['trust_reason']}")
        return False
    dialog = ApprovalDialog(app, info, plugins.changed_files(plugin_id), parent)
    if dialog.exec() != QDialog.Accepted:
        return False
    app.run_command("plugin_trust", plugin_id, enable=True, allow_invalid=dialog.allow_invalid)
    return plugins.info(plugin_id)["trust"] in TRUSTED


# ---- The window -----------------------------------------------------------------------------------------------


class PluginManagerDialog(QDialog):
    def __init__(self, app: "App", parent: QWidget | None = None, query: str = "", show: str = "all") -> None:
        super().__init__(parent)
        self.app = app
        self.session = app.session
        self.runner = package_runner(app)
        self.setWindowTitle("Plugins")
        self.resize(1040, 680)
        self.setStyleSheet(app.stylesheet)
        self._filling = False
        self._infos: dict[str, dict[str, Any]] = {}
        self.current_id: str = ""
        layout = QVBoxLayout(self)
        self.restart_banner = QLabel("", self)
        self.restart_banner.setWordWrap(True)
        self.restart_banner.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold;")
        layout.addWidget(self.restart_banner)
        layout.addWidget(self._build_notice())
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")
        self.tabs.addTab(self._build_sources_tab(), "Sources")
        self.tabs.addTab(self._build_packages_tab(), "Python packages")
        self.safety = SafetyGuide(app, self)
        self.tabs.addTab(self.safety, "Safety")
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self._build_job_area())
        row = QHBoxLayout()
        self.developer = QCheckBox("Developer tools", self)
        self.developer.setToolTip("Show Reload (re-import a plugin's code after editing it)")
        self.developer.setChecked(bool(self.session.settings.value(DEVELOPER_KEY, False, type=bool)))
        self.developer.toggled.connect(self._on_developer)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        row.addWidget(self.developer)
        row.addStretch()
        row.addWidget(close_button)
        layout.addLayout(row)
        self.runner.started.connect(self._on_job_started)
        self.runner.output.connect(self._on_job_output)
        self.runner.finished.connect(self._on_job_finished)
        self._unwatch = self.session.plugins.watch(self._on_plugins_changed)
        self.set_chip(show)
        self.search.setText(query)
        self.refresh()
        if self.runner.busy:
            self._on_job_started(self.runner.job.description if self.runner.job else "")
            for line in self.runner.log:
                self.log.appendPlainText(line)
        self.search.setFocus()

    def done(self, result: int) -> None:  # noqa: D401  (also runs for accept/reject)
        for signal, slot in ((self.runner.started, self._on_job_started), (self.runner.output, self._on_job_output),
                             (self.runner.finished, self._on_job_finished)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._unwatch.dispose()
        super().done(result)

    def _on_plugins_changed(self) -> None:
        if not self._filling:
            self.refresh()

    # ---- The first-time notice and the banner ------------------------------------------------------

    def _build_notice(self) -> QWidget:
        self.notice = QFrame(self)
        self.notice.setFrameShape(QFrame.StyledPanel)
        notice_layout = QVBoxLayout(self.notice)
        title = QLabel(f"<b>{text.FIRST_TIME_TITLE}</b>", self.notice)
        body = QLabel(text.FIRST_TIME, self.notice)
        body.setWordWrap(True)
        row = QHBoxLayout()
        learn = QLabel(f'<a href="guide" style="color: {LINK_COLOR};">Read "Staying safe with plugins and scripts"</a>',
                       self.notice)
        learn.linkActivated.connect(lambda _href: self.show_safety())
        got_it = QPushButton("Got it", self.notice)
        got_it.clicked.connect(self.dismiss_notice)
        row.addWidget(learn)
        row.addStretch()
        row.addWidget(got_it)
        notice_layout.addWidget(title)
        notice_layout.addWidget(body)
        notice_layout.addLayout(row)
        self.notice.setVisible(not self.session.settings.value(NOTICE_KEY, False, type=bool))
        return self.notice

    def show_safety(self) -> None:
        self.tabs.setCurrentWidget(self.safety)

    def dismiss_notice(self) -> None:
        self.session.settings.setValue(NOTICE_KEY, True)
        self.notice.setVisible(False)

    def _update_risk_banner(self) -> None:
        review = [info for info in self._infos.values() if info["needs_review"]]
        trust_all = [s["path"] for s in self.session.plugins.source_info()
                     if s["policy"] == "trust_all" and s["kind"] != "builtin" and s["enabled"]]
        learn = f' <a href="guide" style="color: {LINK_COLOR};">Learn more</a>'
        if review or trust_all:
            parts = [text.attention(len(review))] if review else []
            if trust_all:
                parts.append(text.TRUST_ALL_BANNER.format(paths=", ".join(trust_all)))
            self.risk_banner.setText(" ".join(parts) + learn)
            self.risk_banner.setStyleSheet(f"color: {WARN_COLOR}; font-weight: bold;")
        else:
            self.risk_banner.setText(text.SHORT + learn)
            self.risk_banner.setStyleSheet("")

    # ---- Plugins tab -----------------------------------------------------------------------------

    def _build_plugins_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.risk_banner = QLabel("", page)
        self.risk_banner.setWordWrap(True)
        self.risk_banner.linkActivated.connect(lambda _href: self.show_safety())
        layout.addWidget(self.risk_banner)
        top = QHBoxLayout()
        self.search = QLineEdit(page)
        self.search.setPlaceholderText("Search plugins: name, author, tag, or something one provides (a command, tool…)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)
        layout.addLayout(top)
        chips = QHBoxLayout()
        self.chip_group = QButtonGroup(page)
        self.chip_group.setExclusive(True)
        self.chip_buttons: dict[str, QPushButton] = {}
        for key, label in CHIPS:
            button = QPushButton(label, page)
            button.setCheckable(True)
            button.setProperty("chip", key)
            self.chip_group.addButton(button)
            self.chip_buttons[key] = button
            chips.addWidget(button)
        self.chip_group.buttonClicked.connect(lambda b: self.set_chip(b.property("chip")))
        chips.addStretch()
        layout.addLayout(chips)
        splitter = QSplitter(Qt.Horizontal, page)
        self.model = PluginModel(page)
        self.model.toggled.connect(self._on_toggled)
        self.proxy = PluginFilter(page)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView(splitter)
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.selectionModel().currentRowChanged.connect(lambda *_a: self._show_current())
        self.table.activated.connect(lambda _i: self.details.setFocus())
        details = QWidget(splitter)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.details = QTextBrowser(details)
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._on_link)
        details_layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        self.enable_box = QCheckBox("Switched on", details)
        self.enable_box.clicked.connect(lambda checked: self._on_toggled(self.current_id, checked))
        self.trust_button = QPushButton("Review…", details)
        self.trust_button.clicked.connect(self._on_trust_button)
        self.folder_button = QPushButton("Open folder", details)
        self.folder_button.clicked.connect(self._open_plugin_folder)
        self.help_button = QPushButton("Open in Help", details)
        self.help_button.clicked.connect(lambda: self.app.run_command("help.docs", f"plugin:{self.current_id}"))
        self.reload_button = QPushButton("Reload", details)
        self.reload_button.setToolTip("Re-import the plugin's code (its files are checked again first)")
        self.reload_button.clicked.connect(self._reload_selected)
        self.clear_activity_button = QPushButton("Clear activity", details)
        self.clear_activity_button.clicked.connect(self._clear_activity)
        for widget in (self.enable_box, self.trust_button, self.folder_button, self.help_button,
                       self.clear_activity_button, self.reload_button):
            buttons.addWidget(widget)
        buttons.addStretch()
        details_layout.addLayout(buttons)
        splitter.addWidget(self.table)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([560, 700])
        layout.addWidget(splitter, 1)
        needed_row = QHBoxLayout()
        self.needed_label = QLabel("", page)
        self.needed_label.setWordWrap(True)
        self.install_needed_button = QPushButton("Install what's needed", page)
        self.install_needed_button.setToolTip("Download and install the Python packages these plugins need")
        self.install_needed_button.clicked.connect(self._install_needed)
        needed_row.addWidget(self.needed_label, 1)
        needed_row.addWidget(self.install_needed_button)
        layout.addLayout(needed_row)
        self.conflicts = QLabel("", page)
        self.conflicts.setWordWrap(True)
        layout.addWidget(self.conflicts)
        return page

    def set_chip(self, chip: str) -> None:
        chip = chip if chip in self.chip_buttons else "all"
        self.chip_buttons[chip].setChecked(True)
        self.proxy.set_chip(chip)
        self._select_first_if_needed()

    def refresh(self) -> None:
        self._fill_plugins()
        self._fill_sources()
        self._fill_packages()
        self._update_banner()
        self._update_buttons()

    def _fill_plugins(self) -> None:
        self._filling = True
        try:
            infos = self.session.plugins.list_plugins()
            self._infos = {info["id"]: info for info in infos}
            self.proxy.haystacks = {info["id"]: self._haystack(info) for info in infos}
            self.model.set_rows(infos)
            self._on_search(self.search.text())
            self._select(self.current_id)
        finally:
            self._filling = False
        self._update_risk_banner()
        needed = self.session.plugins.packages_needed()
        if needed:
            plugins = sorted({p for ids in needed.values() for p in ids})
            self.needed_label.setText(
                f"{len(plugins)} plugin{'s are' if len(plugins) > 1 else ' is'} waiting for Python packages: "
                + ", ".join(sorted(needed)))
        else:
            self.needed_label.setText("")
        self.install_needed_button.setVisible(bool(needed))
        conflicts = self.app.keymap.conflicts()
        if conflicts:
            lines = [f"{key}: {', '.join(targets)}" for key, targets in conflicts]
            self.conflicts.setText("Shortcut conflicts (the last registered wins):\n" + "\n".join(lines))
        else:
            self.conflicts.setText("No shortcut conflicts.")

    def _haystack(self, info: dict[str, Any]) -> str:
        author = info["author"]
        commands = [c.name for c in self.session.commands.commands() if c.plugin == info["id"]]
        parts = [info["id"], info["name"], info["description"], author.get("name", ""),
                 author.get("organization", ""), info["category"], " ".join(info["tags"]), info["source"],
                 info["trust_label"], status_text(info), " ".join(commands)]
        return " ".join(parts).lower()

    def _on_search(self, query: str) -> None:
        words = [w.lower() for w in query.split()]
        ranks: dict[str, int] = {}
        if words:
            # Plugins providing something that matches (a command, tool, node type, guide…) too.
            try:
                for entry in self.session.docs.search(query, limit=200):
                    owner = entry.name if entry.kind == "plugin" else entry.plugin
                    if owner and owner not in ranks and owner in self._infos:
                        ranks[owner] = len(ranks)
            except Exception:  # noqa: BLE001  (search help, never a reason to fail)
                ranks = {}
        self.proxy.set_query(words, ranks)
        self.proxy.sort(0 if words else -1)
        self._select_first_if_needed()

    def visible_ids(self) -> list[str]:
        return [self.proxy.index(row, 0).data(Qt.UserRole) for row in range(self.proxy.rowCount())]

    def _select(self, plugin_id: str) -> None:
        for row in range(self.proxy.rowCount()):
            if self.proxy.index(row, 0).data(Qt.UserRole) == plugin_id:
                self.table.selectRow(row)
                self._show_current()
                return
        self._select_first_if_needed(force=True)

    def select_plugin(self, plugin_id: str) -> None:
        self._select(plugin_id)

    def _select_first_if_needed(self, force: bool = False) -> None:
        if self.proxy.rowCount() == 0:
            self.current_id = ""
            self._show_current()
            return
        if force or self.current_id not in self.visible_ids():
            self.table.selectRow(0)
            self._show_current()

    def _show_current(self) -> None:
        index = self.table.currentIndex()
        plugin_id = index.siblingAtColumn(0).data(Qt.UserRole) if index.isValid() else ""
        self.current_id = plugin_id or ""
        info = self._infos.get(self.current_id)
        has = info is not None
        for widget in (self.enable_box, self.trust_button, self.folder_button, self.help_button):
            widget.setVisible(has)
        self.reload_button.setVisible(has and self.developer.isChecked())
        self.clear_activity_button.setVisible(has and not info["builtin"] and bool(info.get("activity", {})
                                                                                   .get("counts")))
        if info is None:
            self.details.setHtml("<p>No plugin matches.</p>")
            return
        self.enable_box.blockSignals(True)
        self.enable_box.setChecked(PluginModel.is_on(info))
        self.enable_box.setEnabled(PluginModel.checkable(info))
        self.enable_box.blockSignals(False)
        trust = info["trust"]
        if info["builtin"] or trust in ("verified", "session"):
            self.trust_button.setVisible(False)
        elif trust == "trusted":
            self.trust_button.setText("Revoke approval")
            self.trust_button.setEnabled(True)
        else:
            self.trust_button.setText("Review and approve…")
            self.trust_button.setEnabled(trust != "blocked")
        self.details.setHtml(render_markdown(self.details_markdown(info)))

    # ---- The details page ------------------------------------------------------------------------

    def details_markdown(self, info: dict[str, Any]) -> str:
        plugins = self.session.plugins
        lines = [f"## {_md(info['name'])}", "",
                 f"{_code(info['id'])} · version {_md(info['version'])} · {_md(status_text(info))}"]
        if info["description"]:
            lines += ["", _md(info["description"])]
        lines += ["", f"**{_md(TRUST_BADGES.get(info['trust'], info['trust']))}** — "
                      f"{_md(text.TRUST_HELP.get(info['trust'], ''))}"]
        if info["trust_reason"] and not info["builtin"]:
            lines += ["", f"Why: {_md(info['trust_reason'])}"]
        if info["error"]:
            lines += ["", f"**Problem:** {_md(info['error'])}"]
        if info["broken"]:
            return "\n".join(lines)
        # Origin and authenticity.
        author = info["author"]
        who = author.get("name", "") or "not given"
        if author.get("organization"):
            who += f" ({author['organization']})"
        if author.get("email"):
            who += f" <{author['email']}>"
        lines += ["", "### Origin and authenticity", ""]
        lines.append(f"- **Author:** {_md(who)}" + ("" if info["builtin"] else " (as the plugin says)"))
        if not info["builtin"]:
            signature = info["signature"]
            if signature["status"] == "valid":
                publisher = self.session.plugins.trust.publisher(signature["key_id"])
                state = "a trusted publisher" if publisher else "not one of your trusted publishers"
                lines.append(f"- **Signature:** valid, by {_md(signature['signer'])} ({_md(signature['key_id'])}), "
                             f"{state}")
            elif signature["status"] == "invalid":
                lines.append(f"- **Signature:** INVALID — {_md(signature['problem'])}")
            else:
                lines.append("- **Signature:** not signed")
        for key, label in (("license", "License"), ("homepage", "Homepage"), ("repository", "Repository"),
                           ("issues", "Issues")):
            if info[key]:
                lines.append(f"- **{label}:** {_link(info[key]) if key != 'license' else _md(info[key])}")
        lines.append(f"- **Source:** {_md(info['source'])}" + (f" — {_md(info['source_path'])}"
                                                                if not info["builtin"] else ""))
        if not info["builtin"]:
            lines.append(f"- **Folder:** {_md(info['path'])}")
            lines.append(f"- **Content hash:** `{info['content_hash'][:23]}…`")
            if info["publish_missing"]:
                lines.append("- **Missing from its manifest:** " + _md(", ".join(info["publish_missing"])))
        # Other copies.
        if info["conflicts"]:
            lines += ["", "### Other copies found", "",
                      f"- **In use:** {_md(info['path'])} — version {_md(info['version'])}, {_md(info['source'])}, "
                      f"{_md(info['trust_label'])}"]
            for index, other in enumerate(info["conflicts"]):
                entry = (f"{_md(other['path'])} — version {_md(other['version'])}, {_md(other['source'])}, "
                         f"{_md(other['trust_label'])}")
                if other["broken"]:
                    lines.append(f"- {entry} — can't be read: {_md(other['broken'])}")
                elif other["blocked"]:
                    lines.append(f"- {entry} — {_md(other['blocked'])}")
                else:
                    lines.append(f"- {entry} — [Use this copy](resolve:{index})")
        # What it says it does.
        if not info["builtin"]:
            lines += ["", "### Permissions it declares", ""]
            if info["permissions"]:
                for key in info["permissions"]:
                    label, risk = text.PERMISSION_RISKS.get(key, (key, ""))
                    lines.append(f"- **{label}** — {_md(risk)}")
            else:
                lines.append("None.")
            if info["overrides"]:
                lines.append("- **Replaces:** " + _md(", ".join(info["overrides"])))
        # What it provides.
        provides = self._provides(info["id"])
        if provides:
            lines += ["", "### Provides", ""] + provides
        needs = []
        if info["requires"]:
            needs.append("- **Plugins:** " + ", ".join(f"[{_md(r)}](plugin:{r})" for r in info["requires"]))
        if info["python_requires"]:
            missing = set(info["missing_packages"])
            needs.append("- **Python packages:** " + ", ".join(
                _md(r) + (" (missing)" if r in missing else "") for r in info["python_requires"]))
        if needs:
            lines += ["", "### Needs", ""] + needs
        # What the monitor noticed.
        if not info["builtin"]:
            lines += ["", "### Activity", ""] + self._activity_lines(info)
        if info["warnings"]:
            lines += ["", "### Warnings", ""] + [f"- {_md(w)}" for w in info["warnings"]]
        if not info["builtin"]:
            readme = plugins.readme(info["id"])
            if readme:
                lines += ["", "### About", "", _demote(readme)]
        return "\n".join(lines)

    def _provides(self, plugin_id: str) -> list[str]:
        out = []
        commands = sorted(c.name for c in self.session.commands.commands() if c.plugin == plugin_id)
        if commands:
            shown = ", ".join(f"{_code(c)}" for c in commands[:25])
            out.append(f"- **Commands:** {shown}" + (f" and {len(commands) - 25} more" if len(commands) > 25 else ""))
        types = self.session.types
        node_types = sorted(n for n in types.names(include_abstract=True)
                            if getattr(types.get(n), "plugin", None) == plugin_id)
        if node_types:
            out.append("- **Node types:** " + ", ".join(f"{_code(n)}" for n in node_types))
        actions = self.app.actions.specs
        tools = sorted(spec.text.replace("&&", "&") for key, spec in actions.items()
                       if spec.plugin == plugin_id and key.startswith("tool."))
        if tools:
            out.append("- **Tools:** " + _md(", ".join(tools)))
        mine = {key for key, spec in actions.items() if spec.plugin == plugin_id}
        clashes = [(key, targets) for key, targets in self.app.keymap.conflicts() if mine & set(targets)]
        if clashes:
            out.append("- **Shortcut conflicts:** " + _md("; ".join(f"{k}: {', '.join(t)}" for k, t in clashes)))
        return out

    def _activity_lines(self, info: dict[str, Any]) -> list[str]:
        monitor = self.session.plugins.monitor
        if not monitor.enabled:
            return ["The activity monitor is off (Sources tab)."]
        activity = info.get("activity") or {}
        counts = activity.get("counts") or {}
        if not counts:
            return [_md(text.MONITOR_NOTHING) if info["status"] == "active" else "It hasn't run in this session."]
        lines = [f"- {_md(ACTIVITY_KINDS.get(kind, kind))}: {count}×" for kind, count in sorted(counts.items())]
        violations = activity.get("violations", 0)
        if violations:
            lines.insert(0, f"**pixelRef noticed it doing {violations} thing{'s' if violations > 1 else ''} its "
                            "manifest doesn't declare:**")
            lines.insert(1, "")
        entries = [e for e in monitor.entries(info["id"]) if e.violation][-12:] or monitor.entries(info["id"])[-6:]
        if entries:
            lines += ["", "Most recent:", ""]
            for entry in reversed(entries):
                when = datetime.datetime.fromtimestamp(entry.time).strftime("%H:%M:%S")
                where = f"{Path(entry.file).name}:{entry.line}"
                note = " (through pixelRef)" if entry.via_pixelref else ("" if entry.declared else " — not declared")
                lines.append(f"- {when} {_md(ACTIVITY_KINDS.get(entry.kind, entry.kind))}: {_code(entry.detail)} "
                             f"({_md(where)}){note}")
        return lines

    def _on_link(self, url: QUrl) -> None:
        target = url.toString()
        if target.startswith("resolve:"):
            info = self._infos.get(self.current_id)
            index = int(target.split(":", 1)[1])
            if info is None or index >= len(info["conflicts"]):
                return
            other = info["conflicts"][index]
            if other["builtin"] is False and info["builtin"]:
                answer = QMessageBox.warning(
                    self, "Replace a built-in plugin?",
                    f"This replaces pixelRef's own “{info['name']}” with the copy in {other['path']}. "
                    "It still needs your approval before it runs.", QMessageBox.Ok | QMessageBox.Cancel,
                    QMessageBox.Cancel)
                if answer != QMessageBox.Ok:
                    return
            self.app.run_command("plugin_resolve", self.current_id, other["path"])
            self.refresh()
        elif target.startswith("plugin:"):
            self.search.clear()
            self.set_chip("all")
            self._select(target.split(":", 1)[1])
        elif target.startswith(("https://", "http://")):
            QDesktopServices.openUrl(url)

    # ---- Actions ---------------------------------------------------------------------------------

    def _on_toggled(self, plugin_id: str, on: bool) -> None:
        info = self._infos.get(plugin_id)
        if info is None:
            return
        if on and info["trust"] not in TRUSTED:
            review_and_approve(self.app, plugin_id, self)
        else:
            self.app.run_command("load_plugin" if on else "unload_plugin", plugin_id)
        self.current_id = plugin_id
        self.refresh()

    def _on_trust_button(self) -> None:
        info = self._infos.get(self.current_id)
        if info is None:
            return
        if info["trust"] == "trusted":
            answer = QMessageBox.question(self, "Revoke approval",
                                          f"Switch “{info['name']}” off and forget that you approved it?")
            if answer == QMessageBox.Yes:
                self.app.run_command("plugin_revoke_trust", self.current_id)
        else:
            review_and_approve(self.app, self.current_id, self)
        self.refresh()

    def _open_plugin_folder(self) -> None:
        info = self._infos.get(self.current_id)
        if info and info["path"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(info["path"]))

    def _clear_activity(self) -> None:
        self.app.run_command("clear_plugin_activity", self.current_id)
        self.refresh()

    def _on_developer(self, on: bool) -> None:
        self.session.settings.setValue(DEVELOPER_KEY, bool(on))
        self._show_current()

    def _reload_selected(self) -> None:
        if self.current_id:
            self.app.run_command("reload_plugin", self.current_id)
            self.refresh()

    def _install_needed(self) -> None:
        needed = self.session.plugins.packages_needed()
        if not needed or not self._pip_ready():
            return
        plugins = sorted({p for ids in needed.values() for p in ids})
        self.runner.start(lambda: self.session.packages.plan_install(list(needed), plugins=plugins))

    # ---- Sources tab ----------------------------------------------------------------------------

    def _build_sources_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = QLabel("Folders pixelRef looks in for plugins, in order: when two hold a plugin with the same id, "
                       "the earlier one is used (pick another copy on the plugin's page). New plugins stay off "
                       "until you approve them, unless the folder's policy says otherwise.", page)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.sources_tree = QTreeWidget(page)
        self.sources_tree.setColumnCount(5)
        self.sources_tree.setHeaderLabels(["Folder", "Kind", "Policy", "Plugins", "Notes"])
        self.sources_tree.setRootIsDecorated(False)
        self.sources_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.sources_tree.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.sources_tree.itemChanged.connect(self._on_source_item_changed)
        self.sources_tree.itemSelectionChanged.connect(self._update_source_buttons)
        layout.addWidget(self.sources_tree, 1)
        row = QHBoxLayout()
        self.add_source_button = QPushButton("Add folder…", page)
        self.add_source_button.clicked.connect(self._add_source)
        self.remove_source_button = QPushButton("Remove", page)
        self.remove_source_button.clicked.connect(self._remove_source)
        self.up_button = QPushButton("Move up", page)
        self.up_button.clicked.connect(lambda: self._move_source(-1))
        self.down_button = QPushButton("Move down", page)
        self.down_button.clicked.connect(lambda: self._move_source(1))
        self.policy_combo = QComboBox(page)
        for policy in POLICIES:
            self.policy_combo.addItem(POLICY_LABELS[policy], policy)
        self.policy_combo.activated.connect(self._on_policy)
        rescan = QPushButton("Rescan", page)
        rescan.setToolTip("Look in every folder again")
        rescan.clicked.connect(lambda: (self.app.run_command("rescan_plugins"), self.refresh()))
        for widget in (self.add_source_button, self.remove_source_button, self.up_button, self.down_button):
            row.addWidget(widget)
        row.addWidget(QLabel("Policy:", page))
        row.addWidget(self.policy_combo)
        row.addStretch()
        row.addWidget(rescan)
        layout.addLayout(row)
        self.source_warning = QLabel("", page)
        self.source_warning.setWordWrap(True)
        self.source_warning.setStyleSheet(f"color: {WARN_COLOR};")
        layout.addWidget(self.source_warning)
        publishers_label = QLabel("<b>Trusted publishers</b> — plugins they sign (with files matching the "
                                  "signature) load without asking.", page)
        publishers_label.setWordWrap(True)
        layout.addWidget(publishers_label)
        self.publishers_tree = QTreeWidget(page)
        self.publishers_tree.setColumnCount(3)
        self.publishers_tree.setHeaderLabels(["Publisher", "Key id", "Added"])
        self.publishers_tree.setRootIsDecorated(False)
        self.publishers_tree.setMaximumHeight(120)
        self.publishers_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.publishers_tree)
        publisher_row = QHBoxLayout()
        add_publisher = QPushButton("Add publisher key…", page)
        add_publisher.setToolTip("A .pub.toml file the publisher gave you (python -m pixelref plugin keygen)")
        add_publisher.clicked.connect(self._add_publisher)
        remove_publisher = QPushButton("Remove", page)
        remove_publisher.clicked.connect(self._remove_publisher)
        publisher_row.addWidget(add_publisher)
        publisher_row.addWidget(remove_publisher)
        publisher_row.addStretch()
        layout.addLayout(publisher_row)
        self.monitor_box = QCheckBox("Activity monitor", page)
        self.monitor_box.toggled.connect(lambda on: self.app.run_command("plugin_monitor", on))
        monitor_label = QLabel(text.MONITOR, page)
        monitor_label.setWordWrap(True)
        layout.addWidget(self.monitor_box)
        layout.addWidget(monitor_label)
        return page

    def _fill_sources(self) -> None:
        self._filling = True
        try:
            selected = self._selected_source()
            self.sources_tree.clear()
            for info in self.session.plugins.source_info():
                notes = list(info["warnings"])
                if not info["enabled"]:
                    notes.insert(0, "switched off")
                item = QTreeWidgetItem([info["path"], info["title"], POLICY_LABELS.get(info["policy"], info["policy"]),
                                        str(info["plugins"]), " ".join(notes)])
                item.setData(0, Qt.UserRole, info)
                if info["editable"]:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked if info["enabled"] else Qt.Unchecked)
                if info["warnings"] or info["policy"] == "trust_all" and info["kind"] != "builtin":
                    item.setForeground(4, QColor(WARN_COLOR))
                    item.setForeground(2, QColor(WARN_COLOR) if info["policy"] == "trust_all" else item.foreground(2))
                self.sources_tree.addTopLevelItem(item)
                if selected and info["path"] == selected["path"]:
                    item.setSelected(True)
            self.publishers_tree.clear()
            for publisher in self.session.plugins.trust.publishers():
                item = QTreeWidgetItem([publisher.get("name", ""), publisher["key_id"],
                                        publisher.get("added", "")[:10]])
                item.setData(0, Qt.UserRole, publisher["key_id"])
                self.publishers_tree.addTopLevelItem(item)
            self.monitor_box.blockSignals(True)
            self.monitor_box.setChecked(self.session.plugins.monitor.enabled)
            self.monitor_box.blockSignals(False)
        finally:
            self._filling = False
        self._update_source_buttons()

    def _selected_source(self) -> dict[str, Any] | None:
        items = self.sources_tree.selectedItems() if hasattr(self, "sources_tree") else []
        return items[0].data(0, Qt.UserRole) if items else None

    def _update_source_buttons(self) -> None:
        info = self._selected_source()
        editable = bool(info and info["editable"])
        self.remove_source_button.setEnabled(bool(info and info["kind"] == "added"))
        self.up_button.setEnabled(bool(info and info["kind"] == "added"))
        self.down_button.setEnabled(bool(info and info["kind"] == "added"))
        self.policy_combo.setEnabled(editable)
        if info is not None:
            self.policy_combo.setCurrentIndex(max(0, self.policy_combo.findData(info["policy"])))
        self.source_warning.setText(" ".join(info["warnings"]) if info else "")

    def _on_source_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        info = item.data(0, Qt.UserRole)
        self.app.run_command("set_plugin_source", info["path"], enabled=item.checkState(0) == Qt.Checked)
        self.refresh()

    def _add_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a plugin folder", str(Path.home()))
        if folder:
            self.add_source(folder)

    def add_source(self, folder: str, confirm: bool = True) -> None:
        warnings = folder_warnings(Path(folder))
        message = text.ADD_SOURCE + ("\n\n" + "\n".join(warnings) if warnings else "")
        if confirm and QMessageBox.question(self, "Add a plugin folder", message,
                                            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Ok:
            return
        self.app.run_command("add_plugin_source", folder)
        self.refresh()
        self.tabs.setCurrentIndex(0)
        self.set_chip("attention")

    def _remove_source(self) -> None:
        info = self._selected_source()
        if info and info["kind"] == "added":
            self.app.run_command("remove_plugin_source", info["path"])
            self.refresh()

    def _move_source(self, offset: int) -> None:
        info = self._selected_source()
        if info and info["kind"] == "added":
            self.app.run_command("move_plugin_source", info["path"], offset)
            self.refresh()

    def _on_policy(self, _index: int) -> None:
        info = self._selected_source()
        policy = self.policy_combo.currentData()
        if not info or not info["editable"] or policy == info["policy"]:
            return
        if policy == "trust_all" and not self.confirm_trust_all(info["path"]):
            self._update_source_buttons()
            return
        self.app.run_command("set_plugin_source", info["path"], policy=policy)
        self.refresh()

    def confirm_trust_all(self, path: str) -> bool:
        name = Path(path).name
        typed, ok = QInputDialog.getText(self, "Trust everything in this folder?", text.TRUST_ALL.format(name=name))
        return bool(ok and typed.strip() == name)

    def _add_publisher(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Add a trusted publisher", str(Path.home()),
                                                    "Publisher keys (*.pub.toml)")
        if not path:
            return
        from ..core.plugin_trust import read_public_key

        try:
            key = read_public_key(Path(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Not a publisher key", str(exc))
            return
        answer = QMessageBox.question(
            self, "Trust this publisher?",
            f"Plugins signed by “{key['name']}” ({key['key_id']}) will run without asking.\n\n"
            "Only add keys you got directly from the publisher (for example from your studio's admin).",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Ok:
            self.app.run_command("add_trusted_publisher", path)
            self.refresh()

    def _remove_publisher(self) -> None:
        items = self.publishers_tree.selectedItems()
        if items:
            self.app.run_command("remove_trusted_publisher", items[0].data(0, Qt.UserRole))
            self.refresh()

    # ---- Packages tab ----------------------------------------------------------------------------

    def _build_packages_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = QLabel("Some plugins need extra Python packages. pixelRef keeps them in its own folder, "
                       "separate from any Python installed on this computer.", page)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.packages_tree = QTreeWidget(page)
        self.packages_tree.setColumnCount(4)
        self.packages_tree.setHeaderLabels(["Package", "Version", "Needed by", "Notes"])
        self.packages_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.packages_tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.packages_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.packages_tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.packages_tree, 1)
        buttons = QHBoxLayout()
        self.update_button = QPushButton("Update", page)
        self.update_button.setToolTip("Install the newest versions of the selected packages")
        self.update_button.clicked.connect(self._update_selected)
        self.update_all_button = QPushButton("Update all", page)
        self.update_all_button.clicked.connect(self._update_all)
        self.remove_button = QPushButton("Remove", page)
        self.remove_button.clicked.connect(self._remove_selected)
        self.file_button = QPushButton("Install from file…", page)
        self.file_button.setToolTip("Install a downloaded .whl file (for computers without internet).\n" + text.PACKAGES)
        self.file_button.clicked.connect(self._install_file)
        open_button = QPushButton("Open folder", page)
        open_button.clicked.connect(self._open_folder)
        for button in (self.update_button, self.update_all_button, self.remove_button, self.file_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(open_button)
        layout.addLayout(buttons)
        # Installing any package by name is for people who know what they want.
        self.advanced = QCheckBox("Advanced: install a package by name", page)
        layout.addWidget(self.advanced)
        self.advanced_row = QWidget(page)
        advanced_layout = QVBoxLayout(self.advanced_row)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        warning = QLabel(text.PACKAGES, self.advanced_row)
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {WARN_COLOR};")
        advanced_layout.addWidget(warning)
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit(self.advanced_row)
        self.name_edit.setPlaceholderText("One package, e.g. numpy  or  numpy>=1.26")
        self.name_edit.returnPressed.connect(self._install_by_name)
        install_button = QPushButton("Install", self.advanced_row)
        install_button.clicked.connect(self._install_by_name)
        self.name_install_button = install_button
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(install_button)
        advanced_layout.addLayout(name_row)
        self.advanced_row.setVisible(False)
        self.advanced.toggled.connect(self.advanced_row.setVisible)
        layout.addWidget(self.advanced_row)
        self.folder_label = QLabel("", page)
        self.folder_label.setObjectName("pathLabel")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.folder_label)
        return page

    def _fill_packages(self) -> None:
        selected = {item.data(0, Qt.UserRole) for item in self.packages_tree.selectedItems()}
        self.packages_tree.clear()
        names = {plugin_id: info["name"] for plugin_id, info in self._infos.items()}
        for info in self.session.run("packages.list"):
            notes = []
            if not info["chosen"]:
                notes.append("installed for another package")
            if info["file"]:
                notes.append(f"from {info['file']}")
            if info["chosen"] and not info["version"]:
                notes.append("not installed yet" if not self.session.packages.restart_needed else "after restart")
            needed_by = ", ".join(names.get(p, p) for p in info["needed_by"])
            item = QTreeWidgetItem([info["name"], info["version"] or "—", needed_by, "; ".join(notes)])
            item.setData(0, Qt.UserRole, info["name"])
            item.setData(1, Qt.UserRole, info["chosen"])
            self.packages_tree.addTopLevelItem(item)
            item.setSelected(info["name"] in selected)
        packages = self.session.packages
        self.folder_label.setText(f"Folder: {packages.site}  ·  Python {packages.tag}")

    def _selected_packages(self, chosen_only: bool = True) -> list[str]:
        return [item.data(0, Qt.UserRole) for item in self.packages_tree.selectedItems()
                if item.data(1, Qt.UserRole) or not chosen_only]

    def _update_buttons(self) -> None:
        busy = self.runner.busy
        chosen = bool(self._selected_packages())
        self.update_button.setEnabled(chosen and not busy)
        self.remove_button.setEnabled(chosen and not busy)
        self.update_all_button.setEnabled(bool(self.session.packages.wanted()) and not busy)
        self.file_button.setEnabled(not busy)
        self.name_install_button.setEnabled(not busy)
        self.install_needed_button.setEnabled(not busy)

    def _update_selected(self) -> None:
        names = self._selected_packages()
        if names and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_update(names))

    def _update_all(self) -> None:
        if self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_update())

    def _remove_selected(self) -> None:
        names = self._selected_packages()
        if names and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_remove(names))

    def _install_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Install a Python package file", str(Path.home()),
                                                    "Python wheels (*.whl)")
        if path and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_install_file(Path(path)))

    def _install_by_name(self) -> None:
        name = self.name_edit.text().strip()
        if name and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_install([name], user=True))

    def _open_folder(self) -> None:
        folder = self.session.packages.site
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _pip_ready(self) -> bool:
        if is_frozen() or pip_available():
            return True
        self._show_message("pip is missing from the Python that runs pixelRef. "
                           "Install it with:  python -m ensurepip", error=True)
        return False

    # ---- Job progress ----------------------------------------------------------------------------

    def _build_job_area(self) -> QWidget:
        area = QWidget(self)
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.progress = QProgressBar(area)
        self.progress.setRange(0, 0)  # pip reports no overall progress: a busy bar
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.progress.setVisible(False)
        self.message = QLabel("", area)
        self.message.setWordWrap(True)
        self.details_button = QPushButton("Details", area)
        self.details_button.setCheckable(True)
        self.details_button.setVisible(False)
        row.addWidget(self.message, 1)
        row.addWidget(self.details_button)
        layout.addWidget(self.progress)
        layout.addLayout(row)
        self.log = QPlainTextEdit(area)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(120)
        self.log.setVisible(False)
        self.details_button.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        return area

    def _on_job_started(self, description: str) -> None:
        self.log.clear()
        self.progress.setVisible(True)
        self.details_button.setVisible(True)
        self._show_message(f"Installing {description}…" if description else "Working…")
        self._update_buttons()

    def _on_job_output(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_job_finished(self, result: dict[str, Any]) -> None:
        self.progress.setVisible(False)
        self.details_button.setVisible(bool(self.log.toPlainText()))
        self._show_message(result["message"], error=not result["ok"])
        self.refresh()

    def _show_message(self, message: str, error: bool = False) -> None:
        self.message.setText(message)
        self.message.setStyleSheet(f"color: {ERROR_COLOR};" if error else "")

    def _update_banner(self) -> None:
        if self.session.packages.restart_needed:
            self.restart_banner.setText("Restart pixelRef to finish changing Python packages.")
            self.restart_banner.setVisible(True)
        else:
            self.restart_banner.setVisible(False)
