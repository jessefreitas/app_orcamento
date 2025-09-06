import sys
import os
import io
import base64
import json
import mimetypes
from datetime import datetime
from typing import List, Tuple

import requests
from PySide6.QtCore import (
    Qt, QSize, QMimeData, Signal, QObject, QThread, QByteArray, QBuffer, QSettings
)
from PySide6.QtGui import (
    QGuiApplication, QDragEnterEvent, QDropEvent, QKeySequence, QPixmap, QAction
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QProgressBar,
    QAbstractItemView, QStyle, QSpacerItem, QSizePolicy, QCheckBox
)


# ---------- Utilidades ----------

def qimage_to_png_bytes(qimg) -> bytes:
    """Converte QImage/QPixmap em PNG bytes."""
    if isinstance(qimg, QPixmap):
        qimg = qimg.toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    qimg.save(buf, "PNG")
    return bytes(ba)

def guess_mime_from_filename(name: str) -> str:
    mtype, _ = mimetypes.guess_type(name)
    return mtype or "application/octet-stream"

def png_bytes_to_base64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ---------- Worker de Envio (Thread) ----------

class SenderWorker(QObject):
    progressed = Signal(int)           # progresso 0..100
    finished = Signal(bool, str)       # sucesso, mensagem
    def __init__(self, webhook_url: str, seller_name: str, client_name: str,
                 phone: str, conversation_id: str,
                 images: List[Tuple[str, str, str]],  # (filename, mime, base64)
                 send_chained_requests: bool = False):
        super().__init__()
        self.webhook_url = webhook_url
        self.seller_name = seller_name
        self.client_name = client_name
        self.phone = phone
        self.conversation_id = conversation_id
        self.images = images
        self.send_chained_requests = send_chained_requests

    def _payload_base(self):
        return {
            "seller_name": self.seller_name or "",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "client": {
                "name": self.client_name,
                "phone": self.phone or None,
                "conversation_id": self.conversation_id,
            }
        }

    def run(self):
        try:
            total_steps = len(self.images) if self.send_chained_requests else 1
            total_steps = max(1, total_steps)
            # Envio encadeado (requisições separadas) opcional
            if self.send_chained_requests:
                for idx, (fname, mime, b64) in enumerate(self.images, start=1):
                    payload = self._payload_base()
                    payload["images"] = [{
                        "index": idx,
                        "filename": fname,
                        "mime": mime,
                        "base64": b64
                    }]
                    r = requests.post(self.webhook_url, json=payload, timeout=30)
                    r.raise_for_status()
                    self.progressed.emit(int(idx * 100 / total_steps))
                self.finished.emit(True, f"{len(self.images)} imagem(ns) enviada(s) com sucesso (encadeado).")
                return

            # Envio único com “arquivos separados” em um array ordenado
            images_obj = []
            for i, (fname, mime, b64) in enumerate(self.images, start=1):
                images_obj.append({
                    "index": i,
                    "filename": fname,
                    "mime": mime,
                    "base64": b64
                })

            payload = self._payload_base()
            payload["images"] = images_obj

            r = requests.post(self.webhook_url, json=payload, timeout=60)
            r.raise_for_status()
            self.progressed.emit(100)
            self.finished.emit(True, f"{len(self.images)} imagem(ns) enviada(s) com sucesso.")
        except requests.RequestException as e:
            self.finished.emit(False, f"Falha no envio HTTP: {e}")
        except Exception as e:
            self.finished.emit(False, f"Erro inesperado: {e}")


# ---------- Widget da Lista de Imagens (colagem/arrastar-soltar) ----------

class ImageList(QListWidget):
    images_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setIconSize(QSize(88, 88))
        # Ação de deletar selecionados (Del)
        delete_action = QAction("Remover", self)
        delete_action.setShortcut(QKeySequence.Delete)
        delete_action.triggered.connect(self.remove_selected)
        self.addAction(delete_action)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        md: QMimeData = e.mimeData()
        added = 0
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    self._add_file_path(path)
                    added += 1
        elif md.hasImage():
            # Colar imagem “pura” vinda do SO
            img = md.imageData()
            self._add_qimage(img, suggested_name="clipboard.png")
            added += 1
        if added:
            self.images_changed.emit()

    def keyPressEvent(self, e):
        # Ctrl+V para colar do clipboard
        if e.matches(QKeySequence.Paste):
            cb = QGuiApplication.clipboard()
            md = cb.mimeData()
            if md.hasImage():
                img = md.imageData()
                self._add_qimage(img, suggested_name="clipboard.png")
                self.images_changed.emit()
                return
            elif md.hasUrls():
                for url in md.urls():
                    if url.isLocalFile():
                        self._add_file_path(url.toLocalFile())
                self.images_changed.emit()
                return
        super().keyPressEvent(e)

    def _add_qimage(self, qimg, suggested_name="clipboard.png"):
        # Gera thumbnail e item
        pix = QPixmap.fromImage(qimg) if hasattr(qimg, "size") else QPixmap()
        icon = self.style().standardIcon(QStyle.SP_FileIcon) if pix.isNull() else QPixmap(pix).scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        item = QListWidgetItem()
        if isinstance(icon, QPixmap):
            item.setIcon(icon)
        else:
            item.setIcon(icon)
        item.setText(suggested_name)
        item.setData(Qt.UserRole, ("__clipboard__", qimg))  # marcador de origem
        self.addItem(item)

    def _add_file_path(self, path: str):
        if not os.path.isfile(path):
            return
        pix = QPixmap(path)
        icon = self.style().standardIcon(QStyle.SP_FileIcon) if pix.isNull() else pix.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        item = QListWidgetItem()
        if isinstance(icon, QPixmap):
            item.setIcon(icon)
        else:
            item.setIcon(icon)
        item.setText(os.path.basename(path))
        item.setToolTip(path)
        item.setData(Qt.UserRole, ("__file__", path))
        self.addItem(item)

    def remove_selected(self):
        for it in self.selectedItems():
            self.takeItem(self.row(it))
        self.images_changed.emit()

    def clear_all(self):
        self.clear()
        self.images_changed.emit()

    def collect_images(self) -> List[Tuple[str, str, str]]:
        """Retorna lista [(filename, mime, base64), ...] em ordem."""
        result = []
        for i in range(self.count()):
            it = self.item(i)
            origin, data = it.data(Qt.UserRole)
            if origin == "__file__":
                path = data
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                    b64 = base64.b64encode(raw).decode("ascii")
                    mime = guess_mime_from_filename(path)
                    result.append((os.path.basename(path), mime, b64))
                except Exception:
                    # Ignora arquivo inválido
                    continue
            else:
                # clipboard QImage
                qimg = data
                png = qimage_to_png_bytes(qimg)
                b64 = png_bytes_to_base64(png)
                result.append(("clipboard.png", "image/png", b64))
        return result


# ---------- Janela Principal ----------

class FloatingUploader(QWidget):
    ORG = "OmniForge"
    APP = "FloatingUploader"

    def __init__(self):
        super().__init__()
        # Janela flutuante (sem moldura / sempre no topo / não aparece na barra de tarefas)
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(780, 520)

        # Estado arraste
        self._drag_pos = None

        # QSettings
        self.settings = QSettings(self.ORG, self.APP)

        # Conteúdo (cartão central)
        root = QVBoxLayout(self)
        card = QWidget(self)
        card.setObjectName("card")
        root.addWidget(card)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(14, 14, 14, 14)
        card_l.setSpacing(10)

        # Barra superior (título + fechar)
        top = QHBoxLayout()
        self.title = QLabel("Uploader de Orçamentos (Janela Flutuante)")
        self.title.setStyleSheet("font-weight:600;")
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.clicked.connect(self.close)
        btn_close.setToolTip("Fechar")
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(btn_close)
        card_l.addLayout(top)

        # Abas
        self.tabs = QTabWidget()
        card_l.addWidget(self.tabs)

        # --- Aba Orçamento ---
        self.tab_main = QWidget()
        self.tabs.addTab(self.tab_main, "Orçamento")

        m = QVBoxLayout(self.tab_main)
        form1 = QHBoxLayout()
        self.in_client = QLineEdit()
        self.in_client.setPlaceholderText("Nome do cliente (obrigatório)")
        self.in_phone = QLineEdit()
        self.in_phone.setPlaceholderText("Telefone (opcional)")
        self.in_conversation = QLineEdit()
        self.in_conversation.setPlaceholderText("ID da conversa (obrigatório)")
        form1.addWidget(self.in_client, 4)
        form1.addWidget(self.in_phone, 3)
        form1.addWidget(self.in_conversation, 3)
        m.addLayout(form1)

        hint = QLabel(
            "Cole prints com Ctrl+V (ou arraste arquivos de imagem). Máximo de 10 itens."
        )
        hint.setStyleSheet("color:#666; font-size:12px;")
        m.addWidget(hint)

        self.img_list = ImageList()
        m.addWidget(self.img_list, 1)

        # Botões de ação
        actions = QHBoxLayout()
        btn_add = QPushButton("Adicionar arquivos…")
        btn_clear = QPushButton("Limpar lista")
        actions.addWidget(btn_add)
        actions.addWidget(btn_clear)
        actions.addStretch(1)
        self.chk_chain = QCheckBox("Enviar em requisições separadas (encadeadas)")
        self.chk_chain.setToolTip("Se marcado, envia uma requisição por imagem, na ordem.")
        actions.addWidget(self.chk_chain)
        m.addLayout(actions)

        btn_add.clicked.connect(self.add_files)
        btn_clear.clicked.connect(self.img_list.clear_all)
        self.img_list.images_changed.connect(self._enforce_limit)

        # Rodapé: progresso + enviar/fechar
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.btn_send = QPushButton("Enviar orçamento")
        self.btn_send.setDefault(True)
        self.btn_send.clicked.connect(self.handle_send)
        self.btn_close2 = QPushButton("Fechar")
        self.btn_close2.clicked.connect(self.close)
        footer.addWidget(self.progress, 4)
        footer.addItem(QSpacerItem(10, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))
        footer.addWidget(self.btn_send)
        footer.addWidget(self.btn_close2)
        m.addLayout(footer)

        # --- Aba Configurações ---
        self.tab_cfg = QWidget()
        self.tabs.addTab(self.tab_cfg, "Configurações")
        c = QVBoxLayout(self.tab_cfg)
        cfg_row1 = QHBoxLayout()
        self.in_seller = QLineEdit()
        self.in_seller.setPlaceholderText("Nome do vendedor (definir aqui)")
        self.in_webhook = QLineEdit()
        self.in_webhook.setPlaceholderText("Webhook URL (definir aqui)")
        cfg_row1.addWidget(self.in_seller, 1)
        cfg_row1.addWidget(self.in_webhook, 2)
        c.addLayout(cfg_row1)

        cfg_btns = QHBoxLayout()
        self.btn_save = QPushButton("Salvar configurações")
        self.btn_save.clicked.connect(self.save_settings)
        cfg_btns.addStretch(1)
        cfg_btns.addWidget(self.btn_save)
        c.addLayout(cfg_btns)

        c.addStretch(1)
        note = QLabel(
            "Observação: o webhook e o nome do vendedor **não** são definidos na inicialização; "
            "configure-os aqui antes do envio."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666; font-size:12px;")
        c.addWidget(note)

        # Carrega settings
        self.load_settings()

        # Estilo (cartão)
        self.setStyleSheet("""
            QWidget#card {
                background: #111; 
                border-radius: 12px; 
                border: 1px solid #2a2a2a;
            }
            QLabel { color: #eaeaea; }
            QLineEdit {
                background: #1a1a1a; 
                border: 1px solid #333; 
                border-radius: 6px; 
                padding: 6px; 
                color: #eaeaea;
                selection-background-color: #444;
            }
            QPushButton {
                background: #262626; 
                border: 1px solid #3a3a3a; 
                border-radius: 6px; 
                padding: 6px 10px; 
                color: #eaeaea;
            }
            QPushButton:hover { border-color: #6a5acd; }
            QTabBar::tab {
                color: #ddd; background:#1a1a1a; border:1px solid #333; padding:6px 10px; border-top-left-radius:6px; border-top-right-radius:6px;
            }
            QTabBar::tab:selected { background:#222; border-bottom-color:#222; }
            QListWidget { background:#0f0f0f; border:1px solid #2a2a2a; border-radius:8px; color:#ddd; }
            QProgressBar { background:#1a1a1a; border:1px solid #333; border-radius:6px; color:#eaeaea; text-align:center; }
            QProgressBar::chunk { background:#6a5acd; }
            QCheckBox { color: #eaeaea; }
        """)

    # ----- Movimento da janela -----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ----- Settings -----
    def load_settings(self):
        self.in_seller.setText(self.settings.value("seller_name", "", str))
        self.in_webhook.setText(self.settings.value("webhook_url", "", str))

    def save_settings(self):
        self.settings.setValue("seller_name", self.in_seller.text().strip())
        self.settings.setValue("webhook_url", self.in_webhook.text().strip())
        QMessageBox.information(self, "Configurações", "Configurações salvas com sucesso.")

    # ----- Lógica -----
    def _enforce_limit(self):
        # Garante no máximo 10 itens, removendo excedentes do fim
        while self.img_list.count() > 10:
            self.img_list.takeItem(self.img_list.count() - 1)
        self.progress.setValue(0)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Selecionar imagens", "",
            "Imagens (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;Todos (*.*)"
        )
        if not files:
            return
        space_left = max(0, 10 - self.img_list.count())
        for p in files[:space_left]:
            self.img_list._add_file_path(p)
        if len(files) > space_left:
            QMessageBox.warning(self, "Limite", "Foram adicionadas apenas as 10 primeiras imagens.")
        self.img_list.images_changed.emit()

    def validate(self) -> Tuple[bool, str]:
        if not self.in_webhook.text().strip():
            return False, "Defina o Webhook na aba Configurações."
        if not self.in_seller.text().strip():
            return False, "Defina o Nome do vendedor na aba Configurações."
        if not self.in_client.text().strip():
            return False, "Informe o Nome do cliente."
        if not self.in_conversation.text().strip():
            return False, "Informe o ID da conversa (obrigatório)."
        if self.img_list.count() == 0:
            return False, "Adicione ao menos 1 imagem."
        return True, ""

    def handle_send(self):
        ok, msg = self.validate()
        if not ok:
            QMessageBox.warning(self, "Campos obrigatórios", msg)
            return

        images = self.img_list.collect_images()
        if len(images) > 10:
            images = images[:10]  # salvaguarda

        # Desliga botões durante envio
        self.btn_send.setEnabled(False)
        self.btn_close2.setEnabled(False)
        self.progress.setValue(5)

        # Prepara Worker/Thread
        self.thread = QThread()
        self.worker = SenderWorker(
            webhook_url=self.in_webhook.text().strip(),
            seller_name=self.in_seller.text().strip(),
            client_name=self.in_client.text().strip(),
            phone=self.in_phone.text().strip(),
            conversation_id=self.in_conversation.text().strip(),
            images=images,
            send_chained_requests=self.chk_chain.isChecked()
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progressed.connect(self.progress.setValue)
        self.worker.finished.connect(self._on_send_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _on_send_finished(self, success: bool, message: str):
        self.btn_send.setEnabled(True)
        self.btn_close2.setEnabled(True)
        if success:
            QMessageBox.information(self, "Envio concluído", message)
            # Mantém lista (caso deseje reenviar), mas zera barra
            self.progress.setValue(0)
        else:
            QMessageBox.critical(self, "Erro no envio", message)


def main():
    app = QApplication(sys.argv)
    w = FloatingUploader()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
</final_file_content>
</write_to_file>
