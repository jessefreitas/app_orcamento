import os, sys, uuid, datetime
from pathlib import Path
import logging
import logging.handlers
import json
import base64
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot, Signal, QSettings
from PySide6.QtGui import (QColor, QPainter, QBrush, QGuiApplication, QPixmap,
                           QShortcut, QKeySequence, QIcon)
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLineEdit, QSizePolicy, QScrollArea, QDialog,
                               QDialogButtonBox, QTabWidget)
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkRequest, QNetworkReply, QSslSocket)

# Setup a rotating file logger for analysis
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'app_orcamento.log'
log_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger('app_orcamento')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# Helper function to read and parse logs for analysis
def analyze_logs():
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        # Simple analysis: count errors and warnings
        error_count = sum(1 for line in lines if 'ERROR' in line)
        warning_count = sum(1 for line in lines if 'WARNING' in line)
        info_count = sum(1 for line in lines if 'INFO' in line)
        return {
            'total_lines': len(lines),
            'errors': error_count,
            'warnings': warning_count,
            'info': info_count,
            'last_10_lines': lines[-10:]
        }
    except Exception as e:
        return {'error': str(e)}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
def qimage_to_base64_string(qimage, fmt="PNG", quality=92) -> str:
    if isinstance(qimage, QPixmap):
        qimage = qimage.toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, fmt, quality)
    buf.close()
    return base64.b64encode(bytes(ba)).decode("utf-8")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(450)
        self.settings = QSettings("OmniForge", "AppOrcamento")
        self.nam = QNetworkAccessManager(self)

        main_layout = QVBoxLayout(self)
        tab_widget = QTabWidget()
        main_layout.addWidget(tab_widget)

        # General Tab
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        self.seller_name_input = QLineEdit()
        self.seller_name_input.setText(self.settings.value("seller_name", ""))
        general_layout.addWidget(QLabel("Nome do Vendedor:"))
        general_layout.addWidget(self.seller_name_input)
        general_layout.addStretch()
        tab_widget.addTab(general_tab, "Geral")

        # Webhook Tab
        webhook_tab = QWidget()
        webhook_layout = QVBoxLayout(webhook_tab)
        self.webhook_url_input = QLineEdit()
        self.webhook_url_input.setText(self.settings.value("webhook_url", ""))
        test_btn = QPushButton("Testar Conexão")
        test_btn.clicked.connect(self.test_webhook)
        self.webhook_status_label = QLabel("Status: N/A")
        
        webhook_layout.addWidget(QLabel("URL do Webhook:"))
        webhook_layout.addWidget(self.webhook_url_input)
        
        hbox = QHBoxLayout()
        hbox.addWidget(test_btn)
        hbox.addStretch()
        webhook_layout.addLayout(hbox)
        webhook_layout.addWidget(self.webhook_status_label)
        webhook_layout.addStretch()
        tab_widget.addTab(webhook_tab, "Webhook")

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def test_webhook(self):
        # Override the webhook URL with the provided test URL
        test_url = "https://webhook.skycracker.com.br/webhook/fbf031f4-c238-4a58-b1a7-2c4ca2d09161"
        self.webhook_url_input.setText(test_url)
        url = test_url.strip()
        if not url:
            self.webhook_status_label.setText("Status: URL não pode estar vazia.")
            self.webhook_status_label.setStyleSheet("color: #ffc107;")
            return

        # Directly send a POST request with empty JSON body to test webhook connection
        req = QNetworkRequest(QUrl(url))
        self.webhook_status_label.setText("Status: Testando conexão com webhook...")
        self.webhook_status_label.setStyleSheet("color: #17a2b8;")

        max_retries = 3
        retry_count = 0

        def send_request():
            nonlocal retry_count
            if retry_count >= max_retries:
                self.webhook_status_label.setText("Status: Falha! Número máximo de tentativas atingido.")
                self.webhook_status_label.setStyleSheet("color: #dc3545;")
                logger.error("Webhook Test Error: Número máximo de tentativas atingido.")
                return
            retry_count += 1
            logger.info(f"Tentativa {retry_count} de teste do webhook para URL: {url}")
            reply = self.nam.post(req, QByteArray(b'{}'))
            reply.finished.connect(lambda: on_finished_with_retry(reply))

        def on_finished_with_retry(reply):
            err_code = reply.error()
            err_string = reply.errorString()
            try:
                payload = bytes(reply.readAll()).decode("utf-8", "ignore").strip()
            except Exception:
                payload = ""

            status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)

            if err_code == QNetworkReply.NoError and status_code < 400:
                self.webhook_status_label.setText(f"Status: Sucesso! (Código: {status_code})")
                self.webhook_status_label.setStyleSheet("color: #28a745;")
                logger.info(f"Webhook Test Success: Status Code: {status_code}, Payload: {payload}")
                reply.deleteLater()
            else:
                logger.warning(f"Webhook Test Falhou na tentativa {retry_count}: Erro: {err_string}, Código: {status_code}")
                reply.deleteLater()
                QTimer.singleShot(2000, send_request)

        send_request()

    def accept(self):
        seller_name = self.seller_name_input.text().strip()
        webhook_url = self.webhook_url_input.text().strip()
        self.settings.setValue("seller_name", seller_name)
        self.settings.setValue("webhook_url", webhook_url)
        self.settings.sync()
        logger.info(f"Saving Seller Name: {seller_name}")
        logger.info(f"Saving Webhook URL: {webhook_url}")
        super().accept()


class ImagePreviewItem(QWidget):
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
        self.settings = QSettings("OmniForge", "AppOrcamento")

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(500, 500)
        self._drag_pos = QPoint()

        # ====== UI ======
        container = QWidget(self)
        container.setObjectName("card")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title_layout = QHBoxLayout()
        self.title = QLabel("OmniForge — App Orçamento")
        self.title.setStyleSheet("font-weight:600;")
        self.settings_btn = QPushButton("⚙️") # Botão de Configurações
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setStyleSheet("QPushButton { font-size: 18px; border-radius: 14px; }")
        self.settings_btn.clicked.connect(self.open_settings)
        title_layout.addWidget(self.title)
        title_layout.addStretch()
        title_layout.addWidget(self.settings_btn)

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

        self.seller_name_label = QLineEdit()
        self.seller_name_label.setPlaceholderText("Vendedor (Configure em ⚙️)")
        self.seller_name_label.setReadOnly(True)
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Telefone (Opcional)")
        self.conversation_id_input = QLineEdit()
        self.conversation_id_input.setPlaceholderText("ID Conversa")

        form_layout = QHBoxLayout()
        form_layout.addWidget(self.seller_name_label, 1)
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

        layout.addLayout(title_layout)
        layout.addWidget(self.scroll_area, 1)
        layout.addLayout(form_layout)
        layout.addWidget(self.queue_lbl)
        layout.addWidget(self.status_lbl)
        layout.addLayout(btn_layout)

        self.setAcceptDrops(True)
        QShortcut(QKeySequence.Paste, self, activated=self.handle_paste)
        self.nam = QNetworkAccessManager(self)

        # Load settings and check for first run
        self.load_settings()
        if self.settings.value("first_run", "true") == "true":
            self.run_setup_wizard()

    def run_setup_wizard(self):
        wizard = SetupWizard(self)
        if wizard.exec():
            self.settings.setValue("first_run", "false")
            self.load_settings()
            self.status("Configuração inicial concluída.")
        else:
            # If the user cancels the wizard, close the application
            self.close()

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec():
            self.load_settings()
            self.status("Configurações salvas.")

    def load_settings(self):
        self.WEBHOOK_URL = self.settings.value("webhook_url", "")
        self.SELLER_NAME = self.settings.value("seller_name", "")
        self.seller_name_label.setText(self.SELLER_NAME)
        if not self.WEBHOOK_URL or not self.SELLER_NAME:
            self.status("Por favor, configure o nome e o webhook em ⚙️")

    def paintEvent(self, event):
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft(); event.accept()
    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton: self.move(event.globalPosition().toPoint() - self._drag_pos); event.accept()
    def mouseDoubleClickEvent(self, event):
        flags = self.windowFlags(); self.setWindowFlags(flags ^ Qt.WindowTransparentForInput); self.show()

    def dragEnterEvent(self, event):
        if (event.mimeData().hasUrls() or event.mimeData().hasImage()) and len(self.image_queue) < 10: event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasImage(): self.enqueue_image(md.imageData())
        elif md.hasUrls():
            for url in md.urls():
                p = Path(url.toLocalFile())
                if p.is_file():
                    pix = QPixmap(str(p))
                    if not pix.isNull(): self.enqueue_image(pix, filename=p.name)

    def handle_paste(self):
        if len(self.image_queue) >= 10: return
        img = QGuiApplication.clipboard().image()
        if not img.isNull(): self.enqueue_image(img)

    def enqueue_image(self, qimg_or_pix, filename: str | None = None):
        if len(self.image_queue) >= 10: self.status("Fila cheia."); return
        if not self.image_queue: self.hint_label.hide()
        if not filename: filename = f"img-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
        
        pixmap = QPixmap(qimg_or_pix)
        item_data = {"filename": filename, "base64_data": qimage_to_base64_string(pixmap)}
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
        if not self.image_queue: self.hint_label.show()

    def clear_queue(self):
        self.image_queue.clear()
        while self.image_list_layout.count():
            item = self.image_list_layout.takeAt(0)
            widget = item.widget()
            if widget and widget != self.hint_label: widget.deleteLater()
        self.hint_label.show()
        self.update_queue_label()

    def send_queue(self):
        if not self.WEBHOOK_URL or not self.SELLER_NAME:
            self.status("Configure o nome e o webhook em ⚙️ primeiro!"); return
        if not self.image_queue: self.status("Fila de envio vazia."); return
        
        phone = self.phone_input.text()
        conversation_id = self.conversation_id_input.text()

        if not conversation_id: 
            self.status("Preencha o ID da Conversa."); 
            logger.warning("Attempted to send queue without conversation ID")
            return

        images_payload = [[item["base64_data"]] for item in self.image_queue]
        params = {'name': self.SELLER_NAME, 'phone': phone, 'conversation_id': conversation_id}
        
        req = QNetworkRequest(QUrl(f"{self.WEBHOOK_URL}?{urlencode(params)}"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        
        reply = self.nam.post(req, json.dumps(images_payload).encode("utf-8"))
        self.active_replies.append(reply)
        
        self.status(f"Enviando {len(self.image_queue)} imagens…")
        logger.info(f"Sending {len(self.image_queue)} images to webhook URL: {self.WEBHOOK_URL} with params: {params}")
        reply.finished.connect(lambda: self._on_finished(reply))
        self.clear_queue()

    @Slot()
    def _on_finished(self, reply):
        err_code = reply.error()
        err_string = reply.errorString()
        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        try: payload = bytes(reply.readAll()).decode("utf-8", "ignore").strip()
        except Exception: payload = ""

        if err_code != QNetworkReply.NoError: 
            self.status(f"Falha no envio: {err_string}")
            logger.error(f"Webhook Send Error: {err_code}, Status Code: {status_code}, Error String: {err_string}, Payload: {payload}")
        else: 
            self.status(f"Resposta: {payload}" if payload else "Envio concluído!")
            logger.info(f"Webhook Send Success: Status Code: {status_code}, Payload: {payload}")
        
        reply.deleteLater()
        if reply in self.active_replies: self.active_replies.remove(reply)

    def update_queue_label(self):
        self.queue_lbl.setText(f"Fila: {len(self.image_queue)}/10")

    def status(self, text: str):
        self.status_lbl.setText(text)

class SetupWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistente de Configuração Inicial")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.settings = QSettings("OmniForge", "AppOrcamento")
        self.nam = QNetworkAccessManager(self)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Bem-vindo! Por favor, configure o webhook para continuar."))
        
        self.webhook_url_input = QLineEdit()
        self.webhook_url_input.setPlaceholderText("https://seu-webhook.com/endpoint")
        layout.addWidget(QLabel("URL do Webhook:"))
        layout.addWidget(self.webhook_url_input)

        test_btn = QPushButton("Testar e Salvar")
        test_btn.clicked.connect(self.test_and_save)
        self.status_label = QLabel("Status: Aguardando configuração.")
        
        layout.addWidget(test_btn)
        layout.addWidget(self.status_label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def test_and_save(self):
        url = self.webhook_url_input.text().strip()
        if not url:
            self.status_label.setText("Status: URL não pode estar vazia.")
            self.status_label.setStyleSheet("color: #ffc107;")
            return

        req = QNetworkRequest(QUrl(url))
        self.status_label.setText("Status: Testando...")
        self.status_label.setStyleSheet("color: #17a2b8;")

        # Use POST request with empty JSON body for webhook test
        reply = self.nam.post(req, QByteArray(b'{}'))
        reply.finished.connect(lambda: self.on_test_finished(reply))

    def on_test_finished(self, reply):
        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() == QNetworkReply.NoError and status_code < 400:
            self.status_label.setText(f"Status: Sucesso! (Código: {status_code})")
            self.status_label.setStyleSheet("color: #28a745;")
            self.settings.setValue("webhook_url", self.webhook_url_input.text().strip())
            # Ask for seller name
            self.prompt_for_seller_name()
        else:
            error_string = reply.errorString()
            self.status_label.setText(f"Status: Falha! (Erro: {error_string})")
            self.status_label.setStyleSheet("color: #dc3545;")
        reply.deleteLater()

    def prompt_for_seller_name(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Nome do Vendedor")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Por favor, insira o nome do vendedor:"))
        seller_name_input = QLineEdit()
        layout.addWidget(seller_name_input)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)
        
        if dialog.exec():
            self.settings.setValue("seller_name", seller_name_input.text().strip())
            self.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet('''
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
        QDialog { background-color: #282c34; }
    ''')
    w = FloatingWidget()
    w.show()
    sys.exit(app.exec())
