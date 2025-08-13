# app.py (corregido)
# - Selector elige el **POV** (desde qué lado miras)
# - SI eliges POV = Negras -> controlas **BLANCAS** (opuesto) pero vista **volteada**
# - SI eliges POV = Blancas -> controlas **NEGRAS** (opuesto) pero vista **normal**
# - Historial y CPL en vivo

import sys, atexit, random
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtCore import Qt, QSize
import qdarkstyle
import chess, chess.engine, chess.svg

# ---------- recursos empaquetados ----------
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / rel)
    return str(Path(__file__).parent / rel)

ENGINE_PATH = resource_path("assets/stockfish/stockfish.exe")

# ---------- modos CPL ----------
CPL_MODES = {
    "Perfecto (≈1)": {"depth": 18, "mpv": 4,  "target": 1,  "perfect": True},

    # Rango “muy preciso”
    "CPL 15": {"depth": 14, "mpv": 6, "target": 15, "perfect": False},
    "CPL 20": {"depth": 13, "mpv": 7, "target": 20, "perfect": False},
    "CPL 25": {"depth": 13, "mpv": 8, "target": 25, "perfect": False},
    "CPL 30": {"depth": 12, "mpv": 8, "target": 30, "perfect": False},
    "CPL 35": {"depth": 12, "mpv": 9, "target": 35, "perfect": False},

    # Rango medio (buena naturalidad/velocidad)
    "CPL 40": {"depth": 10, "mpv": 10, "target": 40, "perfect": False},
    "CPL 45": {"depth": 10, "mpv": 11, "target": 45, "perfect": False},
    "CPL 50": {"depth": 10, "mpv": 12, "target": 50, "perfect": False},

    # Rango medio-alto (afinado para que el target sea estable)
    "CPL 55": {"depth": 11, "mpv": 13, "target": 55, "perfect": False},
    "CPL 60": {"depth": 11, "mpv": 14, "target": 60, "perfect": False},
    "CPL 65": {"depth": 11, "mpv": 15, "target": 65, "perfect": False},

    # Alto (más opciones para elegir jugadas “peores”)
    "CPL 70": {"depth": 8, "mpv": 16, "target": 70, "perfect": False},
},

engine = None
LAST_ENGINE_CPL = 0  # memoria corta del motor (tilt)
SIZE = 560
CELL = SIZE // 8

# ---------- utilidades de evaluación ----------
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

def _choose_by_target_cpl(lines, target_cpl: int, last_engine_cpl: int = 0):
    # mejor evaluación disponible para el bando al turno
    best_cp = lines[0][1]
    spread = best_cp - (lines[-1][1] if len(lines) > 1 else best_cp)

    # sigma dinámica: más grande en targets altos, posiciones complejas y tras blunder (tilt)
    sigma = max(4, int(target_cpl * (0.25 if target_cpl < 60 else 0.35)))
    sigma += min(30, int(spread * 0.10))  # complejidad de la posición
    if last_engine_cpl >= 120:            # venimos de error grande
        sigma += 20

    desired = max(0, int(random.gauss(target_cpl, sigma)))

    # Satisficing: acepto la primera lo bastante cercana al objetivo
    epsilon = max(3, int(target_cpl * 0.10))  # tolerancia
    for mv, cp in lines:
        delta = max(0, best_cp - cp)
        if delta >= max(0, desired - epsilon):
            return mv, delta

    # Fallback: elige la más cercana (comportamiento original)
    pick, best_diff, picked_cp = lines[0][0], float("inf"), lines[0][1]
    for mv, cp in lines:
        delta = max(0, best_cp - cp)
        diff = abs(delta - desired)
        if diff < best_diff:
            best_diff, pick, picked_cp = diff, mv, cp
    return pick, max(0, best_cp - picked_cp)
def engine_pick_move(board: chess.Board, mode_name: str):
    global LAST_ENGINE_CPL
    p = CPL_MODES[mode_name]
    lines = _multipv(board, depth=p["depth"], mpv=p["mpv"])
    if not lines:
        mv = engine.play(board, chess.engine.Limit(depth=p["depth"]))
        mv = mv.move if hasattr(mv, 'move') else mv
        LAST_ENGINE_CPL = 0
        return mv, 0
    if p["perfect"]:
        LAST_ENGINE_CPL = 0
        return lines[0][0], 0
    mv, cpl = _choose_by_target_cpl(lines, p["target"], LAST_ENGINE_CPL)
    LAST_ENGINE_CPL = cpl
    return mv, cpl
class BoardWidget(QSvgWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.board = chess.Board()
        self.origin = None
        self.mode = "CPL 30"
        self.analysis_enabled = True

        # Separar **POV** (vista) de **human_color** (quién juegas)
        self.pov_color = chess.WHITE     # POV inicial = blancas
        self.human_color = chess.BLACK   # control inicial = negras (opuesto al POV)
        self.flipped = False             # flipped si POV = negras

        # métricas CPL
        self.cpl_sum_me = 0; self.cpl_cnt_me = 0
        self.cpl_sum_engine = 0; self.cpl_cnt_engine = 0

        self.refresh()

    def set_pov_and_control(self, pov_color: chess.Color):
        # POV = pov_color; tú controlas el OPUESTO
        self.pov_color = pov_color
        self.human_color = chess.BLACK if pov_color == chess.WHITE else chess.WHITE
        self.flipped = (self.pov_color == chess.BLACK)
        self.refresh()

    # ---- CPL promedio
    def avg_cpl_me(self):
        return (self.cpl_sum_me / self.cpl_cnt_me) if self.cpl_cnt_me else 0.0
    def avg_cpl_engine(self):
        return (self.cpl_sum_engine / self.cpl_cnt_engine) if self.cpl_cnt_engine else 0.0

    # ---- conversión (x,y)->square considerando flip (según POV)
    def _sq_from_xy(self, x, y):
        if self.flipped:
            return y * 8 + (7 - x)   # negras abajo
        return (7 - y) * 8 + x       # blancas abajo

    # ---- render + resaltados
    def refresh(self):
        last_mv = self.board.peek() if self.board.move_stack else None
        squares = {}
        if self.origin is not None:
            squares[self.origin] = "#ffcccc"

        svg = chess.svg.board(
            board=self.board,
            flipped=self.flipped,              # POV
            size=SIZE,
            lastmove=last_mv,
            squares=squares,
            colors={"lastmove": "#ff6b6b"}
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
            try: self.parent().update_turn_highlight()
            except: pass
            return
        self._try_play(sq)

    # ---- intento de jugada (humano)
    def _try_play(self, dst_sq):
        if self.board.turn != self.human_color:
            self.origin = None
            self.refresh()
            try: self.parent().update_turn_highlight()
            except: pass
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
            try: self.parent().update_turn_highlight()
            except: pass
            return

        # CPL de MI jugada (si análisis activo)
        my_cpl = 0
        if self.analysis_enabled:
            p = CPL_MODES[self.mode]
            lines = _multipv(self.board, depth=p["depth"], mpv=max(6, p["mpv"]))
            if lines:
                best_cp = lines[0][1]
                chosen_cp = None
                for mv_i, cp_i in lines:
                    if mv_i == mv:
                        chosen_cp = cp_i; break
                if chosen_cp is None:
                    tmp = self.board.copy(); tmp.push(mv)
                    info = engine.analyse(tmp, chess.engine.Limit(depth=p["depth"]))
                    chosen_cp = -_score_to_cp(info["score"], pov_white=(tmp.turn == chess.WHITE))
                my_cpl = max(0, best_cp - chosen_cp)

        san = self.board.san(mv)
        self.board.push(mv)
        if self.analysis_enabled:
            self.cpl_sum_me += my_cpl; self.cpl_cnt_me += 1
        try:
            self.parent().rebuild_move_list_from_board()
        except: pass
        self.refresh()
        try:
            self.parent().update_turn_highlight()
            self.parent().update_cpl_labels()
        except: pass

        if self.board.is_game_over():
            return

        self._engine_move_and_update()

    def _engine_move_and_update(self):
        mv_e, cpl_e = engine_pick_move(self.board, self.mode)
        san_e = self.board.san(mv_e)
        self.board.push(mv_e)
        if self.analysis_enabled:
            self.cpl_sum_engine += cpl_e; self.cpl_cnt_engine += 1
        try:
            self.parent().rebuild_move_list_from_board()
        except: pass
        self.refresh()
        try:
            self.parent().update_turn_highlight()
            self.parent().update_cpl_labels()
        except: pass

# ---------- ventana ----------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ajedrez CPL")
        self.setWindowIcon(QIcon(resource_path("assets/app.png")))

        # Selector controla el **POV** (vista). El control humano es el opuesto.
        self.pov_color = chess.WHITE
        self.human_color = chess.BLACK   # opuesto
        self.engine_color = chess.WHITE  # el motor juega el POV

        self.board_frame = QFrame()
        self.board_frame.setFrameShape(QFrame.NoFrame)
        self.boardw = BoardWidget(parent=self)
        self.boardw.set_pov_and_control(self.pov_color)

        self.moves_list = QListWidget(); self.moves_list.setMinimumWidth(220)
        self.lbl_turn = QLabel("")
        self.lbl_cpl_me = QLabel("CPL (Yo): 0.0")
        self.lbl_cpl_engine = QLabel("CPL (Motor): 0.0")

        self.cmb_mode = QComboBox(); self.cmb_mode.addItems(CPL_MODES.keys()); self.cmb_mode.setCurrentText("CPL 30")
        self.cmb_side = QComboBox(); self.cmb_side.addItems(["Blancas (POV)", "Negras (POV)"])
        self.cmb_side.setCurrentText("Blancas (POV)")
        self.chk_analysis = QCheckBox("Análisis (CPL en vivo)"); self.chk_analysis.setChecked(True)

        self.btn_undo = QPushButton("Deshacer (2 jugadas)")
        self.btn_reset = QPushButton("Reiniciar")

        top = QHBoxLayout()
        top.addWidget(QLabel("CPL:")); top.addWidget(self.cmb_mode)
        top.addWidget(QLabel("POV:")); top.addWidget(self.cmb_side)
        top.addWidget(self.chk_analysis)
        top.addStretch(1)
        top.addWidget(self.btn_undo); top.addWidget(self.btn_reset)

        bf_layout = QVBoxLayout(self.board_frame); bf_layout.setContentsMargins(8,8,8,8)
        bf_layout.addWidget(self.boardw, 0, Qt.AlignCenter)

        right = QVBoxLayout()
        right.addWidget(QLabel("Historial"), 0, Qt.AlignTop)
        right.addWidget(self.moves_list, 1)
        right.addWidget(self.lbl_turn)
        right.addWidget(self.lbl_cpl_me)
        right.addWidget(self.lbl_cpl_engine)
        right.addWidget(QLabel("© 2025 Gabriel Golker"), 0, Qt.AlignBottom)

        root = QVBoxLayout(self)
        root.addLayout(top)
        mid = QHBoxLayout(); mid.addWidget(self.board_frame, 0, Qt.AlignCenter); mid.addLayout(right, 0)
        root.addLayout(mid)

        self.cmb_mode.currentTextChanged.connect(self.on_mode_changed)
        self.cmb_side.currentTextChanged.connect(self.on_side_changed)
        self.chk_analysis.stateChanged.connect(self.on_analysis_toggled)
        self.btn_undo.clicked.connect(self.on_undo_pair)
        self.btn_reset.clicked.connect(self.on_reset)

        self.setStyleSheet(qdarkstyle.load_stylesheet())
        self.update_turn_highlight(); self.update_cpl_labels()

    # ---------- UI helpers ----------
    def update_turn_highlight(self):
        if self.boardw.board.turn == chess.WHITE:
            self.lbl_turn.setText("Turno: Blancas")
            self.board_frame.setStyleSheet("QFrame { background-color: #142235; border-radius: 10px; }")
        else:
            self.lbl_turn.setText("Turno: Negras")
            self.board_frame.setStyleSheet("QFrame { background-color: #332014; border-radius: 10px; }")

    def rebuild_move_list_from_board(self):
        temp = chess.Board(); self.moves_list.clear()
        for mv in self.boardw.board.move_stack:
            san = temp.san(mv)
            ply = len(temp.move_stack) + 1
            move_num = (ply + 1) // 2
            if ply % 2 == 1:
                self.moves_list.addItem(QListWidgetItem(f"{move_num}. {san}"))
            else:
                last_row = self.moves_list.count() - 1
                if last_row >= 0:
                    item = self.moves_list.item(last_row)
                    item.setText(item.text() + f"   {san}")
                else:
                    self.moves_list.addItem(QListWidgetItem(f"{move_num}. ... {san}"))
            temp.push(mv)
        self.moves_list.scrollToBottom()

    def update_cpl_labels(self):
        self.lbl_cpl_me.setText(f"CPL (Yo): {self.boardw.avg_cpl_me():.1f}")
        self.lbl_cpl_engine.setText(f"CPL (Motor): {self.boardw.avg_cpl_engine():.1f}")

    # ---------- eventos ----------
    def on_mode_changed(self, v):
        self.boardw.mode = v

    def on_side_changed(self, v):
        # El selector define el **POV**. El control humano es el OPUESTO.
        if v.startswith("Blancas"):
            self.pov_color = chess.WHITE
        else:
            self.pov_color = chess.BLACK

        self.human_color = chess.BLACK if self.pov_color == chess.WHITE else chess.WHITE
        self.engine_color = self.pov_color

        self.boardw.set_pov_and_control(self.pov_color)

        # Si es el comienzo y le toca al motor (POV), que mueva primero
        if len(self.boardw.board.move_stack) == 0 and self.boardw.board.turn == self.engine_color:
            self.boardw._engine_move_and_update()

        self.rebuild_move_list_from_board(); self.update_turn_highlight(); self.update_cpl_labels()

    def on_analysis_toggled(self, state):
        self.boardw.analysis_enabled = (state == Qt.Checked)
        self.update_cpl_labels()

    def on_undo_pair(self):
        for _ in range(2):
            if self.boardw.board.move_stack:
                self.boardw.board.pop()
        self.boardw.origin = None
        self.rebuild_move_list_from_board(); self.boardw.refresh(); self.update_turn_highlight(); self.update_cpl_labels()

    def on_reset(self):
        self.boardw.board = chess.Board(); self.boardw.origin = None
        self.boardw.cpl_sum_me = self.boardw.cpl_sum_engine = 0
        self.boardw.cpl_cnt_me = self.boardw.cpl_cnt_engine = 0
        self.moves_list.clear()
        # POV y control actuales se mantienen
        self.boardw.set_pov_and_control(self.pov_color)
        self.boardw.refresh(); self.update_turn_highlight(); self.update_cpl_labels()
        if self.boardw.board.turn == self.engine_color:
            self.boardw._engine_move_and_update()

# ---------- main ----------
def main():
    global engine
    engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
    engine.configure({"Threads": 2, "Hash": 256})

    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(QSize(SIZE + 300, SIZE + 180))
    w.show()
    ret = app.exec()

    try: engine.quit()
    except: pass
    sys.exit(ret)

if __name__ == "__main__":
    main()
