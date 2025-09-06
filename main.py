import os, sys, uuid, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot, Signal
from PySide6.QtGui import (QColor, QPainter, QBrush, QGuiApplication, QPixmap,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLineEdit, QSizePolicy, QScrollArea)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkRequest, QNetworkReply)

# ====== Configuração do webhook ======
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "https://webhook.skycracker.com.br/webhook/fbf031f4-c238-4a58-b1a7-2c4ca2d09161")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")  # opcional, para header Authorization


import base64
from urllib.parse import urlencode
import json

def qimage_to_base64_string(qimage, fmt="PNG", quality=92) -> str:
    """Converte QImage/QPixmap para string Base64."""
    if isinstance(qimage, QPixmap):
        qimage = qimage.toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, fmt, quality)
    buf.close()
    return base64.b64encode(bytes(ba)).decode("utf-8")


class ImagePreviewItem(QWidget):
    """Widget para um item na lista de preview de imagem."""
    removed = Signal(QWidget)

    def __init__(self, pixmap: QPixmap, filename: str, item_data: dict):
        super().__init__()
        self.item_data = item_data

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        thumbnail_label = QLabel()
        thumbnail_label.setPixmap(pixmap.scaled(60, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(thumbnail_label)

        filename_label = QLabel(filename)
        filename_label.setWordWrap(True)
        layout.addWidget(filename_label, 1)

        remove_btn = QPushButton("X")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setStyleSheet("QPushButton { border-radius: 12px; background-color: rgba(255,255,255,0.1); } QPushButton:hover { background-color: rgba(255,100,100,0.8); }")
        remove_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(remove_btn)


class FloatingWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.image_queue = []
        self.active_replies = []
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(500, 500) # Aumentar altura para a lista
        self._drag_pos = QPoint()

        # ====== UI ======
        container = QWidget(self)
        container.setObjectName("card")
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(14, 14, 14, 14)
        self.main_layout.setSpacing(10)

        self.title = QLabel("OmniForge — App Flutuante")
        self.title.setStyleSheet("font-weight:600;")
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # Área de scroll para a lista de imagens
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self.image_list_widget = QWidget()
        self.image_list_layout = QVBoxLayout(self.image_list_widget)
        self.image_list_layout.setAlignment(Qt.AlignTop)
        self.image_list_layout.setContentsMargins(0, 5, 0, 5)
        self.image_list_layout.setSpacing(8)
        
        self.scroll_area.setWidget(self.image_list_widget)

        self.hint_label = QLabel("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setStyleSheet("color: #888;")
        self.image_list_layout.addWidget(self.hint_label)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nome")
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Telefone (Opcional)")
        self.conversation_id_input = QLineEdit()
        self.conversation_id_input.setPlaceholderText("ID Conversa")

        form_layout = QHBoxLayout()
        form_layout.addWidget(self.name_input, 1)
        form_layout.addWidget(self.phone_input, 1)
        form_layout.addWidget(self.conversation_id_input, 1)

        self.queue_lbl = QLabel("Fila: 0/10")
        self.status_lbl = QLabel("Pronto.")
        self.status_lbl.setObjectName("statusLabel")

        self.send_btn = QPushButton("Enviar Orçamento")
        self.send_btn.setObjectName("sendButton")
        self.send_btn.clicked.connect(self.send_queue)
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.close)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.send_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(btn_close)

        self.main_layout.addWidget(self.title)
        self.main_layout.addWidget(self.scroll_area, 1)
        self.main_layout.addLayout(form_layout)
        self.main_layout.addWidget(self.queue_lbl)
        self.main_layout.addWidget(self.status_lbl)
        self.main_layout.addLayout(btn_layout)

        self.setStyleSheet('''
            QWidget#card {
                background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1, stop:0 rgba(40, 44, 52, 230), stop:1 rgba(20, 22, 26, 240));
                color: #f0f0f0; border-radius: 14px; border: 1px solid rgba(120, 120, 120, 60);
            }
            QLabel { color: #e0e0e0; }
            QLineEdit {
                padding: 10px; border: 1px solid #444; border-radius: 8px;
                background: rgba(0,0,0,0.3); color: #f0f0f0; font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #7a63ff; background: rgba(0,0,0,0.2); }
            QPushButton {
                padding: 8px 14px; border: 1px solid #555; border-radius: 8px;
                background: transparent; color: #e0e0e0; font-weight: 600;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.08); border-color: #888; }
            QPushButton#sendButton { background-color: #7a63ff; border-color: #7a63ff; color: #ffffff; }
            QPushButton#sendButton:hover { background-color: #8b74ff; }
            QLabel#statusLabel { color: #90ee90; font-weight: 600; }
        ''')

        self.setAcceptDrops(True)
        QShortcut(QKeySequence.Paste, self, activated=self.handle_paste)
        self.nam = QNetworkAccessManager(self)

    def paintEvent(self, event):
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        flags = self.windowFlags()
        if flags & Qt.WindowTransparentForInput:
            self.setWindowFlags(flags & ~Qt.WindowTransparentForInput)
            self.status("Recebendo cliques novamente.")
        else:
            self.setWindowFlags(flags | Qt.WindowTransparentForInput)
            self.status("Clicar-através ativado (duplo clique para voltar).")
        self.show()

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
                if p.is_file():
                    pix = QPixmap(str(p))
                    if not pix.isNull():
                        self.enqueue_image(pix, filename=p.name)

    def handle_paste(self):
        if len(self.image_queue) >= 10: return
        cb = QGuiApplication.clipboard()
        img = cb.image()
        if not img.isNull():
            self.enqueue_image(img)

    def enqueue_image(self, qimg_or_pix, filename: str | None = None):
        if len(self.image_queue) >= 10:
            self.status("Fila cheia. Envie as imagens antes de adicionar novas.")
            return
        
        if not self.image_queue:
            self.hint_label.hide()

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"img-{ts}-{uuid.uuid4().hex[:6]}.png"

        pixmap = QPixmap(qimg_or_pix)
        base64_string = qimage_to_base64_string(pixmap)
        
        item_data = {"filename": filename, "base64_data": base64_string}
        self.image_queue.append(item_data)

        preview_item = ImagePreviewItem(pixmap, filename, item_data)
        preview_item.removed.connect(self.remove_image)
        self.image_list_layout.addWidget(preview_item)
        
        self.update_queue_label()
        self.status(f"Imagem '{filename}' adicionada.")

    @Slot(QWidget)
    def remove_image(self, item_widget):
        self.image_queue.remove(item_widget.item_data)
        item_widget.deleteLater()
        self.update_queue_label()
        if not self.image_queue:
            self.hint_label.show()

    def clear_queue(self):
        self.image_queue.clear()
        while self.image_list_layout.count():
            item = self.image_list_layout.takeAt(0)
            widget = item.widget()
            if widget and widget != self.hint_label:
                widget.deleteLater()
        self.hint_label.show()
        self.update_queue_label()

    def send_queue(self):
        if not self.image_queue:
            self.status("Fila de envio vazia."); return

        name = self.name_input.text()
        phone = self.phone_input.text()
        conversation_id = self.conversation_id_input.text()

        if not name or not conversation_id:
            self.status("Por favor, preencha Nome e ID Conversa."); return

        images_payload = [[item["base64_data"]] for item in self.image_queue]
        params = {'name': name, 'phone': phone, 'conversation_id': conversation_id}
        
        req = QNetworkRequest(QUrl(f"{WEBHOOK_URL}?{urlencode(params)}"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        if WEBHOOK_TOKEN:
            req.setRawHeader(b"Authorization", f"Bearer {WEBHOOK_TOKEN}".encode())
        
        reply = self.nam.post(req, json.dumps(images_payload).encode("utf-8"))
        self.active_replies.append(reply)
        
        self.status(f"Enviando {len(self.image_queue)} imagens…")
        reply.finished.connect(lambda: self._on_finished(reply))
        self.clear_queue()

    @Slot()
    def _on_finished(self, reply):
        err_code = reply.error()
        err_string = reply.errorString()
        
        try:
            payload = bytes(reply.readAll()).decode("utf-8", "ignore").strip()
        except Exception:
            payload = ""

        if err_code != QNetworkReply.NoError:
            self.status(f"Falha no envio: {err_string}")
        else:
            self.status(f"Resposta: {payload}" if payload else "Envio concluído!")
        
        reply.deleteLater()
        if reply in self.active_replies:
            self.active_replies.remove(reply)

    def update_queue_label(self):
        self.queue_lbl.setText(f"Fila: {len(self.image_queue)}/10")

    def status(self, text: str):
        self.status_lbl.setText(text)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = FloatingWidget()
    w.show()
    sys.exit(app.exec())
