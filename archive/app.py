import sys
import math
import time
import os
import psutil
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from dotenv import load_dotenv
from groq import Groq

# Core module imports
from core.prompt_kernel import CORE_SYSTEM_PROMPT
from core.voice_engine import speak
from core.listener import listen_and_transcribe

load_dotenv()

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

# ==========================================
# 1. AUTONOMOUS AUDIO & COGNITIVE THREAD
# ==========================================
class AutonomousBrain(QThread):
    ui_update = pyqtSignal(str, str, str)  # State, Subtext, Color Hex
    log_update = pyqtSignal(str)           # Command log entry

    def __init__(self):
        super().__init__()
        self.is_running = True
        try:
            self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        except Exception as e:
            print(f"[Groq Error]: {e}")
            self.client = None

    def run(self):
        time.sleep(1.0)
        while self.is_running:
            if self.client:
                self._run_cycle()
            else:
                time.sleep(1)

    def _run_cycle(self):
        self.ui_update.emit("LISTENING", "Listening for voice command...", "#FF2A6D")
        user_text = listen_and_transcribe(self.client)

        if not user_text or not user_text.strip():
            self.ui_update.emit("IDLE", "System Standby // Mic Active", "#00E5FF")
            time.sleep(0.2)
            return

        self.log_update.emit(user_text)

        self.ui_update.emit("THINKING", f'"{user_text}"', "#38BDF8")
        try:
            response = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": CORE_SYSTEM_PROMPT + "\nKeep answers concise and direct."},
                    {"role": "user", "content": user_text}
                ],
                max_tokens=80,
                temperature=0.3
            )
            reply = response.choices[0].message.content
            
            self.ui_update.emit("SPEAKING", reply, "#10B981")
            speak(reply)
        except Exception as e:
            self.ui_update.emit("IDLE", f"Error: {e}", "#FF2A6D")

        self.ui_update.emit("IDLE", "System Standby // Mic Active", "#00E5FF")


# ==========================================
# 2. VECTOR VISUALIZER
# ==========================================
class OrbitalVisualizer(QWidget):
    def __init__(self):
        super().__init__()
        self.angle = 0
        self.state_color = QColor("#00E5FF")
        self.pulse = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(25)

    def set_color(self, hex_color):
        self.state_color = QColor(hex_color)

    def animate(self):
        self.angle = (self.angle + 1.5) % 360
        self.pulse = (math.sin(math.radians(self.angle * 2.5)) + 1) / 2
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        base_radius = min(w, h) / 2.6

        painter.translate(cx, cy)

        halo_gradient = QRadialGradient(0, 0, base_radius * 1.25)
        c_outer = QColor(self.state_color)
        c_outer.setAlpha(int(35 + self.pulse * 30))
        halo_gradient.setColorAt(0, c_outer)
        halo_gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(halo_gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(0, 0), base_radius * 1.25, base_radius * 1.25)

        pen = QPen(self.state_color)
        pen.setWidthF(1.5)
        painter.setPen(pen)

        painter.rotate(-self.angle * 0.8)
        for i in range(24):
            if i % 2 == 0:
                painter.drawLine(int(base_radius * 1.05), 0, int(base_radius * 1.15), 0)
            painter.rotate(15)
        painter.rotate(self.angle * 0.8)

        pen.setStyle(Qt.DashLine)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.rotate(self.angle)
        painter.drawEllipse(QPointF(0, 0), base_radius, base_radius)
        painter.rotate(-self.angle)

        pen.setStyle(Qt.SolidLine)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(0, 0), base_radius * 0.72, base_radius * 0.72)

        core_r = (base_radius * 0.32) + (self.pulse * 7)
        core_grad = QRadialGradient(0, 0, core_r)
        c_core_center = QColor(self.state_color)
        c_core_center.setAlpha(220)
        c_core_edge = QColor(self.state_color)
        c_core_edge.setAlpha(80)
        
        core_grad.setColorAt(0, c_core_center)
        core_grad.setColorAt(1, c_core_edge)
        
        painter.setBrush(QBrush(core_grad))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(0, 0), core_r, core_r)


# ==========================================
# 3. HUD WINDOW WITH EXPLICIT CLOSE BUTTON
# ==========================================
class HolographicHUD(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, 1280, 1024)

        # Allow dragging window with left click
        self.drag_position = QPoint()

        self._build_ui()

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self._update_telemetry)
        self.telemetry_timer.start(1500)

        self.brain = AutonomousBrain()
        self.brain.ui_update.connect(self._handle_ui_update)
        self.brain.log_update.connect(self._handle_log_update)
        self.brain.start()

    def _build_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)
        self.setCentralWidget(main_widget)

        glass_stylesheet = """
            QFrame {
                background-color: rgba(6, 15, 30, 0.88);
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #CBD5E1;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """

        # TOP BAR WITH CLOSE & MINIMIZE BUTTONS
        top_bar = QFrame()
        top_bar.setFixedHeight(44)
        top_bar.setStyleSheet(glass_stylesheet)

        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(15, 0, 10, 0)

        sys_title = QLabel("NEXUS // AUTONOMOUS OS HUD")
        sys_title.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 12px;")
        
        self.status_indicator = QLabel("● SYSTEM ONLINE")
        self.status_indicator.setStyleSheet("color: #10B981; font-weight: bold; font-size: 11px;")

        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("color: #94A3B8; font-size: 11px;")

        # Window Action Buttons
        min_btn = QPushButton("—")
        min_btn.setFixedSize(28, 28)
        min_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 229, 255, 0.15);
                border: 1px solid #00E5FF;
                border-radius: 5px;
                color: #00E5FF;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 229, 255, 0.4);
            }
        """)
        min_btn.clicked.connect(self.showMinimized)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 42, 109, 0.2);
                border: 1px solid #FF2A6D;
                border-radius: 5px;
                color: #FF2A6D;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 42, 109, 0.8);
                color: #FFFFFF;
            }
        """)
        close_btn.clicked.connect(self.close)

        top_layout.addWidget(sys_title)
        top_layout.addSpacing(15)
        top_layout.addWidget(self.status_indicator)
        top_layout.addStretch()
        top_layout.addWidget(self.clock_label)
        top_layout.addSpacing(15)
        top_layout.addWidget(min_btn)
        top_layout.addWidget(close_btn)

        main_layout.addWidget(top_bar)

        # CENTER GRID
        center_grid = QHBoxLayout()
        center_grid.setSpacing(12)

        # LEFT PANEL (260px)
        left_panel = QFrame()
        left_panel.setFixedWidth(260)
        left_panel.setStyleSheet(glass_stylesheet)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)

        t_header = QLabel("┼ SYSTEM TELEMETRY")
        t_header.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 12px;")
        left_layout.addWidget(t_header)
        left_layout.addSpacing(8)

        self.cpu_label = QLabel("CPU Load: --%")
        self.ram_label = QLabel("RAM Usage: --%")
        self.disk_label = QLabel("Disk Storage: --%")
        
        for lbl in [self.cpu_label, self.ram_label, self.disk_label]:
            lbl.setStyleSheet("font-size: 11px; color: #E2E8F0;")
            left_layout.addWidget(lbl)

        left_layout.addStretch()

        # CENTER PANEL
        center_panel = QVBoxLayout()
        center_panel.setSpacing(12)
        
        self.visualizer = OrbitalVisualizer()
        
        self.subtitle_banner = QLabel("SYSTEM STANDBY // MIC ACTIVE")
        self.subtitle_banner.setFixedHeight(50)
        self.subtitle_banner.setStyleSheet("""
            background-color: rgba(6, 15, 30, 0.88);
            border: 1px solid #00E5FF;
            border-radius: 8px;
            color: #00E5FF;
            font-size: 12px;
            font-style: italic;
            padding: 8px 12px;
        """)
        self.subtitle_banner.setAlignment(Qt.AlignCenter)
        self.subtitle_banner.setWordWrap(True)

        center_panel.addWidget(self.visualizer, stretch=1)
        center_panel.addWidget(self.subtitle_banner)

        # RIGHT PANEL (260px)
        right_panel = QFrame()
        right_panel.setFixedWidth(260)
        right_panel.setStyleSheet(glass_stylesheet)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)

        r_header = QLabel("┼ RECENT COMMANDS")
        r_header.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 12px;")
        right_layout.addWidget(r_header)
        right_layout.addSpacing(8)

        self.log_list = QListWidget()
        self.log_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                color: #94A3B8;
                font-family: 'Consolas';
                font-size: 10px;
            }
            QListWidget::item {
                padding: 3px 0px;
            }
        """)
        self.log_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_layout.addWidget(self.log_list)

        center_grid.addWidget(left_panel)
        center_grid.addLayout(center_panel, stretch=1)
        center_grid.addWidget(right_panel)

        main_layout.addLayout(center_grid)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    def _update_telemetry(self):
        self.clock_label.setText(time.strftime("%a, %b %d, %Y | %I:%M:%S %p"))
        self.cpu_label.setText(f"CPU Load: {psutil.cpu_percent()}%")
        self.ram_label.setText(f"RAM Usage: {psutil.virtual_memory().percent}%")
        self.disk_label.setText(f"Disk Storage: {psutil.disk_usage('/').percent}%")

    def _handle_ui_update(self, state, text, hex_color):
        self.subtitle_banner.setText(text)
        self.subtitle_banner.setStyleSheet(f"""
            background-color: rgba(6, 15, 30, 0.88);
            border: 1px solid {hex_color};
            border-radius: 8px;
            color: {hex_color};
            font-size: 12px;
            font-style: italic;
            padding: 8px 12px;
        """)
        self.status_indicator.setText(f"● {state}")
        self.status_indicator.setStyleSheet(f"color: {hex_color}; font-weight: bold; font-size: 11px;")
        self.visualizer.set_color(hex_color)

    def _handle_log_update(self, text):
        t_str = time.strftime("%H:%M:%S")
        self.log_list.addItem(f"[{t_str}] {text}")
        self.log_list.scrollToBottom()

    def closeEvent(self, event):
        self.brain.is_running = False
        self.brain.wait()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    hud = HolographicHUD()
    hud.show()
    sys.exit(app.exec_())