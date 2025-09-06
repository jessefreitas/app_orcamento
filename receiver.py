import base64
import datetime
import uuid
from pathlib import Path

from flask import Flask, request, jsonify

app = Flask(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

@app.route("/webhook", methods=['POST'])
def webhook_receiver():
    # 1. Obter nome e telefone dos parâmetros da URL
    name = request.args.get("name")
    phone = request.args.get("phone")

    if not name or not phone:
        return jsonify({"ok": False, "error": "Nome e telefone são obrigatórios"}), 400

    # 2. Obter o corpo JSON da requisição
    images_payload = request.get_json()
    if not isinstance(images_payload, list):
        return jsonify({"ok": False, "error": "Corpo da requisição inválido, esperado um array JSON"}), 400

    print(f"Recebido de: {name} ({phone})")

    saved_files = []
    # 3. Iterar sobre a lista de imagens, decodificar e salvar
    for i, item in enumerate(images_payload):
        try:
            # O cliente envia [[base64_str1], [base64_str2], ...]
            if not isinstance(item, list) or not item:
                print(f"Item {i} ignorado: formato inesperado.")
                continue

            base64_data = item[0]
            image_data = base64.b64decode(base64_data)

            # Gerar um nome de arquivo único
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"img-{ts}-{uuid.uuid4().hex[:6]}.png"
            path = UPLOAD_DIR / filename
            
            with open(path, "wb") as f:
                f.write(image_data)
            
            saved_files.append(filename)
            print(f"  - Imagem salva: {filename}")

        except (base64.binascii.Error, IndexError, TypeError) as e:
            print(f"Erro ao processar item {i}: {e}")
            continue
    
    if not saved_files:
        return jsonify({"ok": False, "error": "Nenhuma imagem válida foi processada"}), 400

    # 4. Retornar sucesso
    # O cliente espera a string "envio ok" na resposta
    return f"envio ok ({len(saved_files)} imagens salvas)", 200


if __name__ == "__main__":
    # A porta 8000 é comum, mas o webhook original não especifica.
    # Usaremos a porta 5000, que é o padrão do Flask.
    app.run(host="0.0.0.0", port=5000, debug=True)
