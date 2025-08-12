# app.py (POV opuesto + motor mueve cuando le toca + historial/CPL en vivo)

import sys, random
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtSvgWidgets import QSvgWidget
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

# ---------- modos CPL ----------
CPL_MODES = {
    "Perfecto (≈1)": {"depth": 18, "mpv": 4,  "target": 1,  "perfect": True},
    "CPL 15":        {"depth": 14, "mpv": 6,  "target": 15, "perfect": False},
    "CPL 30":        {"depth": 12, "mpv": 8,  "target": 30, "perfect": False},
    "CPL 40":        {"depth": 10, "mpv": 10, "target": 40, "perfect": False},
}

engine = None
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
        mv = engine.play(board, chess.engine.Limit(depth=p["depth"]))
        mv = mv.move if hasattr(mv, 'move') else mv
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
        self.analysis_enabled = True

        # POV vs color humano (controlas el opuesto al POV)
        self.pov_color = chess.WHITE      # POV inicial = blancas
        self.human_color = chess.BLACK    # control inicial = negras
        self.flipped = False              # flipped si POV = negras

        # métricas CPL
        self.cpl_sum_me = 0; self.cpl_cnt_me = 0
        self.cpl_sum_engine = 0; self.cpl_cnt_engine = 0

        self.refresh()

    def set_pov_and_control(self, pov_color: chess.Color):
        # POV = pov_color; tú controlas el opuesto
        self.pov_color = pov_color
        self.human_color = chess.BLACK if pov_color == chess.WHITE else chess.WHITE
        self.flipped = (self.pov_color == chess.BLACK)
        self.refresh()

    # ---- CPL promedio
    def avg_cpl_me(self):
        return (self.cpl_sum_me / self.cpl_cnt_me) if self.cpl_cnt_me else 0.0
    def avg_cpl_engine(self):
        return (self.cpl_sum_engine / self.cpl_cnt_engine) if self.cpl_cnt_engine else 0.0

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
            self.parent().ui_turn_only()
            return
        self._try_play(sq)

    # ---- intento de jugada (humano)
    def _try_play(self, dst_sq):
        if self.board.turn != self.human_color:
            self.origin = None
            self.refresh()
            self.parent().ui_turn_only()
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
            self.parent().ui_turn_only()
            return

        # CPL de MI jugada
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

        self.board.push(mv)
        if self.analysis_enabled:
            self.cpl_sum_me += my_cpl; self.cpl_cnt_me += 1

        # limpia selección y refresca UI
        self.origin = None
        self.parent().ui_full_refresh()

        if self.board.is_game_over():
            return

        # Turno del motor
        self._engine_move_and_update()

    def _engine_move_and_update(self):
        mv_e, cpl_e = engine_pick_move(self.board, self.mode)
        self.board.push(mv_e)
        if self.analysis_enabled:
            self.cpl_sum_engine += cpl_e; self.cpl_cnt_engine += 1

        # refresco completo
        self.origin = None
        self.parent().ui_full_refresh()

# ---------- ventana ----------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ajedrez CPL – Gabriel Golker")
        self.setWindowIcon(QIcon(resource_path("assets/app.png")))

        # El selector controla el POV (vista). Tú controlas el opuesto.
        self.pov_color = chess.WHITE
        self.human_color = chess.BLACK
        self.engine_color = chess.WHITE

        self.board_frame = QFrame()
        self.board_frame.setFrameShape(QFrame.NoFrame)
        self.boardw = BoardWidget(parent=self)
        self.boardw.set_pov_and_control(self.pov_color)

        self.moves_list = QListWidget(); self.moves_list.setMinimumWidth(220)
        self.lbl_turn = QLabel("")
        self.lbl_cpl_me = QLabel("CPL (Yo): 0.0")
        self.lbl_cpl_engine = QLabel("CPL (Motor): 0.0")
        self.lbl_you_play = QLabel("Tú controlas: Negras")

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
        right.addWidget(self.lbl_you_play)
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
        self.ui_full_refresh()

        # <-- NUEVO: si arranca y es turno del motor (POV), que mueva
        if len(self.boardw.board.move_stack) == 0 and self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

    # ---------- helpers de UI ----------
    def ui_turn_only(self):
        if self.boardw.board.turn == chess.WHITE:
            self.lbl_turn.setText("Turno: Blancas")
            self.board_frame.setStyleSheet("QFrame { background-color: #142235; border-radius: 10px; }")
        else:
            self.lbl_turn.setText("Turno: Negras")
            self.board_frame.setStyleSheet("QFrame { background-color: #332014; border-radius: 10px; }")

    def rebuild_move_list_from_board(self):
        temp = chess.Board()
        self.moves_list.clear()
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

    def update_you_play_label(self):
        self.lbl_you_play.setText(f"Tú controlas: {'Blancas' if self.boardw.human_color == chess.WHITE else 'Negras'}")

    def ui_full_refresh(self):
        self.rebuild_move_list_from_board()
        self.update_cpl_labels()
        self.update_you_play_label()
        self.ui_turn_only()
        self.boardw.refresh()
        QApplication.processEvents()

    # ---------- eventos ----------
    def on_mode_changed(self, v):
        self.boardw.mode = v

    def on_side_changed(self, v):
        # Selector define el POV; tú controlas el opuesto
        if v.startswith("Blancas"):
            self.pov_color = chess.WHITE
        else:
            self.pov_color = chess.BLACK

        # Sincroniza roles
        self.boardw.set_pov_and_control(self.pov_color)
        self.human_color = self.boardw.human_color
        self.engine_color = self.pov_color

        # Si es inicio (o aún sin jugadas) y es turno del motor (POV), que mueva
        if len(self.boardw.board.move_stack) == 0 and self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

        self.ui_full_refresh()

    def on_analysis_toggled(self, state):
        self.boardw.analysis_enabled = (state == Qt.Checked)
        self.update_cpl_labels()

    def on_undo_pair(self):
        for _ in range(2):
            if self.boardw.board.move_stack:
                self.boardw.board.pop()
        self.boardw.origin = None
        self.ui_full_refresh()

    def on_reset(self):
        self.boardw.board = chess.Board()
        self.boardw.origin = None
        self.boardw.cpl_sum_me = self.boardw.cpl_sum_engine = 0
        self.boardw.cpl_cnt_me = self.boardw.cpl_cnt_engine = 0
        self.boardw.set_pov_and_control(self.pov_color)
        self.ui_full_refresh()
        if self.boardw.board.turn == self.engine_color:
            QTimer.singleShot(0, self.boardw._engine_move_and_update)

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

