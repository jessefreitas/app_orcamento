import os, sys, uuid, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot
from PySide6.QtGui import (QColor, QPainter, QBrush, QGuiApplication, QPixmap,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLineEdit)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkRequest,
                               QHttpMultiPart, QHttpPart)

# ====== Configuração do webhook ======
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "https://webhook.skycracker.com.br/webhook/fbf031f4-c238-4a58-b1a7-2c4ca2d09161")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")  # opcional, para header Authorization


def qimage_to_bytes(qimage, fmt="PNG", quality=92) -> bytes:
    """Converte QImage/QPixmap para bytes."""
    if isinstance(qimage, QPixmap):
        qimage = qimage.toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, fmt, quality)
    buf.close()
    return bytes(ba)


class FloatingWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.image_queue = []
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # não aparece na barra de tarefas
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(400, 300)
        self._drag_pos = QPoint()

        # ====== UI ======
        container = QWidget(self)
        container.setObjectName("card")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.title = QLabel("OmniForge — App Flutuante")
        self.title.setStyleSheet("font-weight:600;")

        self.hint = QLabel("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("opacity:0.9;")

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nome")
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Telefone")

        form_layout = QHBoxLayout()
        form_layout.addWidget(self.name_input)
        form_layout.addWidget(self.phone_input)

        self.queue_lbl = QLabel("Fila: 0/10")
        self.status_lbl = QLabel("Pronto.")
        self.status_lbl.setStyleSheet("opacity:0.85;")

        self.send_btn = QPushButton("Enviar Fila")
        self.send_btn.clicked.connect(self.send_queue)
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.close)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.send_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)

        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addLayout(form_layout)
        layout.addStretch()
        layout.addWidget(self.queue_lbl)
        layout.addWidget(self.status_lbl)
        layout.addLayout(btn_layout)

        self.setStyleSheet('''
            QWidget#card {
                background: rgba(20,20,28,210);
                color: #f6f6f6;
                border-radius: 14px;
            }
            QPushButton {
                padding: 6px 10px; border: 1px solid #666; border-radius: 8px;
                background: transparent; color: #f6f6f6;
            }
            QPushButton:hover { background: rgba(255,255,255,0.08); }
            QLineEdit {
                padding: 6px 10px; border: 1px solid #666; border-radius: 8px;
                background: rgba(0,0,0,0.2); color: #f6f6f6;
            }
        ''')

        # ====== Drag & Drop / Clipboard ======
        self.setAcceptDrops(True)
        QShortcut(QKeySequence.Paste, self, activated=self.handle_paste)

        # ====== HTTP Client ======
        self.nam = QNetworkAccessManager(self)

    # ---------- Aparência ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for i, alpha in enumerate((40, 25, 15, 8)):
            painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 16, 16)

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

        # 1) Imagem embutida
        if md.hasImage():
            img = md.imageData()
            self.enqueue_image(img)
            handled = True

        # 2) Arquivos (URLs)
        if md.hasUrls():
            for url in md.urls():
                local = url.toLocalFile()
                if not local:
                    continue
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

        # 1) Se tiver imagem direta
        img = cb.image()
        if not img.isNull():
            self.enqueue_image(img)
            self.hint.setText("Cole uma imagem (Ctrl+V) ou arraste & solte arquivos aqui.")
            return

        # 2) Se for arquivo copiado (paths em URLs)
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
        mime = "image/png" if fmt == "PNG" else "image/jpeg"

        data = qimage_to_bytes(qimg_or_pix, fmt=fmt, quality=92)
        self.image_queue.append({"filename": filename, "data": data, "mime": mime})
        self.update_queue_label()
        self.status(f"Imagem '{filename}' adicionada à fila.")

    def send_queue(self):
        if not self.image_queue:
            self.status("Fila de envio vazia.")
            return

        name = self.name_input.text()
        phone = self.phone_input.text()

        if not name or not phone:
            self.status("Por favor, preencha o nome e o telefone.")
            return

        for item in self.image_queue:
            self.upload_bytes(item["filename"], item["data"], item["mime"], name, phone)

        self.image_queue.clear()
        self.update_queue_label()

    def upload_bytes(self, filename: str, data: bytes, mime: str, name: str, phone: str):
        if not WEBHOOK_URL:
            self.status("WEBHOOK_URL não configurada.")
            return

        url = QUrl(WEBHOOK_URL)
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.UserAgentHeader, "OmniForge-FloatApp/1.0")
        if WEBHOOK_TOKEN:
            req.setRawHeader(b"Authorization", f"Bearer {WEBHOOK_TOKEN}".encode())

        mp = QHttpMultiPart(QHttpMultiPart.FormDataType)

        # Dados do formulário (nome, telefone)
        name_part = QHttpPart()
        name_part.setHeader(QNetworkRequest.ContentDispositionHeader, 'form-data; name="name"')
        name_part.setBody(name.encode("utf-8"))

        phone_part = QHttpPart()
        phone_part.setHeader(QNetworkRequest.ContentDispositionHeader, 'form-data; name="phone"')
        phone_part.setBody(phone.encode("utf-8"))

        # Arquivo
        file_part = QHttpPart()
        disp = f'form-data; name="file"; filename="{filename}"'
        file_part.setHeader(QNetworkRequest.ContentDispositionHeader, disp)
        file_part.setHeader(QNetworkRequest.ContentTypeHeader, mime)
        buf = QBuffer()
        buf.setData(QByteArray(data))
        buf.open(QIODevice.ReadOnly)
        file_part.setBodyDevice(buf)
        buf.setParent(mp)

        mp.append(name_part)
        mp.append(phone_part)
        mp.append(file_part)

        reply = self.nam.post(req, mp)
        mp.setParent(reply)

        self.status(f"Enviando: {filename} …")
        reply.finished.connect(lambda: self._on_finished(reply, filename))

    @Slot()
    def _on_finished(self, reply, filename: str):
        err = reply.error()
        try:
            payload = bytes(reply.readAll()).decode("utf-8", "ignore")
        except Exception:
            payload = "<sem corpo>"
        if err:
            self.status(f"Falha no envio de {filename}: {reply.errorString()}")
        else:
            self.status(f"Envio de {filename} concluído.")
        reply.deleteLater()

    def update_queue_label(self):
        self.queue_lbl.setText(f"Fila: {len(self.image_queue)}/10")

    def status(self, text: str):
        self.status_lbl.setText(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = FloatingWidget()
    w.show()
    sys.exit(app.exec())
