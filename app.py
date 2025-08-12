# app.py (simple: POV opuesto, sin historial ni CPL en vivo)

import sys, random
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QFrame
)
from PySide6.QtSvgWidgets import QSvgWidget
    # Nota: quitamos QCheckBox, QListWidget, QListWidgetItem
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtCore import Qt, QSize, QTimer
import qdarkstyle
import chess, chess.engine, chess.svg

# ---------- recursos empaquetados ----------
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / rel)
    return str(Path(__file__).parent / rel)

ENGINE_PATH = resource_path("assets/stockfish/stockfish.exe")

# ---------- modos CPL (para fuerza/estilo del motor) ----------
CPL_MODES = {
    "Perfecto (≈1)": {"depth": 18, "mpv": 4,  "target": 1,  "perfect": True},
    "CPL 15":        {"depth": 14, "mpv": 6,  "target": 15, "perfect": False},
    "CPL 30":        {"depth": 12, "mpv": 8,  "target": 30, "perfect": False},
    "CPL 40":        {"depth": 10, "mpv": 10, "target": 40, "perfect": False},
}

engine = None
SIZE = 560
CELL = SIZE // 8

# ---------- helpers evaluación (solo para el motor) ----------
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
        pv = i.get("pv"); sc = i.get("score")
        if not pv or not sc:
            continue
        mv = pv[0]; cp = _score_to_cp(sc, pov_white)
        lines.append((mv, cp))
    lines.sort(key=lambda t: t[1], reverse=True)
    return lines

def _choose_by_target_cpl(lines, target_cpl: int):
    best_cp = lines[0][1]
    desired = max(0, int(random.gauss(target_cpl, max(4, target_cpl*0.25))))
    pick, best_diff, picked_cp = lines[0][0], float("inf"), lines[0][1]
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
        res = engine.play(board, chess.engine.Limit(depth=p["depth"]))
        mv = res.move if hasattr(res, "move") else res
        return mv, 0
    if p["perfect"]:
        return lines[0][0], 0
    mv, cpl = _choose_by_target_cpl(lines, p["target"])
    return mv, cpl

# ---------- tablero ----------
class BoardWidget(QSvgWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.board = chess.Board()
        self.origin = None
        self.mode = "CPL 30"

        # POV vs color humano (controlas el opuesto al POV)
        self.pov_color = chess.WHITE      # POV inicial = blancas
        self.human_color = chess.BLACK    # control inicial = negras
        self.flipped = False              # flipped si POV = negras

        self.refresh()

    def set_pov_and_control(self, pov_color: chess.Color):
        # POV = pov_color; tú controlas el opuesto
        self.pov_color = pov_color
        self.human_color = chess.BLACK if pov_color == chess.WHITE else chess.WHITE
        self.flipped = (self.pov_color == chess.BLACK)
        self.refresh()

    # ---- conversión (x,y)->square según POV
    def _sq_from_xy(self, x, y):
        if self.flipped:
            return y * 8 + (7 - x)   # negras abajo
        return (7 - y) * 8 + x       # blancas abajo

    # ---- render + resaltados
    def refresh(self):
        last_mv = self.board.peek() if self.board.move_stack else None
        squares = {}
        if self.origin is not None:
            squares[self.origin] = "#ffd27f"  # selección (ámbar suave)

        svg = chess.svg.board(
            board=self.board,
            flipped=self.flipped,
            size=SIZE,
            lastmove=last_mv,
            squares=squares,
            colors={"lastmove": "#4caf50"}  # último movimiento en verde suave
        )
        self.load(bytearray(svg, encoding="utf-8"))

    # ---- input de mouse
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() != Qt.LeftButton:
            return
        x = int(e.position().x()) // CELL
        y = int(e.position().y()) // CELL
        sq = self._sq_from_xy(x, y)
        if self.origin is None:
            self.origin = sq
            self.refresh()
            self.parent().update_turn_highlight()
            return
        self._try_play(sq)

    # ---- intento de jugada (humano)
    def _try_play(self, dst_sq):
        if self.board.turn != self.human_color:
            self.origin = None
            self.refresh()
            self.parent().update_turn_highlight()
            return

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
            self.parent().update_turn_highlight()
            return

        # aplica MI jugada (sin CPL)
        self.board.push(mv)
        self.refresh()
        self.parent().update_turn_highlight()

        if self.board.is_game_over():
            return

        # turno del motor
        self._engine_move_and_update()

    def _engine_move_and_update(self):
        mv_e, _ = engine_pick_move(self.board, self.mode)
        self.board.push(mv_e)
        self.refresh()
        self.parent().update_turn_highlight()

# ---------- ventana ----------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ajedrez CPL")
        self.setWindowIcon(QIcon(resource_path("assets/app.png")))

        # El selector controla el POV (vista). Tú controlas el opuesto.
        self.pov_color = chess.WHITE
        self.human_color = chess.BLACK
        self.engine_color = chess.WHITE

        self.board_frame = QFrame()
        self.board_frame.setFrameShape(QFrame.NoFrame)
        self.boardw = BoardWidget(parent=self)
        self.boardw.set_pov_and_control(self.pov_color)

        # ------- controles mínimos -------
        self.lbl_turn = QLabel("")
        self.cmb_mode = QComboBox(); self.cmb_mode.addItems(CPL_MODES.keys()); self.cmb_mode.setCurrentText("CPL 30")
        self.cmb_side = QComboBox(); self.cmb_side.addItems(["Blancas (POV)", "Negras (POV)"])
        self.cmb_side.setCurrentText("Blancas (POV)")

        self.btn_undo = QPushButton("Deshacer (2 jugadas)")
        self.btn_reset = QPushButton("Reiniciar")

        # top bar
        top = QHBoxLayout()
        top.addWidget(QLabel("CPL:")); top.addWidget(self.cmb_mode)
        top.addWidget(QLabel("POV:")); top.addWidget(self.cmb_side)
        top.addStretch(1)
        top.addWidget(self.btn_undo); top.addWidget(self.btn_reset)

        # layout tablero centrado
        bf_layout = QVBoxLayout(self.board_frame); bf_layout.setContentsMargins(8,8,8,8)
        bf_layout.addWidget(self.boardw, 0, Qt.AlignCenter)

        # root
        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.board_frame, 0, Qt.AlignCenter)
        root.addWidget(self.lbl_turn)

        # señales
        self.cmb_mode.currentTextChanged.connect(self.on_mode_changed)
        self.cmb_side.currentTextChanged.connect(self.on_side_changed)
        self.btn_undo.clicked.connect(self.on_undo_pair)
        self.btn_reset.clicked.connect(self.on_reset)

        # tema oscuro
        self.setStyleSheet(qdarkstyle.load_stylesheet())

        # estado inicial
        self.update_turn_highlight()

        # Si al iniciar es turno del motor (POV), que mueva primero
        if len(self.boardw.board.move_stack) == 0 and self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

    # ---------- UI helpers ----------
    def update_turn_highlight(self):
        if self.boardw.board.turn == chess.WHITE:
            self.lbl_turn.setText("Turno: Blancas")
            self.board_frame.setStyleSheet("QFrame { background-color: #142235; border-radius: 10px; }")
        else:
            self.lbl_turn.setText("Turno: Negras")
            self.board_frame.setStyleSheet("QFrame { background-color: #332014; border-radius: 10px; }")

    # ---------- eventos ----------
    def on_mode_changed(self, v):
        self.boardw.mode = v

    def on_side_changed(self, v):
        # Selector define el POV; tú controlas el opuesto
        if v.startswith("Blancas"):
            self.pov_color = chess.WHITE
        else:
            self.pov_color = chess.BLACK

        self.boardw.set_pov_and_control(self.pov_color)
        self.human_color = self.boardw.human_color
        self.engine_color = self.pov_color

        # Si es inicio y es turno del motor (POV), que mueva primero
        if len(self.boardw.board.move_stack) == 0 and self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

        self.update_turn_highlight()

    def on_undo_pair(self):
        # deshace 2 jugadas (motor y tuya) si existen
        for _ in range(2):
            if self.boardw.board.move_stack:
                self.boardw.board.pop()
        self.boardw.origin = None
        self.boardw.refresh()
        self.update_turn_highlight()

    def on_reset(self):
        self.boardw.board = chess.Board()
        self.boardw.origin = None
        # mantener POV/roles
        self.boardw.set_pov_and_control(self.pov_color)
        self.boardw.refresh()
        self.update_turn_highlight()
        if self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

# ---------- main ----------
def main():
    global engine
    engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
    engine.configure({"Threads": 2, "Hash": 256})

    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(QSize(SIZE + 120, SIZE + 140))  # un poco más compacto
    w.show()
    ret = app.exec()

    try: engine.quit()
    except: pass
    sys.exit(ret)

if __name__ == "__main__":
    main()
