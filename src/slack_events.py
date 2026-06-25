"""
src/slack_events.py — Slack Events API (F6, fundação).

Verificação de assinatura (HMAC-SHA256), parsing de payloads e handshake de
URL verification. Usado pelo endpoint /api/slack/events do servidor CT2 para
substituir gradualmente o polling do monitor.

A verificação de assinatura é a parte crítica de segurança: garante que o POST
veio mesmo do Slack (usa o Signing Secret do app), não de um terceiro. Por isso
esse endpoint é isento do gate anti-CSRF (que vale para o dashboard same-origin).
"""

import hashlib
import hmac
import os
import time

ENV_PATH = os.path.expanduser("~/.hermes/profiles/dalinar/.env")


def load_signing_secret(env_path=ENV_PATH):
    """Carrega o SLACK_SIGNING_SECRET de os.environ ou do .env do Dalinar."""
    val = os.environ.get("SLACK_SIGNING_SECRET")
    if val:
        return val.strip().strip("\"'")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SLACK_SIGNING_SECRET="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return None


def verify_slack_signature(signing_secret, timestamp, raw_body, signature, max_age=300):
    """Valida a assinatura `X-Slack-Signature` do request (HMAC-SHA256).

    Retorna False se faltar segredo/dados, se o timestamp for velho (>5min,
    anti-replay) ou se a assinatura não bater. Usa comparação de tempo constante.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > max_age:
            return False
    except (ValueError, TypeError):
        return False
    base = "v0:{}:{}".format(timestamp, raw_body).encode("utf-8")
    digest = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def parse_event(payload):
    """Classifica o payload do Slack.

    Retorna:
      ("challenge", valor)  — handshake de URL verification (responder o valor)
      ("event", event_dict) — evento real (event_callback)
      ("ignore", None)      — qualquer outra coisa
    """
    if not isinstance(payload, dict):
        return ("ignore", None)
    if payload.get("type") == "url_verification":
        return ("challenge", payload.get("challenge", ""))
    if payload.get("type") == "event_callback":
        return ("event", payload.get("event", {}) or {})
    return ("ignore", None)
