from flask import Flask, request
from pathlib import Path
app = Flask(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return {"ok": False, "error": "missing file"}, 400
    path = UPLOAD_DIR / f.filename
    f.save(path)
    return {"ok": True, "filename": f.filename, "size": path.stat().st_size}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)