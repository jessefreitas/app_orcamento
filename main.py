import os, sys, uuid, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot
from PySide6.QtGui import (QColor, QPainter, QBrush, QGuiApplication, QPixmap,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLineEdit, QSizePolicy)
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


class FloatingWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.image_queue = []
        self.active_replies = []
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # não aparece na barra de tarefas
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(500, 400)
        self._drag_pos = QPoint()

        # ====== UI ======
        container = QWidget(self)
        container.setObjectName("card")
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.title = QLabel("OmniForge — App Flutuante")
        self.title.setStyleSheet("font-weight:600;")
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.hint = QLabel("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("opacity:0.9;")
        self.hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nome")
        self.name_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Telefone")
        self.phone_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        form_widget = QWidget()
        form_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        form_layout = QHBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        form_layout.addWidget(self.name_input, 1)
        form_layout.addWidget(self.phone_input, 1)

        self.queue_lbl = QLabel("Fila: 0/10")
        self.queue_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.status_lbl = QLabel("Pronto.")
        self.status_lbl.setObjectName("statusLabel") # Add object name for styling
        self.status_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed) # Fix status label height

        self.send_btn = QPushButton("Enviar Fila")
        self.send_btn.setObjectName("sendButton") # Add object name for styling
        self.send_btn.clicked.connect(self.send_queue)
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.close)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.send_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(btn_close)

        layout.addWidget(self.title)
        layout.addWidget(self.hint, 1)
        layout.addWidget(form_widget, 1)
        layout.addWidget(self.queue_lbl)
        layout.addWidget(self.status_lbl)
        layout.addLayout(btn_layout)
        layout.addStretch()

        self.setStyleSheet('''
            QWidget#card {
                background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1, stop:0 rgba(40, 44, 52, 230), stop:1 rgba(20, 22, 26, 240));
                color: #f0f0f0;
                border-radius: 14px;
                border: 1px solid rgba(120, 120, 120, 60);
            }
            QLabel {
                color: #e0e0e0;
            }
            QLineEdit {
                padding: 10px;
                border: 1px solid #444;
                border-radius: 8px;
                background: rgba(0,0,0,0.3);
                color: #f0f0f0;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #7a63ff;
                background: rgba(0,0,0,0.2);
            }
            QPushButton {
                padding: 8px 14px;
                border: 1px solid #555;
                border-radius: 8px;
                background: transparent;
                color: #e0e0e0;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
                border-color: #888;
            }
            QPushButton#sendButton {
                background-color: #7a63ff;
                border-color: #7a63ff;
                color: #ffffff;
            }
            QPushButton#sendButton:hover {
                background-color: #8b74ff;
            }
            QLabel#statusLabel {
                color: #90ee90; /* Light green for success/response */
                font-weight: 600;
            }
        ''')

        # ====== Drag & Drop / Clipboard ======
        self.setAcceptDrops(True)
        QShortcut(QKeySequence.Paste, self, activated=self.handle_paste)

        # ====== HTTP Client ======
        self.nam = QNetworkAccessManager(self)

    # ---------- Aparência (Sombra) ----------
    def paintEvent(self, event):
        # A sombra agora é controlada pelo QWidget#card border e o fundo gradiente.
        # O paintEvent customizado para sombra pode ser removido para simplificar.
        super().paintEvent(event)

    # ---------- Arrastar janela ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    # ---------- Clicar-através (toggle) ----------
    def mouseDoubleClickEvent(self, event):
        flags = self.windowFlags()
        if flags & Qt.WindowTransparentForInput:
            self.setWindowFlags(flags & ~Qt.WindowTransparentForInput)
            self.status("Recebendo cliques novamente.")
        else:
            self.setWindowFlags(flags | Qt.WindowTransparentForInput)
            self.status("Clicar-através ativado (duplo clique para voltar).")
        self.show()

    # ---------- Drag & Drop de arquivos ----------
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if (md.hasUrls() or md.hasImage()) and len(self.image_queue) < 10:
            event.acceptProposedAction()
            self.hint.setText("Solte para adicionar à fila…")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.hint.setText("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")

    def dropEvent(self, event):
        md = event.mimeData()
        handled = False

        if len(self.image_queue) >= 10:
            self.status("Fila cheia. Envie as imagens antes de adicionar novas.")
            event.ignore()
            return

        if md.hasImage():
            img = md.imageData()
            self.enqueue_image(img)
            handled = True

        if md.hasUrls():
            for url in md.urls():
                local = url.toLocalFile()
                if not local: continue
                p = Path(local)
                if p.is_file():
                    pix = QPixmap(str(p))
                    if not pix.isNull():
                        self.enqueue_image(pix, filename=p.name)
                        handled = True

        if handled:
            event.acceptProposedAction()
            self.hint.setText("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
        else:
            event.ignore()
            self.hint.setText("Formato não reconhecido. Use imagem ou arquivo de imagem.")

    # ---------- Colar (Ctrl+V) ----------
    def handle_paste(self):
        if len(self.image_queue) >= 10:
            self.status("Fila cheia. Envie as imagens antes de adicionar novas.")
            return

        cb = QGuiApplication.clipboard()
        md = cb.mimeData()

        img = cb.image()
        if not img.isNull():
            self.enqueue_image(img)
            self.hint.setText("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
            return

        if md.hasUrls():
            for url in md.urls():
                p = Path(url.toLocalFile())
                if p.is_file():
                    pix = QPixmap(str(p))
                    if not pix.isNull():
                        self.enqueue_image(pix, filename=p.name)
            self.hint.setText("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
            return

        self.status("Nada colável detectado (imagem/arquivo).")

    # ---------- Upload ----------
    def enqueue_image(self, qimg_or_pix, filename: str | None = None):
        if len(self.image_queue) >= 10:
            self.status("Fila cheia. Envie as imagens antes de adicionar novas.")
            return

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"img-{ts}-{uuid.uuid4().hex[:6]}.png"

        ext = Path(filename).suffix.lower()
        fmt = "PNG" if ext not in (".jpg", ".jpeg") else "JPG"
        
        base64_string = qimage_to_base64_string(qimg_or_pix, fmt=fmt, quality=92)
        self.image_queue.append({"filename": filename, "base64_data": base64_string})
        self.update_queue_label()
        self.status(f"Imagem '{filename}' adicionada à fila.")

    def send_queue(self):
        print("[DEBUG] Iniciando send_queue...")
        if not self.image_queue:
            self.status("Fila de envio vazia.")
            print("[DEBUG] Fila vazia. Abortando.")
            return

        name = self.name_input.text()
        phone = self.phone_input.text()

        if not name or not phone:
            self.status("Por favor, preencha o nome e o telefone.")
            print(f"[DEBUG] Nome ou telefone não preenchido. Nome: '{name}', Telefone: '{phone}'. Abortando.")
            return

        if not WEBHOOK_URL:
            self.status("WEBHOOK_URL não configurada.")
            print("[DEBUG] WEBHOOK_URL não configurada. Abortando.")
            return

        print("[DEBUG] Preparando payload...")
        images_payload = [[item["base64_data"]] for item in self.image_queue]
        json_payload = json.dumps(images_payload)

        params = {'name': name, 'phone': phone}
        encoded_params = urlencode(params)
        full_url = f"{WEBHOOK_URL}?{encoded_params}"

        print(f"[DEBUG] URL de destino: {full_url}")
        # print(f"[DEBUG] Payload JSON: {json_payload[:200]}...") # Descomente para ver o payload

        url = QUrl(full_url)
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.UserAgentHeader, "OmniForge-FloatApp/1.0")
        if WEBHOOK_TOKEN:
            req.setRawHeader(b"Authorization", f"Bearer {WEBHOOK_TOKEN}".encode())
        
        req.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')

        print("[DEBUG] Enviando requisição POST...")
        reply = self.nam.post(req, json_payload.encode("utf-8"))
        self.active_replies.append(reply) # Manter referência da requisição

        self.status(f"Enviando {len(self.image_queue)} imagens…")
        reply.finished.connect(lambda: self._on_finished(reply))

        self.image_queue.clear()
        self.update_queue_label()

    @Slot()
    def _on_finished(self, reply):
        err_code = reply.error()
        err_string = reply.errorString()
        print(f"[DEBUG] Requisição finalizada. Código de erro: {err_code} ({err_string})")
        
        try:
            payload = bytes(reply.readAll()).decode("utf-8", "ignore").strip()
            print(f"[DEBUG] Resposta recebida (payload): {payload}")
        except Exception as e:
            payload = "<erro ao ler payload>"
            print(f"[DEBUG] Erro ao decodificar resposta: {e}")

        if err_code != QNetworkReply.NoError:
            self.status(f"Falha no envio: {err_string}")
        else:
            if payload:
                self.status(f"Resposta: {payload}")
            else:
                self.status("Envio concluído, sem resposta do servidor.")
        
        reply.deleteLater()
        if reply in self.active_replies:
            self.active_replies.remove(reply) # Remover referência da requisição

    def update_queue_label(self):
        self.queue_lbl.setText(f"Fila: {len(self.image_queue)}/10")

    def status(self, text: str):
        self.status_lbl.setText(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = FloatingWidget()
    w.show()
    sys.exit(app.exec())
