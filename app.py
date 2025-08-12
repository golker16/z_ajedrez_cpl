import sys, atexit, random
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtCore import Qt, QSize
import qdarkstyle
import chess, chess.engine, chess.svg

# === Utilidad para localizar recursos empacados con PyInstaller ===
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:  # ejecutable PyInstaller
        return str(Path(base) / rel)
    return str(Path(__file__).parent / rel)

# Ruta al stockfish embebido
ENGINE_PATH = resource_path("assets/stockfish/stockfish.exe")

# --- Configuración CPL ---
CPL_MODES = {
    "Perfecto (≈1)": {"depth": 18, "mpv": 4,  "target": 1,  "perfect": True},
    "CPL 15":        {"depth": 14, "mpv": 6,  "target": 15, "perfect": False},
    "CPL 30":        {"depth": 12, "mpv": 8,  "target": 30, "perfect": False},
    "CPL 40":        {"depth": 10, "mpv": 10, "target": 40, "perfect": False},
}

engine = None
SIZE = 560
CELL = SIZE // 8

# --- Utilidades de evaluación ---
def _score_to_cp(score: chess.engine.PovScore, pov_white: bool) -> int:
    s = score.white() if pov_white else score.black()
    if s.is_mate():
        return 100_000 if s.mate() and s.mate() > 0 else -100_000
    return s.score()

def _multipv(board: chess.Board, depth=12, mpv=6):
    info = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=mpv)
    pov_white = (board.turn == chess.WHITE)
    lines = []
    for i in info:
        pv = i.get("pv", None)
        sc = i.get("score", None)
        if pv and sc:
            mv = pv[0]
            cp = _score_to_cp(sc, pov_white)
            lines.append((mv, cp))
    lines.sort(key=lambda t: t[1], reverse=True)
    return lines

def _choose_by_target_cpl(lines, target_cpl: int):
    best_cp = lines[0][1]
    desired = max(0, int(random.gauss(target_cpl, max(4, target_cpl * 0.25))))
    pick, best_diff = lines[0][0], float("inf")
    picked_cp = lines[0][1]
    for mv, cp in lines:
        delta = max(0, best_cp - cp)
        diff = abs(delta - desired)
        if diff < best_diff:
            best_diff, pick, picked_cp = diff, mv, cp
    return pick, max(0, best_cp - picked_cp)

def engine_pick_move(board: chess.Board, mode_name: str):
    p = CPL_MODES[mode_name]
    lines = _multipv(board, depth=p["depth"], mpv=p["mpv"])
    if not lines:
        mv = engine.play(board, chess.engine.Limit(depth=p["depth"])).move
        return mv, 0
    if p["perfect"]:
        return lines[0][0], 0
    mv, cpl = _choose_by_target_cpl(lines, p["target"])
    return mv, cpl

# --- Widget del tablero ---
class BoardWidget(QSvgWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.board = chess.Board()
        self.flipped = False
        self.mode = "CPL 30"
        self.origin = None
        self.cpl_sum_me = 0
        self.cpl_cnt_me = 0
        self.cpl_sum_engine = 0
        self.cpl_cnt_engine = 0
        self.refresh()

    def avg_cpl_me(self):
        return self.cpl_sum_me / self.cpl_cnt_me if self.cpl_cnt_me else 0.0

    def avg_cpl_engine(self):
        return self.cpl_sum_engine / self.cpl_cnt_engine if self.cpl_cnt_engine else 0.0

    def set_flipped(self, flip: bool):
        self.flipped = flip
        self.refresh()

    def set_mode(self, mode: str):
        self.mode = mode

    def start_as_black_if_needed(self, label_status: QLabel):
        if self.flipped and len(self.board.move_stack) == 0:
            mv, cpl = engine_pick_move(self.board, self.mode)
            self.board.push(mv)
            self.cpl_sum_engine += cpl
            self.cpl_cnt_engine += 1
            self.refresh()
            label_status.setText(self._status_text())

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() != Qt.LeftButton:
            return
        x = int(e.position().x()) // CELL
        y = int(e.position().y()) // CELL
        sq = self._sq_from_xy(x, y)
        if self.origin is None:
            self.origin = sq
            return
        self._try_play(sq)

    def _try_play(self, dst_sq):
        src = self.origin
        self.origin = None
        if src is None:
            return
        mv = chess.Move(src, dst_sq)
        legal = mv in self.board.legal_moves
        if not legal and self.board.piece_at(src) and self.board.piece_at(src).piece_type == chess.PAWN:
            rank = chess.square_rank(dst_sq)
            if rank in (0, 7):
                mv = chess.Move(src, dst_sq, promotion=chess.QUEEN)
                legal = mv in self.board.legal_moves
        if not legal:
            self.refresh()
            return
        params = CPL_MODES[self.mode]
        lines = _multipv(self.board, depth=params["depth"], mpv=max(6, params["mpv"]))
        my_cpl = 0
        if lines:
            best_cp = lines[0][1]
            chosen_cp = None
            for mv_i, cp_i in lines:
                if mv_i == mv:
                    chosen_cp = cp_i
                    break
            if chosen_cp is None:
                tmp = self.board.copy()
                tmp.push(mv)
                info = engine.analyse(tmp, chess.engine.Limit(depth=params["depth"]))
                chosen_cp = -_score_to_cp(info["score"], pov_white=(tmp.turn == chess.WHITE))
            my_cpl = max(0, best_cp - chosen_cp)
        self.board.push(mv)
        self.cpl_sum_me += my_cpl
        self.cpl_cnt_me += 1
        self.refresh()
        if self.board.is_game_over():
            return
        mv_e, cpl_e = engine_pick_move(self.board, self.mode)
        self.board.push(mv_e)
        self.cpl_sum_engine += cpl_e
        self.cpl_cnt_engine += 1
        self.refresh()

    def _sq_from_xy(self, x, y):
        return (y * 8 + (7 - x)) if self.flipped else ((7 - y) * 8 + x)

    def refresh(self):
        svg = chess.svg.board(board=self.board, flipped=self.flipped, size=SIZE)
        self.load(bytearray(svg, encoding="utf-8"))

    def _status_text(self):
        return (f"CPL (Yo): {self.avg_cpl_me():.1f} | "
                f"CPL (Motor): {self.avg_cpl_engine():.1f}")

# --- Ventana principal ---
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ajedrez CPL – Gabriel Golker")
        self.setWindowIcon(QIcon(resource_path("assets/app.png")))
        self.boardw = BoardWidget()
        self.lbl_status = QLabel(self.boardw._status_text())
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.cmb_mode = QComboBox(); self.cmb_mode.addItems(CPL_MODES.keys())
        self.cmb_mode.setCurrentText("CPL 30")
        self.cmb_side = QComboBox(); self.cmb_side.addItems(["Blancas", "Negras"])
        self.btn_undo = QPushButton("Deshacer")
        self.btn_reset = QPushButton("Reiniciar")
        top = QHBoxLayout()
        top.addWidget(QLabel("CPL:")); top.addWidget(self.cmb_mode)
        top.addWidget(QLabel("Yo:")); top.addWidget(self.cmb_side)
        top.addWidget(self.btn_undo); top.addWidget(self.btn_reset)
        self.lbl_copy = QLabel("© 2025 Gabriel Golker")
        self.lbl_copy.setAlignment(Qt.AlignCenter)
        self.lbl_copy.setStyleSheet("font-size:11px; opacity:0.8;")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.boardw, alignment=Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.lbl_copy)
        self.cmb_mode.currentTextChanged.connect(self.on_mode_changed)
        self.cmb_side.currentTextChanged.connect(self.on_side_changed)
        self.btn_undo.clicked.connect(self.on_undo)
        self.btn_reset.clicked.connect(self.on_reset)
        self.setStyleSheet(qdarkstyle.load_stylesheet())

    def on_mode_changed(self, v):
        self.boardw.set_mode(v)
        self.lbl_status.setText(self.boardw._status_text())

    def on_side_changed(self, v):
        flip = (v == "Negras")
        self.boardw.set_flipped(flip)
        self.boardw.start_as_black_if_needed(self.lbl_status)
        self.lbl_status.setText(self.boardw._status_text())

    def on_undo(self):
        if self.boardw.board.move_stack:
            self.boardw.board.pop()
            self.boardw.refresh()
            self.lbl_status.setText(self.boardw._status_text())

    def on_reset(self):
        self.boardw.board = chess.Board()
        self.boardw.origin = None
        self.boardw.cpl_sum_me = self.boardw.cpl_sum_engine = 0
        self.boardw.cpl_cnt_me = self.boardw.cpl_cnt_engine = 0
        self.boardw.refresh()
        self.lbl_status.setText(self.boardw._status_text())

def main():
    global engine
    engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
    engine.configure({"Threads": 2, "Hash": 256})
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(QSize(SIZE + 40, SIZE + 160))
    w.show()
    ret = app.exec()
    try: engine.quit()
    except: pass
    sys.exit(ret)

if __name__ == "__main__":
    main()
