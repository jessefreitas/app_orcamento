import os, sys, uuid, datetime
from pathlib import Path
import logging
import logging.handlers
import json
import base64
from urllib.parse import urlencode
from typing import Optional  # Compatível com Python < 3.10

from PySide6.QtCore import (
    Qt, QPoint, QByteArray, QBuffer, QIODevice, QUrl, Slot, Signal, QSettings, QTimer, QEvent, QSize
)
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QGuiApplication, QPixmap,
    QShortcut, QKeySequence, QIcon, QImage, QAction
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QSizePolicy, QScrollArea, QDialog, QDialogButtonBox, QTabWidget, QStyle,
    QSystemTrayIcon, QMenu
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply, QSslSocket

# --------------------- Logging com Rotating File Handler ---------------------
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'app_orcamento.log'
log_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('app_orcamento')
logger.setLevel(logging.INFO)
if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
    logger.addHandler(log_handler)

def analyze_logs():
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
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

# ------------------------------ Utilitários ----------------------------------
def qimage_to_base64_string(qimage_or_pixmap, fmt="PNG", quality=92) -> str:
    # Aceita QImage ou QPixmap
    if isinstance(qimage_or_pixmap, QPixmap):
        qimg = qimage_or_pixmap.toImage()
    else:
        qimg = qimage_or_pixmap
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimg.save(buf, fmt, quality)
    buf.close()
    return base64.b64encode(bytes(ba)).decode("utf-8")

def to_pixmap(obj) -> QPixmap:
    """Converte com segurança QImage/QPixmap/path para QPixmap."""
    if isinstance(obj, QPixmap):
        return obj
    if isinstance(obj, QImage):
        return QPixmap.fromImage(obj)
    if isinstance(obj, (str, Path)):
        pm = QPixmap(str(obj))
        if not pm.isNull():
            return pm
        raise ValueError(f'Não foi possível carregar imagem de {obj}')
    # Ex: QMimeData.imageData() pode retornar QImage; já tratado
    try:
        return QPixmap.fromImage(obj)  # tentativa final
    except Exception as e:
        raise TypeError("Tipo de imagem não suportado para conversão em QPixmap") from e

# ---------------------------- Diálogo de Config. ------------------------------
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

        # Aba Geral
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        self.seller_name_input = QLineEdit()
        self.seller_name_input.setText(self.settings.value("seller_name", ""))
        general_layout.addWidget(QLabel("Nome do Vendedor:"))
        general_layout.addWidget(self.seller_name_input)
        general_layout.addStretch()
        tab_widget.addTab(general_tab, "Geral")

        # Aba Webhook
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
        hbox.addWidget(test_btn); hbox.addStretch()
        webhook_layout.addLayout(hbox)
        webhook_layout.addWidget(self.webhook_status_label)
        webhook_layout.addStretch()
        tab_widget.addTab(webhook_tab, "Webhook")

        # Botões
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        # Aba Armazenamento (R2/S3)
        storage_tab = QWidget()
        storage_layout = QVBoxLayout(storage_tab)

        form = QFormLayout()
        self.r2_account_id = QLineEdit(self.settings.value("r2_account_id", ""))
        self.r2_bucket     = QLineEdit(self.settings.value("r2_bucket", ""))
        self.r2_endpoint   = QLineEdit(self.settings.value("r2_endpoint", ""))      # ex: https://<account>.r2.cloudflarestorage.com
        self.r2_public     = QLineEdit(self.settings.value("r2_public_base", ""))   # ex: https://<bucket>.<account>.r2.dev  ou domínio custom
        self.r2_prefix     = QLineEdit(self.settings.value("r2_prefix", "orcamentos/"))
        self.r2_cache      = QLineEdit(self.settings.value("r2_cache_control", "public, max-age=31536000, immutable"))

        self.r2_access_key = QLineEdit(self.settings.value("r2_access_key_id", ""))
        self.r2_secret_key = QLineEdit(self.settings.value("r2_secret_access_key", ""))
        self.r2_secret_key.setEchoMode(QLineEdit.Password)

        # Mostrar/ocultar segredo
        toggle_row = QHBoxLayout()
        toggle_btn = QPushButton("Mostrar")
        toggle_btn.setCheckable(True)
        def _toggle_secret():
            self.r2_secret_key.setEchoMode(QLineEdit.Normal if toggle_btn.isChecked() else QLineEdit.Password)
            toggle_btn.setText("Ocultar" if toggle_btn.isChecked() else "Mostrar")
        toggle_btn.clicked.connect(_toggle_secret)
        toggle_row.addWidget(self.r2_secret_key, 1)
        toggle_row.addWidget(toggle_btn)

        form.addRow("Account ID (R2):", self.r2_account_id)
        form.addRow("Bucket:", self.r2_bucket)
        form.addRow("Endpoint S3:", self.r2_endpoint)
        form.addRow("Base pública:", self.r2_public)
        form.addRow("Prefixo (opcional):", self.r2_prefix)
        form.addRow("Cache-Control:", self.r2_cache)
        form.addRow("Access Key ID:", self.r2_access_key)
        form.addRow("Secret Access Key:", QWidget())  # placeholder para alinhar
        storage_layout.addLayout(form)
        storage_layout.addLayout(toggle_row)

        # Ações de teste
        test_row = QHBoxLayout()
        btn_test_public = QPushButton("Testar Base Pública")
        test_row.addWidget(btn_test_public)
        self.r2_status = QLabel("Status: N/A")
        test_row.addStretch(1)
        test_row.addWidget(self.r2_status)
        storage_layout.addLayout(test_row)

        storage_layout.addStretch()
        tab_widget.addTab(storage_tab, "Armazenamento (R2/S3)")

        # Conectar teste
        btn_test_public.clicked.connect(self._test_public_base)

    def _test_public_base(self):
        base = (self.r2_public.text() or "").strip().rstrip("/")
        if not base:
            self.r2_status.setText("Status: Base pública vazia.")
            self.r2_status.setStyleSheet("color:#ffc107;")
            return

        # Tentativa HEAD; se falhar, cai para GET. Alguns buckets podem retornar 403/404 em "/",
        # então consideramos sucesso qualquer resposta HTTP que não seja erro de rede.
        url = QUrl(base + "/")  # raiz
        req = QNetworkRequest(url)
        self.r2_status.setText(f"Status: Testando {url.toString()} ...")
        self.r2_status.setStyleSheet("color:#17a2b8;")

        reply = self.nam.head(req)
        def _finish_head():
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            network_ok = (reply.error() == QNetworkReply.NoError)
            if network_ok and status is not None:
                # mesmo 403/404 indicam que o host/rota respondeu (DNS/TLS/rota OK)
                self.r2_status.setText(f"Status: OK (HTTP {status})")
                self.r2_status.setStyleSheet("color:#28a745;")
            else:
                # tenta GET para dar feedback melhor
                reply2 = self.nam.get(req)
                def _finish_get():
                    status2 = reply2.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                    if reply2.error() == QNetworkReply.NoError and status2 is not None:
                        self.r2_status.setText(f"Status: OK (HTTP {status2})")
                        self.r2_status.setStyleSheet("color:#28a745;")
                    else:
                        self.r2_status.setText(f"Status: Falha ({reply2.errorString()})")
                        self.r2_status.setStyleSheet("color:#dc3545;")
                    reply2.deleteLater()
                reply2.finished.connect(_finish_get)
            reply.deleteLater()
        reply.finished.connect(_finish_head)

    def accept(self):
        seller_name = self.seller_name_input.text().strip()
        webhook_url = self.webhook_url_input.text().strip()
        self.settings.setValue("seller_name", seller_name)
        self.settings.setValue("webhook_url", webhook_url)
        self.settings.setValue("r2_account_id",        self.r2_account_id.text().strip())
        self.settings.setValue("r2_bucket",            self.r2_bucket.text().strip())
        self.settings.setValue("r2_endpoint",          self.r2_endpoint.text().strip())
        self.settings.setValue("r2_public_base",       self.r2_public.text().strip())
        self.settings.setValue("r2_prefix",            self.r2_prefix.text().strip())
        self.settings.setValue("r2_cache_control",     self.r2_cache.text().strip())
        self.settings.setValue("r2_access_key_id",     self.r2_access_key.text().strip())
        self.settings.setValue("r2_secret_access_key", self.r2_secret_key.text().strip())
        self.settings.sync()
        logger.info(f"Saving Seller Name: {seller_name}")
        logger.info(f"Saving Webhook URL: [HIDDEN]")
        logger.info("Saving R2 settings (sem segredos nos logs).")
        super().accept()

# ---------------------------------- Execução ----------------------------------
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
