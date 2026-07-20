"""
Animagine Studio — local frontend server.

Run this on your laptop. It serves the web app to every device on the same
wifi network, and forwards generation/tag requests to your Colab backend so
the browser never has to talk to Colab (and ngrok) directly — which avoids
CORS headaches and keeps the Colab URL/API key out of browser dev tools on
shared devices.

Usage:
    pip install -r requirements.txt
    python server.py

Then open the printed "Local network URL" on any device connected to the
same wifi.
"""

import socket

import requests
from flask import Flask, jsonify, request, send_from_directory

import tag_lookup

app = Flask(__name__, static_folder="static", static_url_path="")

REQUEST_TIMEOUT_SECONDS = 600  # generation can take a while for larger batches


def get_lan_ip() -> str:
    """Best-effort guess of this machine's LAN IP, for the startup banner."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    payload = request.get_json(force=True, silent=True) or {}

    backend_url = (payload.pop("backend_url", "") or "").rstrip("/")
    api_key = payload.pop("api_key", "")

    if not backend_url:
        return jsonify({"error": "No backend URL configured. Set it in the settings panel."}), 400

    try:
        resp = requests.post(
            f"{backend_url}/generate",
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Couldn't reach the Colab backend. Is the Colab cell still running?"}), 502
    except requests.exceptions.Timeout:
        return jsonify({"error": "The backend took too long to respond and the request timed out."}), 504

    return jsonify(resp.json()), resp.status_code


@app.route("/api/status")
def api_status():
    backend_url = (request.args.get("backend_url", "") or "").rstrip("/")
    api_key = request.args.get("api_key", "")

    if not backend_url:
        return jsonify({"ok": False, "error": "No backend URL configured."}), 400

    try:
        resp = requests.get(
            f"{backend_url}/health",
            headers={"X-API-Key": api_key},
            timeout=15,
        )
    except requests.exceptions.RequestException:
        return jsonify({"ok": False, "error": "Couldn't reach the Colab backend."}), 502

    if resp.status_code != 200:
        return jsonify({"ok": False, "error": resp.json().get("detail", "Backend rejected the request.")}), resp.status_code

    return jsonify({"ok": True, **resp.json()})


@app.route("/api/tags")
def api_tags():
    """Danbooru tag autocomplete, served entirely from the local SQLite index
    built by data/build_tag_index.py — no network calls at request time."""
    query = request.args.get("q", "") or ""
    try:
        limit = int(request.args.get("limit", tag_lookup.DEFAULT_LIMIT))
    except ValueError:
        limit = tag_lookup.DEFAULT_LIMIT

    try:
        return jsonify(tag_lookup.search_tags(query, limit=limit))
    except tag_lookup.TagIndexNotBuilt as e:
        # Don't 500 the UI over this — just log it and return no suggestions.
        print(f"[tags] {e}")
        return jsonify([])


if __name__ == "__main__":
    lan_ip = get_lan_ip()
    port = 5000
    print("\n" + "=" * 60)
    print(" Animagine Studio frontend is running")
    print("=" * 60)
    print(f" On this computer : http://127.0.0.1:{port}")
    print(f" On your wifi     : http://{lan_ip}:{port}")
    print(" Open the wifi URL on any phone/tablet/laptop on the same network.")
    print("=" * 60 + "\n")
    app.run(host="0.0.0.0", port=port, threaded=True)
