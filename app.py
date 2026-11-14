"""GeoAgreste DroneTM — backend leve para servir a plataforma de planejamento de voo."""
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
CORS(app, resources={"/api/*": {"origins": os.environ.get("CORS_ORIGINS", "*").split(",")}})


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(
        status="ok",
        service="GeoAgreste DroneTM API",
        mode="planejamento_de_voo",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
