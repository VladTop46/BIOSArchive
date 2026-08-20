#!/usr/bin/env python3
"""BIOSArchive Catalog Browser — browse vendor/SoC/CPU hierarchy with full metadata."""

import sys
import hashlib
from pathlib import Path
from collections import defaultdict
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QVBoxLayout,
    QHBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit,
    QScrollArea, QFrame, QGroupBox, QGridLayout, QPushButton,
    QStatusBar, QSizePolicy, QComboBox, QRadioButton, QButtonGroup,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

VENDORS_DIR = Path(__file__).resolve().parent.parent.parent / "vendors"

ROLE_TYPE = Qt.ItemDataRole.UserRole
ROLE_DATA = Qt.ItemDataRole.UserRole + 1

TYPE_VENDOR    = "vendor"
TYPE_FAMILY    = "family"
TYPE_CPU_MODEL = "cpu_model"
TYPE_DEVICE    = "device"
TYPE_IMAGE     = "image"

ALL = "All"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fmt_size(b: int | None) -> str:
    if b is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b} {unit}"
        b //= 1024
    return f"{b} TB"


def img_status(img: dict) -> tuple[str, str]:
    """Return (label, hex_color) for an image's verification status."""
    v = img.get("verification") or {}
    status = v.get("status") or ("tested" if v.get("tested") else "unknown")
    colors = {"working": "#27ae60", "broken": "#e74c3c", "tested": "#2980b9"}
    return status, colors.get(status, "#7f8c8d")


def get_cpu(entry: dict) -> dict:
    return (entry.get("platform") or {}).get("cpu") or {}


def entry_statuses(entry: dict) -> set[str]:
    return {img_status(img)[0] for img in (entry.get("images") or [])}


def cpu_family_key(entry: dict) -> str:
    cpu = get_cpu(entry)
    return cpu.get("family") or cpu.get("model") or "Unknown"


def cpu_model_key(entry: dict) -> str:
    cpu = get_cpu(entry)
    vendor = cpu.get("vendor") or ""
    model  = cpu.get("model")  or "Unknown"
    return f"{vendor} {model}".strip()


def load_entries() -> list[dict]:
    entries: list[dict] = []
    if not VENDORS_DIR.exists():
        return entries
    for vendor_dir in sorted(VENDORS_DIR.iterdir()):
        if not vendor_dir.is_dir():
            continue
        for device_dir in sorted(vendor_dir.iterdir()):
            if not device_dir.is_dir():
                continue
            meta_path = device_dir / "metadata.yml"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    data["_path"] = device_dir
                    entries.append(data)
            except Exception as exc:
                print(f"[warn] {meta_path}: {exc}", file=sys.stderr)
    return entries


def filter_entries(entries: list[dict],
                   cpu_vendor: str, cpu_family: str,
                   cpu_model_label: str, status: str) -> list[dict]:
    result = []
    for e in entries:
        cpu = get_cpu(e)
        if cpu_vendor != ALL and (cpu.get("vendor") or "") != cpu_vendor:
            continue
        if cpu_family != ALL and cpu_family_key(e) != cpu_family:
            continue
        if cpu_model_label != ALL and cpu_model_key(e) != cpu_model_label:
            continue
        if status != ALL and status not in entry_statuses(e):
            continue
        result.append(e)
    return result


# ---------------------------------------------------------------------------
# Background SHA-256 worker
# ---------------------------------------------------------------------------

class HashWorker(QThread):
    done   = pyqtSignal(str)   # hex digest
    failed = pyqtSignal(str)   # error message

    def __init__(self, path: Path):
        super().__init__()
        self._path = path

    def run(self):
        try:
            h = hashlib.sha256()
            with open(self._path, "rb") as f:
                while chunk := f.read(1 << 20):   # 1 MB chunks
                    h.update(chunk)
            self.done.emit(h.hexdigest())
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, grid: QGridLayout):
        self._grid = grid
        self._row = 0

    def add(self, label: str, value, mono: bool = False, color: str | None = None):
        if value is None or value == "":
            return
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet("color: #666;")
        lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        val = QLabel(str(value))
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setWordWrap(True)
        if mono:
            val.setFont(QFont("monospace", 9))
        if color:
            val.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._grid.addWidget(lbl, self._row, 0, Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(val, self._row, 1, Qt.AlignmentFlag.AlignTop)
        self._row += 1

    @property
    def count(self) -> int:
        return self._row


class DetailPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._inner = QWidget()
        self._vbox = QVBoxLayout(self._inner)
        self._vbox.setContentsMargins(20, 20, 20, 20)
        self._vbox.setSpacing(12)
        self.setWidget(self._inner)
        self._hash_worker: HashWorker | None = None
        self._verify_btn: QPushButton | None = None
        self._verify_lbl: QLabel | None = None

    def _clear(self):
        if self._hash_worker and self._hash_worker.isRunning():
            self._hash_worker.quit()
            self._hash_worker.wait()
        self._hash_worker = None
        self._verify_btn  = None
        self._verify_lbl  = None
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if w := item.widget():
                w.deleteLater()

    def _heading(self, text: str, size: int = 18) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: {size}px; font-weight: bold;")
        lbl.setWordWrap(True)
        return lbl

    def _sep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("color: #ddd;")
        return f

    def _group(self, title: str) -> tuple[QGroupBox, _Row]:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setColumnMinimumWidth(0, 100)
        grid.setColumnStretch(1, 1)
        return box, _Row(grid)

    def _sha_row(self, grid: QGridLayout, row: int, sha: str):
        lbl = QLabel("SHA-256:")
        lbl.setStyleSheet("color: #666;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        container = QWidget()
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        sha_lbl = QLabel(sha)
        sha_lbl.setFont(QFont("monospace", 9))
        sha_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sha_lbl.setWordWrap(True)
        copy_btn = QPushButton("Copy")
        copy_btn.setFixedWidth(50)
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(sha))
        hbox.addWidget(sha_lbl, 1)
        hbox.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(lbl,       row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(container, row, 1)

    # ---- hash verification ----

    def _start_verify(self, file_path: Path, expected: str):
        self._verify_btn.setEnabled(False)
        self._verify_lbl.setText("Computing…")
        self._verify_lbl.setStyleSheet("color: #888; font-weight: normal;")

        self._hash_worker = HashWorker(file_path)
        self._hash_worker.done.connect(
            lambda digest, ex=expected: self._on_hash_done(digest, ex)
        )
        self._hash_worker.failed.connect(self._on_hash_error)
        self._hash_worker.start()

    def _on_hash_done(self, digest: str, expected: str):
        if self._verify_btn:
            self._verify_btn.setEnabled(True)
        if self._verify_lbl is None:
            return
        if digest.lower() == expected.lower():
            self._verify_lbl.setText("✓ Match")
            self._verify_lbl.setStyleSheet("color: #27ae60; font-weight: bold;")
        else:
            self._verify_lbl.setText("✗ Mismatch")
            self._verify_lbl.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self._verify_lbl.setToolTip(f"Expected: {expected}\nGot:      {digest}")

    def _on_hash_error(self, msg: str):
        if self._verify_btn:
            self._verify_btn.setEnabled(True)
        if self._verify_lbl:
            self._verify_lbl.setText(f"Error: {msg}")
            self._verify_lbl.setStyleSheet("color: #e67e22; font-weight: normal;")

    # ---- public view methods ----

    def show_welcome(self, count: int):
        self._clear()
        self._vbox.addStretch()
        msg = QLabel(f"BIOSArchive Catalog\n\n{count} device(s) indexed")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("font-size: 15px; color: #555;")
        self._vbox.addWidget(msg)
        self._vbox.addStretch()

    def show_vendor(self, vendor: str, entries: list[dict]):
        self._clear()
        self._vbox.addWidget(self._heading(vendor))
        self._vbox.addWidget(self._sep())

        families: set[str] = {cpu_family_key(e) for e in entries}
        total_images = sum(len(e.get("images") or []) for e in entries)

        box, rows = self._group("Summary")
        rows.add("Devices",       str(len(entries)))
        rows.add("Total images",  str(total_images))
        rows.add("CPU families",  ", ".join(sorted(families)))
        self._vbox.addWidget(box)
        self._vbox.addStretch()

    def show_family(self, family: str, entries: list[dict]):
        """Used in By Vendor mode for the CPU family node."""
        self._clear()
        self._vbox.addWidget(self._heading(family))
        self._vbox.addWidget(self._sep())

        first_cpu = next(
            (get_cpu(e) for e in entries if get_cpu(e)),
            None
        )
        if first_cpu:
            box, rows = self._group("CPU / SoC")
            rows.add("Vendor",   first_cpu.get("vendor"))
            rows.add("Model",    first_cpu.get("model"))
            rows.add("Family",   first_cpu.get("family"))
            rows.add("Stepping", first_cpu.get("stepping"))
            self._vbox.addWidget(box)

        box2, rows2 = self._group("Summary")
        rows2.add("Devices",      str(len(entries)))
        rows2.add("Total images", str(sum(len(e.get("images") or []) for e in entries)))
        # List boards (cross-vendor view)
        board_lines = []
        for e in entries:
            plat = e.get("platform") or {}
            board_lines.append(f"{plat.get('vendor', '?')}  —  {plat.get('model', '?')}")
        rows2.add("Boards", "\n".join(board_lines))
        self._vbox.addWidget(box2)
        self._vbox.addStretch()

    def show_cpu_model(self, model_label: str, entries: list[dict]):
        """Used in By CPU Family mode for the CPU model node."""
        self._clear()
        self._vbox.addWidget(self._heading(model_label))
        self._vbox.addWidget(self._sep())

        first_cpu = next((get_cpu(e) for e in entries if get_cpu(e)), None)
        if first_cpu:
            box, rows = self._group("CPU / SoC")
            rows.add("Vendor",   first_cpu.get("vendor"))
            rows.add("Model",    first_cpu.get("model"))
            rows.add("Family",   first_cpu.get("family"))
            rows.add("Stepping", first_cpu.get("stepping"))
            self._vbox.addWidget(box)

        boards_box = QGroupBox(f"Boards  ({len(entries)})")
        boards_vbox = QVBoxLayout(boards_box)
        boards_vbox.setSpacing(2)
        for e in entries:
            plat = e.get("platform") or {}
            imgs = e.get("images") or []
            lbl = QLabel(
                f"<b>{plat.get('vendor', '?')}</b>  —  {plat.get('model', '?')}"
                f"  <span style='color:#888'>({len(imgs)} image(s))</span>"
            )
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            boards_vbox.addWidget(lbl)
        self._vbox.addWidget(boards_box)
        self._vbox.addStretch()

    def show_device(self, entry: dict):
        self._clear()
        plat = entry.get("platform") or {}
        cpu  = plat.get("cpu") or {}
        path = entry.get("_path")

        self._vbox.addWidget(self._heading(plat.get("model", "Unknown device")))
        self._vbox.addWidget(self._sep())

        box, rows = self._group("Platform")
        rows.add("Vendor", plat.get("vendor"))
        rows.add("Model",  plat.get("model"))
        if path:
            rows.add("Path", str(path))
        self._vbox.addWidget(box)

        if any(cpu.get(k) for k in ("vendor", "model", "family", "stepping")):
            box2, rows2 = self._group("CPU / SoC")
            rows2.add("Vendor",   cpu.get("vendor"))
            rows2.add("Model",    cpu.get("model"))
            rows2.add("Family",   cpu.get("family"))
            rows2.add("Stepping", cpu.get("stepping"))
            self._vbox.addWidget(box2)

        images = entry.get("images") or []
        if images:
            box3 = QGroupBox(f"Images  ({len(images)})")
            vbox3 = QVBoxLayout(box3)
            vbox3.setSpacing(8)
            for img in images:
                status, color = img_status(img)
                frame = QFrame()
                frame.setFrameShape(QFrame.Shape.StyledPanel)
                grid = QGridLayout(frame)
                grid.setColumnMinimumWidth(0, 90)
                grid.setColumnStretch(1, 1)
                r = _Row(grid)
                r.add("File",   img.get("file"))
                r.add("Type",   img.get("type"))
                r.add("Size",   fmt_size(img.get("size")))
                r.add("Status", status, color=color)
                if img.get("sha256"):
                    self._sha_row(grid, r.count, str(img["sha256"]))
                vbox3.addWidget(frame)
            self._vbox.addWidget(box3)

        self._vbox.addStretch()

    def show_image(self, entry: dict, img_index: int):
        self._clear()
        plat   = entry.get("platform") or {}
        images = entry.get("images") or []
        if img_index >= len(images):
            return
        img = images[img_index]

        subtitle = f"{plat.get('model', '?')}  /  {img.get('file', '?')}"
        self._vbox.addWidget(self._heading(subtitle, size=16))
        self._vbox.addWidget(self._sep())

        box, rows = self._group("Image")
        rows.add("File", img.get("file"))
        rows.add("Type", img.get("type"))
        rows.add("Size", fmt_size(img.get("size")))
        if img.get("sha256"):
            self._sha_row(box.layout(), rows.count, str(img["sha256"]))
        self._vbox.addWidget(box)

        # Integrity verification
        if img.get("sha256"):
            file_path = (entry.get("_path") or Path()) / (img.get("file") or "")
            expected  = str(img["sha256"])
            box_i = QGroupBox("Integrity")
            vi = QVBoxLayout(box_i)
            row_w = QWidget()
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(8)
            self._verify_btn = QPushButton("Verify SHA-256")
            self._verify_lbl = QLabel("")
            self._verify_lbl.setFont(QFont(None, -1, QFont.Weight.Bold))
            if not file_path.exists():
                self._verify_btn.setEnabled(False)
                self._verify_btn.setToolTip(f"File not found: {file_path}")
            else:
                self._verify_btn.clicked.connect(
                    lambda _=False, fp=file_path, ex=expected: self._start_verify(fp, ex)
                )
            rh.addWidget(self._verify_btn)
            rh.addWidget(self._verify_lbl, 1)
            vi.addWidget(row_w)
            self._vbox.addWidget(box_i)

        src = img.get("source") or {}
        if any(src.get(k) for k in ("type", "origin", "obtained")):
            box2, rows2 = self._group("Source")
            rows2.add("Method",   src.get("type"))
            rows2.add("Origin",   src.get("origin"))
            rows2.add("Obtained", str(src["obtained"]) if src.get("obtained") else None)
            self._vbox.addWidget(box2)

        nvram   = img.get("nvram") or {}
        cleared = nvram.get("cleared")
        method  = nvram.get("method")
        if cleared is not None or method:
            box3, rows3 = self._group("NVRAM")
            if cleared is not None:
                rows3.add("Cleared", "Yes" if cleared else "No",
                          color="#e67e22" if cleared else "#27ae60")
            rows3.add("Method", method)
            self._vbox.addWidget(box3)

        ver    = img.get("verification") or {}
        tested = ver.get("tested")
        status = ver.get("status")
        if tested is not None or status:
            box4, rows4 = self._group("Verification")
            if tested is not None:
                rows4.add("Tested", "Yes" if tested else "No",
                          color="#27ae60" if tested else "#7f8c8d")
            if status:
                s_color = {"working": "#27ae60", "broken": "#e74c3c"}.get(status)
                rows4.add("Status", status, color=s_color)
            self._vbox.addWidget(box4)

        notes = img.get("notes") or []
        if notes:
            box5 = QGroupBox("Notes")
            vbox5 = QVBoxLayout(box5)
            for note in notes:
                lbl = QLabel(f"• {note}")
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                vbox5.addWidget(lbl)
            self._vbox.addWidget(box5)

        self._vbox.addStretch()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BIOSArchive — Catalog Browser")
        self.resize(1180, 740)

        self._entries = load_entries()
        self._build_ui()
        self._init_combos()
        self._refresh()

    # ---- UI construction ----

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ===== Left panel =====
        left = QWidget()
        left.setMinimumWidth(270)
        left.setMaximumWidth(440)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(6)

        # -- View toggle --
        view_row = QHBoxLayout()
        view_row.setSpacing(12)
        view_lbl = QLabel("View:")
        view_lbl.setStyleSheet("color: #555;")
        self._radio_vendor = QRadioButton("By Vendor")
        self._radio_family = QRadioButton("By CPU Family")
        self._radio_vendor.setChecked(True)
        self._view_group = QButtonGroup(self)
        self._view_group.addButton(self._radio_vendor)
        self._view_group.addButton(self._radio_family)
        self._radio_vendor.toggled.connect(self._refresh)
        view_row.addWidget(view_lbl)
        view_row.addWidget(self._radio_vendor)
        view_row.addWidget(self._radio_family)
        view_row.addStretch()
        lv.addLayout(view_row)

        # -- Filter bar --
        filter_frame = QFrame()
        filter_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ff = QGridLayout(filter_frame)
        ff.setContentsMargins(6, 6, 6, 6)
        ff.setSpacing(4)

        def _combo() -> QComboBox:
            c = QComboBox()
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return c

        ff.addWidget(QLabel("CPU Vendor:"), 0, 0)
        self._combo_cpu_vendor = _combo()
        ff.addWidget(self._combo_cpu_vendor, 0, 1)

        ff.addWidget(QLabel("CPU Family:"), 1, 0)
        self._combo_cpu_family = _combo()
        ff.addWidget(self._combo_cpu_family, 1, 1)

        ff.addWidget(QLabel("CPU Model:"), 2, 0)
        self._combo_cpu_model = _combo()
        ff.addWidget(self._combo_cpu_model, 2, 1)

        ff.addWidget(QLabel("Status:"), 3, 0)
        self._combo_status = _combo()
        self._combo_status.addItems([ALL, "working", "broken", "tested", "unknown"])
        ff.addWidget(self._combo_status, 3, 1)

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedHeight(24)
        reset_btn.clicked.connect(self._reset_filters)
        ff.addWidget(reset_btn, 4, 0, 1, 2)

        lv.addWidget(filter_frame)

        # -- Text search --
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search in tree…")
        self._search.textChanged.connect(self._on_text_filter)
        lv.addWidget(self._search)

        # -- Tree --
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Info"])
        hdr = self._tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, hdr.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, hdr.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(True)
        self._tree.setAnimated(True)
        self._tree.itemSelectionChanged.connect(self._on_select)
        lv.addWidget(self._tree)

        splitter.addWidget(left)

        # ===== Right panel =====
        self._detail = DetailPanel()
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 860])

        self.setStatusBar(QStatusBar())

        # Connect combo signals AFTER widgets exist
        self._combo_cpu_vendor.currentTextChanged.connect(self._on_vendor_changed)
        self._combo_cpu_family.currentTextChanged.connect(self._on_family_changed)
        self._combo_cpu_model.currentTextChanged.connect(self._refresh)
        self._combo_status.currentTextChanged.connect(self._refresh)

    # ---- Combo population ----

    def _init_combos(self):
        """Populate all combos from the full entry list (called once at startup)."""
        vendors = sorted({get_cpu(e).get("vendor") or "" for e in self._entries} - {""})
        self._combo_cpu_vendor.blockSignals(True)
        self._combo_cpu_vendor.addItem(ALL)
        self._combo_cpu_vendor.addItems(vendors)
        self._combo_cpu_vendor.blockSignals(False)
        self._rebuild_family_combo(emit=False)

    def _rebuild_family_combo(self, emit: bool = True):
        sel_vendor = self._combo_cpu_vendor.currentText()
        pool = self._entries if sel_vendor == ALL else [
            e for e in self._entries if get_cpu(e).get("vendor") == sel_vendor
        ]
        families = sorted({cpu_family_key(e) for e in pool} - {"Unknown"})

        prev = self._combo_cpu_family.currentText()
        self._combo_cpu_family.blockSignals(True)
        self._combo_cpu_family.clear()
        self._combo_cpu_family.addItem(ALL)
        self._combo_cpu_family.addItems(families)
        idx = self._combo_cpu_family.findText(prev)
        self._combo_cpu_family.setCurrentIndex(max(0, idx))
        self._combo_cpu_family.blockSignals(False)

        self._rebuild_model_combo(emit=False)
        if emit:
            self._refresh()

    def _rebuild_model_combo(self, emit: bool = True):
        sel_vendor = self._combo_cpu_vendor.currentText()
        sel_family = self._combo_cpu_family.currentText()
        pool = self._entries
        if sel_vendor != ALL:
            pool = [e for e in pool if get_cpu(e).get("vendor") == sel_vendor]
        if sel_family != ALL:
            pool = [e for e in pool if cpu_family_key(e) == sel_family]
        models = sorted({cpu_model_key(e) for e in pool} - {"Unknown", ""})

        prev = self._combo_cpu_model.currentText()
        self._combo_cpu_model.blockSignals(True)
        self._combo_cpu_model.clear()
        self._combo_cpu_model.addItem(ALL)
        self._combo_cpu_model.addItems(models)
        idx = self._combo_cpu_model.findText(prev)
        self._combo_cpu_model.setCurrentIndex(max(0, idx))
        self._combo_cpu_model.blockSignals(False)

        if emit:
            self._refresh()

    def _reset_filters(self):
        for combo in (self._combo_cpu_vendor, self._combo_cpu_family,
                      self._combo_cpu_model, self._combo_status):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._search.clear()
        self._rebuild_family_combo(emit=False)
        self._refresh()

    # ---- Signal handlers ----

    def _on_vendor_changed(self):
        self._rebuild_family_combo(emit=True)

    def _on_family_changed(self):
        self._rebuild_model_combo(emit=True)

    def _refresh(self):
        entries = filter_entries(
            self._entries,
            self._combo_cpu_vendor.currentText(),
            self._combo_cpu_family.currentText(),
            self._combo_cpu_model.currentText(),
            self._combo_status.currentText(),
        )
        if self._radio_vendor.isChecked():
            self._populate_by_vendor(entries)
        else:
            self._populate_by_family(entries)

        n_all = len(self._entries)
        n_flt = len(entries)
        suffix = f"  •  filtered: {n_flt}" if n_flt != n_all else ""
        self.statusBar().showMessage(f"{n_all} device(s) indexed{suffix}  •  {VENDORS_DIR}")

        # Re-apply text search on the rebuilt tree
        self._on_text_filter(self._search.text())

    # ---- Tree builders ----

    def _tree_fonts(self):
        bold = QFont(); bold.setBold(True)
        mono = QFont("monospace", 9)
        return bold, mono

    def _populate_by_vendor(self, entries: list[dict]):
        self._tree.clear()
        bold, mono = self._tree_fonts()

        by_vendor: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for e in entries:
            plat   = e.get("platform") or {}
            vendor = plat.get("vendor") or "Unknown"
            by_vendor[vendor][cpu_family_key(e)].append(e)

        for vendor, families in sorted(by_vendor.items()):
            total = sum(len(v) for v in families.values())
            v_item = QTreeWidgetItem(self._tree, [vendor, str(total)])
            v_item.setData(0, ROLE_TYPE, TYPE_VENDOR)
            v_item.setData(0, ROLE_DATA, {"vendor": vendor, "families": dict(families)})
            v_item.setFont(0, bold)
            v_item.setExpanded(True)

            for family, devs in sorted(families.items()):
                f_item = QTreeWidgetItem(v_item, [family, str(len(devs))])
                f_item.setData(0, ROLE_TYPE, TYPE_FAMILY)
                f_item.setData(0, ROLE_DATA, {"family": family, "entries": devs})
                f_item.setExpanded(True)
                self._add_device_items(f_item, devs, mono)

    def _populate_by_family(self, entries: list[dict]):
        self._tree.clear()
        bold, mono = self._tree_fonts()

        by_family: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for e in entries:
            by_family[cpu_family_key(e)][cpu_model_key(e)].append(e)

        for family, models in sorted(by_family.items()):
            total = sum(len(v) for v in models.values())
            f_item = QTreeWidgetItem(self._tree, [family, str(total)])
            f_item.setData(0, ROLE_TYPE, TYPE_FAMILY)
            f_item.setData(0, ROLE_DATA, {"family": family,
                                           "entries": [e for v in models.values() for e in v]})
            f_item.setFont(0, bold)
            f_item.setExpanded(True)

            for model_label, devs in sorted(models.items()):
                cpu = get_cpu(devs[0])
                stepping = cpu.get("stepping") or ""
                info = f"{len(devs)}  {stepping}".strip()
                m_item = QTreeWidgetItem(f_item, [model_label, info])
                m_item.setData(0, ROLE_TYPE, TYPE_CPU_MODEL)
                m_item.setData(0, ROLE_DATA, {"model_label": model_label, "entries": devs})
                m_item.setExpanded(True)
                # Devices include vendor prefix so they're distinguishable cross-vendor
                self._add_device_items(m_item, devs, mono, show_vendor=True)

    def _add_device_items(self, parent: QTreeWidgetItem, devs: list[dict],
                          mono: QFont, show_vendor: bool = False):
        for entry in devs:
            plat  = entry.get("platform") or {}
            model = plat.get("model") or "Unknown"
            label = f"{plat.get('vendor', '?')} — {model}" if show_vendor else model
            imgs  = entry.get("images") or []
            d_item = QTreeWidgetItem(parent, [label, str(len(imgs))])
            d_item.setData(0, ROLE_TYPE, TYPE_DEVICE)
            d_item.setData(0, ROLE_DATA, entry)

            for idx, img in enumerate(imgs):
                fname = img.get("file") or f"image-{idx+1}"
                i_item = QTreeWidgetItem(d_item, [fname, fmt_size(img.get("size"))])
                i_item.setData(0, ROLE_TYPE, TYPE_IMAGE)
                i_item.setData(0, ROLE_DATA, {"entry": entry, "index": idx})
                i_item.setFont(0, mono)
                _, color = img_status(img)
                i_item.setForeground(1, QBrush(QColor(color)))

    # ---- Selection handler ----

    def _on_select(self):
        items = self._tree.selectedItems()
        if not items:
            self._detail.show_welcome(len(self._entries))
            return
        item = items[0]
        kind = item.data(0, ROLE_TYPE)
        data = item.data(0, ROLE_DATA)

        if kind == TYPE_VENDOR:
            all_devs = [e for devs in data["families"].values() for e in devs]
            self._detail.show_vendor(data["vendor"], all_devs)
        elif kind == TYPE_FAMILY:
            self._detail.show_family(data["family"], data["entries"])
        elif kind == TYPE_CPU_MODEL:
            self._detail.show_cpu_model(data["model_label"], data["entries"])
        elif kind == TYPE_DEVICE:
            self._detail.show_device(data)
        elif kind == TYPE_IMAGE:
            self._detail.show_image(data["entry"], data["index"])

    # ---- Text search ----

    def _on_text_filter(self, text: str):
        needle = text.strip().lower()
        self._filter_item(self._tree.invisibleRootItem(), needle)

    def _filter_item(self, parent: QTreeWidgetItem, needle: str) -> bool:
        any_visible = False
        for i in range(parent.childCount()):
            child = parent.child(i)
            child_match = self._filter_item(child, needle)
            self_match  = not needle or needle in child.text(0).lower()
            visible     = self_match or child_match
            child.setHidden(not visible)
            if visible:
                any_visible = True
                if child_match and needle:
                    child.setExpanded(True)
        return any_visible


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BIOSArchive Catalog Browser")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
