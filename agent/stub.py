"""Stub agent — replaced in Phase 6 with the real Anthropic tool-use implementation."""

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "note": "stub — Phase 6 not yet implemented"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
