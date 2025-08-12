import os
import sys
from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
    QGroupBox, QLineEdit, QFormLayout, QMessageBox
)

# Try to load QDarkStyle if available
_HAS_QDARK = False
try:
    import qdarkstyle
    _HAS_QDARK = True
except Exception:
    _HAS_QDARK = False

# Import processing engine
try:
    from engine import apply_envelopes
except Exception as e:
    def apply_envelopes(dest_path, mold_paths, out_path, cfg, progress_cb, log_cb):
        log_cb("[WARN] engine.apply_envelopes not found, copying destination as dummy output.")
        import shutil, time
        total = max(1, len(mold_paths))
        for i, p in enumerate(mold_paths, start=1):
            time.sleep(0.1)
            progress_cb(int(i * 80 / total))
            log_cb(f"Using dummy mold: {Path(p).name}")
        shutil.copy2(dest_path, out_path)
        progress_cb(100)
        log_cb(f"Done. (Output: {out_path})")

AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aiff', '.aif'}

class Worker(QThread):
    progressed = Signal(int)
    logged = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, dest_path, mold_paths, out_path, cfg):
        super().__init__()
        self.dest_path = dest_path
        self.mold_paths = mold_paths
        self.out_path = out_path
        self.cfg = cfg

    def run(self):
        try:
            def _p(v): self.progressed.emit(int(v))
            def _l(msg): self.logged.emit(str(msg))
            _l("Starting processing…")
            apply_envelopes(self.dest_path, self.mold_paths, self.out_path, self.cfg, _p, _l)
            self.finished_ok.emit(self.out_path)
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(tb)

class DropList(QListWidget):
    def __init__(self, allow_multiple=True):
        super().__init__()
        self.setAcceptDrops(True)
        self.allow_multiple = allow_multiple
        self.setMinimumHeight(120)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.suffix.lower() in AUDIO_EXTS:
                        self._add_path(child)
            else:
                if p.suffix.lower() in AUDIO_EXTS:
                    self._add_path(p)
        e.acceptProposedAction()

    def _add_path(self, p: Path):
        if not self.allow_multiple:
            self.clear()
        it = QListWidgetItem(str(p))
        self.addItem(it)

    def paths(self):
        out = []
        for i in range(self.count()):
            out.append(self.item(i).text())
        return out

class MainWin(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Copy Envelope")

        # Optional window icon if present (user can add assets/app.png later)
        icon_path = Path("assets/app.png")  # PNG for window icon
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QVBoxLayout(self)

        # Description text
        desc = QLabel("""
        <b>Descripción</b><br>
        Este programa toma uno o varios audios “molde” y extrae su
        envolvente de volumen (la curva de subidas y bajadas de intensidad)
        para aplicarla sobre otro audio “destino”.
        """.strip())
        desc.setWordWrap(True)
        root.addWidget(desc)

        copyright = QLabel("© 2025 Gabriel Golker")
        root.addWidget(copyright)

        # Mold group
        g_molds = QGroupBox("Moldes (arrastra archivos sueltos o una carpeta)")
        lm = QVBoxLayout(g_molds)
        self.mold_list = DropList(allow_multiple=True)
        lm.addWidget(self.mold_list)

        btns_m = QHBoxLayout()
        btn_add_files = QPushButton("Añadir archivos…")
        btn_add_folder = QPushButton("Añadir carpeta…")
        btn_clear_m = QPushButton("Limpiar")
        btns_m.addWidget(btn_add_files)
        btns_m.addWidget(btn_add_folder)
        btns_m.addWidget(btn_clear_m)
        lm.addLayout(btns_m)

        btn_add_files.clicked.connect(self.pick_mold_files)
        btn_add_folder.clicked.connect(self.pick_mold_folder)
        btn_clear_m.clicked.connect(self.mold_list.clear)

        root.addWidget(g_molds)

        # Destination
        g_dest = QGroupBox("Destino (arrastra o elige un archivo)")
        ld = QVBoxLayout(g_dest)
        self.dest_list = DropList(allow_multiple=False)
        ld.addWidget(self.dest_list)
        btn_dest = QPushButton("Elegir destino…")
        btn_clear_d = QPushButton("Limpiar")
        bdh = QHBoxLayout()
        bdh.addWidget(btn_dest)
        bdh.addWidget(btn_clear_d)
        ld.addLayout(bdh)
        btn_dest.clicked.connect(self.pick_dest_file)
        btn_clear_d.clicked.connect(self.dest_list.clear)
        root.addWidget(g_dest)

        # Quick config
        g_cfg = QGroupBox("Configuración rápida")
        lf = QFormLayout(g_cfg)
        self.ed_bpm = QLineEdit("100")
        self.ed_attack = QLineEdit("1.0")
        self.ed_release = QLineEdit("0.5")
        self.ed_floor_db = QLineEdit("-40.0")
        self.ed_mode = QLineEdit("hilbert")  # 'hilbert' or 'rms'
        self.ed_combine = QLineEdit("max")   # max/mean/geom_mean/product/sum_limited/weighted
        self.ed_weights = QLineEdit("")      # optional: comma-separated weights
        self.ed_out = QLineEdit(str(Path.cwd() / "salida.wav"))
        lf.addRow("BPM:", self.ed_bpm)
        lf.addRow("Attack ms:", self.ed_attack)
        lf.addRow("Release ms:", self.ed_release)
        lf.addRow("Floor dB:", self.ed_floor_db)
        lf.addRow("Envelope mode:", self.ed_mode)
        lf.addRow("Combine mode:", self.ed_combine)
        lf.addRow("Weights (coma):", self.ed_weights)
        lf.addRow("Archivo de salida:", self.ed_out)
        root.addWidget(g_cfg)

        # Progress & Logs
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(5000)
        root.addWidget(self.logs)

        # Action buttons
        hb = QHBoxLayout()
        self.btn_run = QPushButton("Procesar")
        self.btn_run.clicked.connect(self.on_run)
        hb.addWidget(self.btn_run)
        root.addLayout(hb)

        self.worker = None

    def append_log(self, text):
        self.logs.appendPlainText(text)

    def pick_mold_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Elegir moldes", str(Path.cwd()),
                                               "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        for f in files:
            self.mold_list._add_path(Path(f))

    def pick_mold_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Elegir carpeta de moldes", str(Path.cwd()))
        if folder:
            for child in sorted(Path(folder).iterdir()):
                if child.suffix.lower() in AUDIO_EXTS:
                    self.mold_list._add_path(child)

    def pick_dest_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Elegir destino", str(Path.cwd()),
                                          "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        if f:
            self.dest_list._add_path(Path(f))

    def on_run(self):
        molds = self.mold_list.paths()
        if not molds:
            QMessageBox.warning(self, "Faltan moldes", "Agrega al menos un molde (archivo o carpeta).")
            return
        dests = self.dest_list.paths()
        if not dests:
            QMessageBox.warning(self, "Falta destino", "Elige el archivo destino.")
            return
        dest = dests[0]
        out = self.ed_out.text().strip()
        if not out:
            QMessageBox.warning(self, "Falta salida", "Especifica el archivo de salida (ej: salida.wav).")
            return

        weights = None
        wtxt = self.ed_weights.text().strip()
        if wtxt:
            try:
                weights = [float(x) for x in wtxt.split(",")]
            except Exception:
                QMessageBox.warning(self, "Weights inválidos", "Usa números separados por coma, ej: 1,0.8,1.2")
                return

        cfg = {
            "bpm": float(self.ed_bpm.text() or 100),
            "attack_ms": float(self.ed_attack.text() or 1.0),
            "release_ms": float(self.ed_release.text() or 0.5),
            "floor_db": float(self.ed_floor_db.text() or -40.0),
            "mode": (self.ed_mode.text() or "hilbert").strip().lower(),
            "combine_mode": (self.ed_combine.text() or "max").strip().lower(),
            "weights": weights,
            "match_lufs": False,
        }

        self.progress.setValue(0)
        self.logs.clear()

        self.worker = Worker(dest, molds, out, cfg)
        self.worker.progressed.connect(self.progress.setValue)
        self.worker.logged.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def on_done(self, out_path):
        self.append_log(f"OK: {out_path}")
        QMessageBox.information(self, "Listo", f"Se generó: {out_path}")

    def on_fail(self, tb):
        self.append_log(tb)
        QMessageBox.critical(self, "Error", "Ocurrió un error. Revisa los logs.")

def main():
    app = QApplication(sys.argv)
    if _HAS_QDARK:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyside6())
    win = MainWin()
    win.resize(900, 760)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()


