# main.py — OmniForge App Orçamento (build unificado)
# - Janela ultra-estreita responsiva (resize por bordas + grips + modo compacto)
# - Minimiza para bandeja (system tray)
# - Enfileira até 10 imagens; upload individual ao R2 (S3) com AWS SigV4
# - Payload ao webhook envia SOMENTE links públicos
# - Após webhook OK, deleta os objetos do bucket (limpeza)
# - Sem teste de S3 na UI; Webhook com teste seguro
# - Tratamento robusto de slots para evitar fechamentos abruptos
# - Logs sem vazar segredos

import os, sys, uuid, datetime, hmac, hashlib, json
from pathlib import Path
from urllib.parse import quote
from typing import Optional, List

from PySide6.QtCore import (
    Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot, Signal,
    QSettings, QTimer, QEvent, QSize, QRect
)
from PySide6.QtGui import (
    QGuiApplication, QPixmap, QShortcut, QKeySequence, QIcon, QImage, QAction
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QSizePolicy, QScrollArea, QDialog, QDialogButtonBox, QTabWidget, QStyle,
    QSystemTrayIcon, QMenu, QStackedLayout, QSizeGrip
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply,
    QSslConfiguration, QSsl
)
import logging, logging.handlers

# ===================== R2 (S3) PREDEFINIÇÕES (ocultas) =====================
R2_DEFAULTS = {
    "account_id": "0245b00ef3744d9e0e07f785971bb90a",
    "bucket":     "imagensorcamento",
    "endpoint":   "https://0245b00ef3744d9e0e07f785971bb90a.r2.cloudflarestorage.com",
    "public_base":"https://pub-7427f77596074193ae789abf82e57fd6.r2.dev",  # sem bucket no caminho
    "prefix":     "orcamentos/",
    "cache_ctrl": "public, max-age=31536000, immutable",
    "key_id":     "8640c7754ac38ddd0e93db858a8ec5a0",
    "key_secret": "dccdeb3f153c03c3c5b5631ad505706e98c950bd38784eea11b68a1a4e5eac21",
}

# ===================== LOGGING =====================
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'app_orcamento.log'
log_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('app_orcamento'); logger.setLevel(logging.INFO)
if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
    logger.addHandler(log_handler)

# ===================== Utilitários =====================
def qimage_to_png_bytes(img) -> bytes:
    """Serializa QImage/QPixmap para PNG (deep copy p/ evitar reuso de buffer do clipboard)."""
    if isinstance(img, QPixmap):
        qimg = img.toImage()
    else:
        qimg = img
    # deep copy para não referenciar memória volátil do clipboard/QMimeData
    qimg = qimg.copy()
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
    qimg.save(buf, "PNG", 92); buf.close()
    return bytes(ba)

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def aws_v4_sign(key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = hmac.new(("AWS4" + key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()

def iso8601_basic(dt: datetime.datetime) -> (str, str):
    return dt.strftime('%Y%m%dT%H%M%SZ'), dt.strftime('%Y%m%d')

# ===================== UI: Preview de imagem =====================
class ImagePreviewItem(QWidget):
    removed = Signal(QWidget)
    def __init__(self, pixmap: QPixmap, filename: str, token: str):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(5,5,5,5); lay.setSpacing(6)
        thumb = QLabel(); thumb.setPixmap(pixmap.scaled(60,50,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        name = QLabel(filename); name.setWordWrap(True); name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        rm = QPushButton("X"); rm.setFixedSize(22,22)
        rm.setStyleSheet("QPushButton{border-radius:11px;background:rgba(255,255,255,0.08);} QPushButton:hover{background:rgba(255,80,80,0.9);}")
        rm.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(thumb); lay.addWidget(name, 1); lay.addWidget(rm)
        self._token = token

# ===================== Diálogo de Configurações =====================
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(480)
        self.settings = QSettings("OmniForge", "AppOrcamento")
        self.nam = QNetworkAccessManager(self)

        # Semeia defaults S3/R2 ocultos
        if not self.settings.value("r2_account_id", ""):
            for k, v in (
                ("r2_account_id",  R2_DEFAULTS["account_id"]),
                ("r2_bucket",      R2_DEFAULTS["bucket"]),
                ("r2_endpoint",    R2_DEFAULTS["endpoint"]),
                ("r2_public",      R2_DEFAULTS["public_base"]),
                ("r2_prefix",      R2_DEFAULTS["prefix"]),
                ("r2_cache",       R2_DEFAULTS["cache_ctrl"]),
                ("r2_key_id",      R2_DEFAULTS["key_id"]),
                ("r2_key_secret",  R2_DEFAULTS["key_secret"]),
            ):
                self.settings.setValue(k, v)
            self.settings.sync()

        main = QVBoxLayout(self)
        tabs = QTabWidget(); main.addWidget(tabs)

        # Aba Geral
        tab_g = QWidget(); lay_g = QVBoxLayout(tab_g)
        self.seller_name_input = QLineEdit(self.settings.value("seller_name", ""))
        lay_g.addWidget(QLabel("Nome do Vendedor:")); lay_g.addWidget(self.seller_name_input); lay_g.addStretch()
        tabs.addTab(tab_g, "Geral")

        # Aba Webhook
        tab_w = QWidget(); lay_w = QVBoxLayout(tab_w)
        self.webhook_url_input = QLineEdit(self.settings.value("webhook_url", ""))
        btn_test_w = QPushButton("Testar Webhook"); btn_test_w.clicked.connect(self.test_webhook)
        self.status_lbl = QLabel("Status: pronto.")
        row_btns = QHBoxLayout(); row_btns.addWidget(btn_test_w); row_btns.addStretch()
        lay_w.addWidget(QLabel("URL do Webhook:")); lay_w.addWidget(self.webhook_url_input)
        lay_w.addLayout(row_btns); lay_w.addWidget(self.status_lbl); lay_w.addStretch()
        tabs.addTab(tab_w, "Webhook")

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        main.addWidget(box)

    def test_webhook(self):
        try:
            url = (self.webhook_url_input.text() or "").strip()
            if not url:
                self.status_lbl.setText("Status: informe a URL do webhook.")
                self.status_lbl.setStyleSheet("color:#ffc107;"); return
            req = QNetworkRequest(QUrl(url))
            req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            self.status_lbl.setText("Status: Testando webhook…")
            self.status_lbl.setStyleSheet("color:#17a2b8;")

            self._webhook_test_reply = self.nam.post(req, b'{"ping":"ok"}')

            def done():
                try:
                    rep = self._webhook_test_reply
                    if rep is None: return
                    st = rep.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                    if rep.error() == QNetworkReply.NoError and (st is None or st < 400):
                        self.status_lbl.setText(f"Webhook OK (HTTP {st})")
                        self.status_lbl.setStyleSheet("color:#28a745;")
                    else:
                        self.status_lbl.setText(f"Webhook falhou: {rep.errorString()} (HTTP {st})")
                        self.status_lbl.setStyleSheet("color:#dc3545;")
                except Exception as e:
                    self.status_lbl.setText(f"Erro interno no teste: {e}")
                    self.status_lbl.setStyleSheet("color:#dc3545;")
                finally:
                    try:
                        self._webhook_test_reply.deleteLater()
                    except Exception:
                        pass
                    self._webhook_test_reply = None

            self._webhook_test_reply.finished.connect(done)

        except Exception as e:
            self.status_lbl.setText(f"Falha ao iniciar teste: {e}")
            self.status_lbl.setStyleSheet("color:#dc3545;")

    def accept(self):
        self.settings.setValue("seller_name", self.seller_name_input.text().strip())
        self.settings.setValue("webhook_url", self.webhook_url_input.text().strip())
        self.settings.sync(); super().accept()

    def closeEvent(self, e):
        try:
            if hasattr(self, "_webhook_test_reply") and self._webhook_test_reply and not self._webhook_test_reply.isFinished():
                self._webhook_test_reply.abort()
        except Exception:
            pass
        super().closeEvent(e)

# ===================== Janela Principal =====================
class FloatingWidget(QWidget):
    RESIZE_MARGIN = 6

    def __init__(self):
        super().__init__()
        self.image_queue: List[dict] = []
        self.settings = QSettings("OmniForge", "AppOrcamento")

        # Sem moldura, sempre no topo
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Ultra-estreita
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(150, 140)  # mais fina ainda
        self.resize(max(240, int(screen.width()*0.18)),
                    max(520, int(screen.height()*0.52)))

        self._drag_pos = QPoint()
        self._resizing = False
        self._resize_region = None
        self._start_geo = QRect()
        self._start_mouse = QPoint()
        self.setMouseTracking(True)

        # ===== UI
        self._container = QWidget(self); self._container.setObjectName("card"); self._container.setMouseTracking(True)
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.addWidget(self._container)
        lay = QVBoxLayout(self._container); lay.setContentsMargins(10,10,10,10); lay.setSpacing(8)

        title_row = QHBoxLayout()
        self.title = QLabel("OmniForge — App Orçamento"); self.title.setStyleSheet("font-weight:600;")
        self.settings_btn = QPushButton()
        icon = QIcon.fromTheme("settings") or QIcon()
        if icon.isNull(): icon = self.style().standardIcon(QStyle.SP_FileIcon)
        self.settings_btn.setIcon(icon); self.settings_btn.setFixedSize(24,24)
        self.settings_btn.setStyleSheet("QPushButton{border-radius:12px; padding:0;}")
        self.settings_btn.clicked.connect(self.open_settings)

        self.minimize_btn = QPushButton(); self.minimize_btn.setIcon(self.style().standardIcon(QStyle.SP_TitleBarMinButton))
        self.minimize_btn.setFixedSize(24,24); self.minimize_btn.setStyleSheet("QPushButton{border-radius:12px; padding:0;}")
        self.minimize_btn.clicked.connect(self.to_tray)
        self.close_btn = QPushButton(); self.close_btn.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
        self.close_btn.setFixedSize(24,24); self.close_btn.setStyleSheet("QPushButton{border-radius:12px; padding:0;}")
        self.close_btn.clicked.connect(self.close)
        title_row.addWidget(self.title); title_row.addStretch(); title_row.addWidget(self.settings_btn)
        title_row.addWidget(self.minimize_btn); title_row.addWidget(self.close_btn)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)  # permite afinar o layout
        self.image_list = QWidget()
        self.image_list_layout = QVBoxLayout(self.image_list); self.image_list_layout.setAlignment(Qt.AlignTop)
        self.image_list_layout.setContentsMargins(0,4,0,4); self.image_list_layout.setSpacing(6)
        self.scroll.setWidget(self.image_list)
        self.hint_label = QLabel("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
        self.hint_label.setWordWrap(True)
        self.hint_label.setAlignment(Qt.AlignCenter); self.hint_label.setStyleSheet("color:#999; font-size:11px;")
        self.hint_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.image_list_layout.addWidget(self.hint_label)

        # Frente: Cliente/Telefone/Conversa (vendedor só nas Configurações)
        self.client_name = QLineEdit(); self.client_name.setPlaceholderText("Nome do Cliente (opcional)")
        self.phone = QLineEdit(); self.phone.setPlaceholderText("Telefone (Opcional)")
        self.conversation_id = QLineEdit(); self.conversation_id.setPlaceholderText("ID Conversa (obrigatório)")
        for w in (self.client_name, self.phone, self.conversation_id):
            w.setMinimumSize(0, 0); w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.form_stack = QStackedLayout()
        row_w = QWidget(); row = QHBoxLayout(row_w); row.setContentsMargins(0,0,0,0); row.setSpacing(6)
        row.addWidget(self.client_name, 1); row.addWidget(self.phone, 1); row.addWidget(self.conversation_id, 1)
        col_w = QWidget(); col = QVBoxLayout(col_w); col.setContentsMargins(0,0,0,0); col.setSpacing(6)
        col.addWidget(self.client_name); col.addWidget(self.phone); col.addWidget(self.conversation_id)
        self.form_stack.addWidget(row_w)   # 0 = largo
        self.form_stack.addWidget(col_w)   # 1 = estreito

        self.queue_lbl = QLabel("Fila: 0/10"); self.queue_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.status_lbl = QLabel("Pronto."); self.status_lbl.setObjectName("statusLabel"); self.status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        self.send_btn = QPushButton("Enviar Orçamento"); self.send_btn.setObjectName("sendButton")
        self.send_btn.clicked.connect(self.send_queue)
        self.send_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.send_btn_icon = self.style().standardIcon(QStyle.SP_ArrowRight)

        btn_row = QHBoxLayout(); btn_row.addStretch(); btn_row.addWidget(self.send_btn)

        lay.addLayout(title_row); lay.addWidget(self.scroll, 1); lay.addLayout(self.form_stack)
        lay.addWidget(self.queue_lbl); lay.addWidget(self.status_lbl); lay.addLayout(btn_row)

        # Grips nos cantos para ajuste manual
        self.grip_tl = QSizeGrip(self); self.grip_tr = QSizeGrip(self); self.grip_bl = QSizeGrip(self); self.grip_br = QSizeGrip(self)

        self.setAcceptDrops(True)
        QShortcut(QKeySequence.Paste, self, activated=self.handle_paste)
        self.nam = QNetworkAccessManager(self)

        self._init_tray()
        self.load_settings()
        self._update_form_mode()
        self._container.installEventFilter(self)

        # Probe silencioso (não mostra nada além do status)
        self._connectivity_probe()

    # ------------------- Tray -------------------
    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        tray_icon = QIcon.fromTheme("application-icon") or self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(tray_icon, self); self.tray.setToolTip("OmniForge — App Orçamento")
        menu = QMenu()
        act_restore = QAction("Restaurar", self); act_restore.triggered.connect(self.restore_from_tray)
        act_quit = QAction("Sair", self); act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_restore); menu.addSeparator(); menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.restore_from_tray() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            try: self.tray.showMessage("App minimizado","O OmniForge está na bandeja.",QSystemTrayIcon.Information,2000)
            except Exception: pass
            self.hide()
        else:
            self.showMinimized()

    def restore_from_tray(self):
        self.show(); self.raise_(); self.activateWindow()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.to_tray)
        super().changeEvent(event)

    # ------------------- Resize pelas bordas -------------------
    def _hit_test(self, pos: QPoint):
        x, y, w, h, m = pos.x(), pos.y(), self.width(), self.height(), self.RESIZE_MARGIN
        left, right, top, bottom = (x <= m), (x >= w-m), (y <= m), (y >= h-m)
        return left, right, top, bottom

    def eventFilter(self, obj, event):
        if obj is self._container and event.type() in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
            if event.type() == QEvent.MouseButtonPress: self.mousePressEvent(event); return True
            if event.type() == QEvent.MouseMove: self.mouseMoveEvent(event); return True
            if event.type() == QEvent.MouseButtonRelease: self.mouseReleaseEvent(event); return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            left,right,top,bottom = self._hit_test(event.position().toPoint())
            if any((left,right,top,bottom)):
                self._resizing = True; self._resize_region=(left,right,top,bottom)
                self._start_geo = self.geometry(); self._start_mouse = event.globalPosition().toPoint()
                event.accept(); return
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._resizing = False; self._resize_region=None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        # --- Redimensionando pelas bordas ---
        if self._resizing and self._resize_region:
            left, right, top, bottom = self._resize_region
            delta = event.globalPosition().toPoint() - self._start_mouse
            g = QRect(self._start_geo)

            if left:
                new_x = g.x() + delta.x()
                new_w = g.width() - delta.x()
                if new_w >= self.minimumWidth():
                    g.setX(new_x); g.setWidth(new_w)

            if right:
                new_w = g.width() + delta.x()
                if new_w >= self.minimumWidth():
                    g.setWidth(new_w)

            if top:
                new_y = g.y() + delta.y()
                new_h = g.height() - delta.y()
                if new_h >= self.minimumHeight():
                    g.setY(new_y); g.setHeight(new_h)

            if bottom:
                new_h = g.height() + delta.y()
                if new_h >= self.minimumHeight():
                    g.setHeight(new_h)

            self.setGeometry(g); self._update_form_mode()
            event.accept(); return

        # --- Arrastar a janela ---
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept(); return

        # --- Cursor adequado ---
        left,right,top,bottom = self._hit_test(pos)
        if (left and top) or (right and bottom): self.setCursor(Qt.SizeFDiagCursor)
        elif (right and top) or (left and bottom): self.setCursor(Qt.SizeBDiagCursor)
        elif left or right: self.setCursor(Qt.SizeHorCursor)
        elif top or bottom: self.setCursor(Qt.SizeVerCursor)
        else: self.unsetCursor()

        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        m = 2
        self.grip_tl.move(m, m)
        self.grip_tr.move(self.width()-self.grip_tr.sizeHint().width()-m, m)
        self.grip_bl.move(m, self.height()-self.grip_bl.sizeHint().height()-m)
        self.grip_br.move(self.width()-self.grip_br.sizeHint().width()-m, self.height()-self.grip_br.sizeHint().height()-m)
        self._update_form_mode()
        super().resizeEvent(event)

    def _update_form_mode(self):
        w = self.width()
        compact = (w < 320)
        self.form_stack.setCurrentIndex(1 if w < 360 else 0)
        self.title.setVisible(w >= 240)
        # Botão compacto (ícone apenas)
        if compact:
            self.send_btn.setText(""); self.send_btn.setIcon(self.send_btn_icon)
        else:
            self.send_btn.setIcon(QIcon()); self.send_btn.setText("Enviar Orçamento")
        # Linhas informativas com elisão visual
        self.queue_lbl.setVisible(w >= 180)
        if w < 200:
            self.status_lbl.setText("Pronto")
        else:
            # mantém último status – não reescreve se já houver mensagem
            if not self.status_lbl.text(): self.status_lbl.setText("Pronto")

    # ------------------- DnD / Clipboard -------------------
    def dragEnterEvent(self, event):
        if (event.mimeData().hasUrls() or event.mimeData().hasImage()) and len(self.image_queue) < 10:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasImage():
            self.enqueue_image(md.imageData())
        elif md.hasUrls():
            for url in md.urls():
                p = Path(url.toLocalFile())
                if p.is_file() and len(self.image_queue) < 10:
                    pm = QPixmap(str(p))
                    if not pm.isNull(): self.enqueue_image(pm, filename=p.name)

    def handle_paste(self):
        if len(self.image_queue) >= 10: return
        img = QGuiApplication.clipboard().image()
        if not img.isNull(): self.enqueue_image(img)

    # ------------------- Fila / Envio -------------------
    def enqueue_image(self, qimg_or_pix, filename: Optional[str] = None):
        if len(self.image_queue) >= 10: self.status("Fila cheia."); return
        if not self.image_queue: self.hint_label.hide()
        if not filename:
            filename = f"img-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"

        if isinstance(qimg_or_pix, QPixmap):
            pm = qimg_or_pix
        else:
            pm = QPixmap.fromImage(qimg_or_pix)

        data = qimage_to_png_bytes(pm)                 # deep copy garantido
        digest = sha256_hex(data)[:8]
        token  = uuid.uuid4().hex[:8]
        safe_name = filename.replace("/", "_").replace("\\", "_")
        self.image_queue.append({"token": token, "filename": safe_name, "data": data, "sha": digest})

        preview = ImagePreviewItem(pm, safe_name, token)
        preview.removed.connect(self.remove_image)
        self.image_list_layout.addWidget(preview)
        self.update_queue_label(); self.status(f"Imagem '{safe_name}' adicionada.")

    @Slot(QWidget)
    def remove_image(self, item_widget):
        tok = item_widget._token
        self.image_queue = [x for x in self.image_queue if x["token"] != tok]
        item_widget.deleteLater()
        self.update_queue_label()
        if not self.image_queue: self.hint_label.show()

    def clear_queue(self):
        self.image_queue.clear()
        for i in reversed(range(self.image_list_layout.count())):
            w = self.image_list_layout.itemAt(i).widget()
            if w and w is not self.hint_label: w.setParent(None); w.deleteLater()
        self.hint_label.show(); self.update_queue_label()

    def update_queue_label(self):
        self.queue_lbl.setText(f"Fila: {len(self.image_queue)}/10")

    def status(self, text: str):
        self.status_lbl.setText(text or "")

    def load_settings(self):
        s = self.settings
        self.WEBHOOK_URL = s.value("webhook_url", "")
        self.SELLER_NAME = s.value("seller_name", "")
        if not self.WEBHOOK_URL or not self.SELLER_NAME:
            self.status("Configure o nome do vendedor e o webhook em ⚙️")
        # R2 oculto
        self.R2_ACCOUNT_ID  = s.value("r2_account_id",  R2_DEFAULTS["account_id"])
        self.R2_BUCKET      = s.value("r2_bucket",      R2_DEFAULTS["bucket"])
        self.R2_ENDPOINT    = (s.value("r2_endpoint",   R2_DEFAULTS["endpoint"]) or "").rstrip("/")
        self.R2_PUBLIC_BASE = (s.value("r2_public",     R2_DEFAULTS["public_base"]) or "").rstrip("/")
        self.R2_PREFIX      = (s.value("r2_prefix",     R2_DEFAULTS["prefix"]) or "").lstrip("/")
        self.R2_CACHE       = s.value("r2_cache",       R2_DEFAULTS["cache_ctrl"])
        self.R2_KEY_ID      = s.value("r2_key_id",      R2_DEFAULTS["key_id"])
        self.R2_KEY_SECRET  = s.value("r2_key_secret",  R2_DEFAULTS["key_secret"])

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.load_settings(); self.status("Configurações salvas.")

    # ---------- Conectividade silenciosa ----------
    def _connectivity_probe(self):
        if not self.R2_PUBLIC_BASE: return
        req = QNetworkRequest(QUrl(self.R2_PUBLIC_BASE + "/"))
        req.setAttribute(QNetworkRequest.Http2AllowedAttribute, False)
        reply = self.nam.head(req)
        def done():
            try:
                if reply.error() == QNetworkReply.NoError:
                    self.status("Conectado.")
                else:
                    self.status("Atenção: verifique sua Internet.")
            finally:
                reply.deleteLater()
        reply.finished.connect(done)

    # ---------- Assinatura AWS V4 (R2) ----------
    def _build_s3_headers(self, method: str, key_path: str, payload: bytes, content_type: Optional[str] = None):
        region, service = "auto", "s3"
        host = QUrl(self.R2_ENDPOINT).host()
        amz_date, date_stamp = iso8601_basic(datetime.datetime.utcnow())
        canonical_uri = f"/{self.R2_BUCKET}/{key_path}"
        payload_hash = sha256_hex(payload)
        canonical_headers = f"host:{host}\n" f"x-amz-content-sha256:{payload_hash}\n" f"x-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join([method, canonical_uri, "", canonical_headers, signed_headers, payload_hash])
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join([algorithm, amz_date, credential_scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
        signing_key = aws_v4_sign(self.R2_KEY_SECRET, date_stamp, region, service)
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authz = f"{algorithm} Credential={self.R2_KEY_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
        headers = {
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authz,
            "Cache-Control": self.R2_CACHE,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _public_url_for(self, key_path: str) -> str:
        # sem bucket no caminho (estilo pub-xxxx.r2.dev)
        return f"{self.R2_PUBLIC_BASE}/{key_path}"

    # ---------- Upload de 1 imagem ----------
    def _put_one_image(self, item, on_done):
        safe_name = item["filename"]
        day = datetime.datetime.utcnow().strftime('%Y/%m/%d/')
        key_path = f"{self.R2_PREFIX}{day}{item['sha']}-{uuid.uuid4().hex[:8]}-{safe_name}"
        key_path = "/".join([p for p in key_path.split("/") if p])  # normaliza

        url = QUrl(f"{self.R2_ENDPOINT}/{self.R2_BUCKET}/{quote(key_path)}")
        req = QNetworkRequest(url)
        req.setAttribute(QNetworkRequest.Http2AllowedAttribute, False)
        cfg = QSslConfiguration.defaultConfiguration(); cfg.setProtocol(QSsl.TlsV1_2OrLater)
        req.setSslConfiguration(cfg)

        for k, v in self._build_s3_headers("PUT", key_path, item["data"], "image/png").items():
            req.setRawHeader(k.encode(), v.encode())

        reply = self.nam.put(req, QByteArray(item["data"]))

        def finished():
            try:
                if reply.error() == QNetworkReply.NoError:
                    on_done(True, key_path, self._public_url_for(key_path), "")
                else:
                    on_done(False, key_path, "", reply.errorString())
            finally:
                reply.deleteLater()
        reply.finished.connect(finished)

    # ---------- Delete de 1 objeto ----------
    def _delete_key(self, key_path: str, on_done):
        url = QUrl(f"{self.R2_ENDPOINT}/{self.R2_BUCKET}/{quote(key_path)}")
        req = QNetworkRequest(url)
        req.setAttribute(QNetworkRequest.Http2AllowedAttribute, False)
        cfg = QSslConfiguration.defaultConfiguration(); cfg.setProtocol(QSsl.TlsV1_2OrLater)
        req.setSslConfiguration(cfg)

        for k, v in self._build_s3_headers("DELETE", key_path, b"", None).items():
            req.setRawHeader(k.encode(), v.encode())

        reply = self.nam.deleteResource(req)
        def finished():
            try:
                if reply.error() == QNetworkReply.NoError:
                    on_done(True, "")
                else:
                    on_done(False, reply.errorString())
            finally:
                reply.deleteLater()
        reply.finished.connect(finished)

    # ---------- Orquestração: upload todos -> webhook -> (se OK) delete ----------
    def _upload_all_and_send(self, client_name: str, phone: str, conversation_id: str):
        items = list(self.image_queue)  # snapshot antes de limpar
        total = len(items)
        self.status(f"Enviando {total} imagem(ns) ao S3…")

        self._idx = 0
        self._keys: List[str] = []
        self._urls: List[str] = []

        def next_upload():
            if self._idx >= total:
                self.status("Upload concluído. Enviando links ao webhook…")
                self._send_links_to_webhook(client_name, phone, conversation_id, self._urls, self._keys)
                return
            self.status(f"Upload {self._idx+1}/{total}…")
            self._put_one_image(items[self._idx], after_upload)

        def after_upload(ok: bool, key_path: str, url: str, err: str):
            if ok:
                self._keys.append(key_path); self._urls.append(url)
                logger.info(f"Upload OK: {url}")
            else:
                logger.error(f"Upload falhou ({key_path}): {err}")
            self._idx += 1; next_upload()

        next_upload()

    def _send_links_to_webhook(self, client_name: str, phone: str, conversation_id: str, urls: List[str], keys: List[str]):
        if not self.WEBHOOK_URL:
            self.status("Webhook não configurado."); return

        payload = {"client_name": client_name or "", "phone": phone or "", "conversation_id": conversation_id, "images": urls}
        req = QNetworkRequest(QUrl(self.WEBHOOK_URL))
        req.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        reply = self.nam.post(req, json.dumps(payload).encode("utf-8"))

        def done():
            try:
                st = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                ok = (reply.error() == QNetworkReply.NoError) and (st is None or st < 400)
                if ok:
                    self.status(f"Orçamento enviado com {len(urls)} link(s). Limpando arquivos temporários…")
                    self._delete_after_webhook(keys)
                else:
                    self.status(f"Falha no webhook: {reply.errorString()} (HTTP {st})")
            finally:
                reply.deleteLater()
        reply.finished.connect(done)

    def _delete_after_webhook(self, keys: List[str]):
        if not keys:
            self.status("Concluído."); return
        self._del_idx = 0; total = len(keys)

        def next_del():
            if self._del_idx >= total:
                self.status("Concluído."); return
            key = keys[self._del_idx]
            self.status(f"Removendo {self._del_idx+1}/{total}…")
            self._delete_key(key, after_del)

        def after_del(ok: bool, err: str):
            if not ok:
                logger.error(f"DELETE falhou: {err}")
            self._del_idx += 1; next_del()

        next_del()

    def send_queue(self):
        if not self.WEBHOOK_URL or not self.SELLER_NAME:
            self.status("Configure o nome do vendedor e o webhook em ⚙️"); return
        if not self.image_queue:
            self.status("Fila de envio vazia."); return
        conversation_id = (self.conversation_id.text() or "").strip()
        if not conversation_id:
            self.status("Preencha o ID da Conversa."); return
        client_name = self.client_name.text().strip()
        phone = self.phone.text().strip()
        self._upload_all_and_send(client_name, phone, conversation_id)
        self.clear_queue()

# ===================== Execução =====================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet('''
        QWidget#card {
            background: rgba(28, 30, 37, 220); /* um pouco mais transparente */
            color: #f0f0f0; border-radius: 12px; border: 1px solid rgba(120,120,120,60);
        }
        QLabel { color: #e0e0e0; }
        QLineEdit {
            padding: 6px; border: 1px solid #444; border-radius: 8px;
            background: rgba(0,0,0,0.28); color: #f0f0f0; font-size: 12px; min-width: 0px;
        }
        QLineEdit:focus { border: 1px solid #7a63ff; background: rgba(0,0,0,0.2); }
        QPushButton {
            padding: 6px 10px; border: 1px solid #555; border-radius: 8px;
            background: transparent; color: #e0e0e0; font-weight: 600; font-size: 12px; min-width: 0px;
        }
        QPushButton:hover { background: rgba(255,255,255,0.08); border-color: #888; }
        QPushButton#sendButton { background-color: #7a63ff; border-color: #7a63ff; color: #ffffff; }
        QPushButton#sendButton:hover { background-color: #8b74ff; }
        QLabel#statusLabel { color: #90ee90; font-weight: 600; }
        QDialog { background-color: #282c34; }
    ''')
    w = FloatingWidget()
    w.show()
    sys.exit(app.exec())
