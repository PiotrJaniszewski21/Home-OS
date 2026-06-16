import json
from pathlib import Path

import yaml
from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from home_os.config import ROOT_DIR
from home_os.modules.ai import ai_bp

SYSTEM_PROMPT = """You are Home OS Assistant, an AI helper for a personal NAS (Network Attached Storage) system. You help the user find files, understand their storage usage, and manage their system.

You have access to tools to query the system. Always use tools to get current information rather than guessing.

Guidelines:
- Be concise and direct
- When listing files, format them clearly with sizes and dates
- For storage questions, include specific numbers
- If a query is ambiguous, ask for clarification
- Never modify or delete files unless explicitly asked"""


class _AttrDict(dict):
    """Dict that allows attribute access for template convenience."""
    def __getattr__(self, key):
        try:
            val = self[key]
            if isinstance(val, dict):
                return _AttrDict(val)
            return val
        except KeyError:
            return ""


@ai_bp.route("/ai")
@login_required
def chat():
    config = current_app.config["_raw_config"]
    provider = config["ai"].get("provider", "")
    if not provider:
        return redirect(url_for("ai.settings"))
    return render_template("ai/chat.html")


@ai_bp.route("/ai/settings")
@login_required
def settings():
    config = current_app.config["_raw_config"]
    ai = config["ai"]
    ai.setdefault("bedrock", {"access_key": "", "secret_key": "", "region": "us-east-1", "model": "anthropic.claude-sonnet-4-6-20250514-v1:0"})
    ai_config = _AttrDict(ai)
    return render_template("ai/settings.html", ai_config=ai_config)


@ai_bp.route("/ai/settings", methods=["POST"])
@login_required
def save_settings():
    config = current_app.config["_raw_config"]

    provider = request.form.get("provider", "")
    config["ai"]["provider"] = provider

    config["ai"]["ollama"]["url"] = request.form.get("ollama_url", "http://localhost:11434")
    config["ai"]["ollama"]["model"] = request.form.get("ollama_model", "llama3")

    claude_key = request.form.get("claude_api_key", "")
    if claude_key:
        config["ai"]["claude"]["api_key"] = claude_key
    config["ai"]["claude"]["model"] = request.form.get("claude_model", "claude-sonnet-4-6-20250514")

    openai_key = request.form.get("openai_api_key", "")
    if openai_key:
        config["ai"]["openai"]["api_key"] = openai_key
    config["ai"]["openai"]["model"] = request.form.get("openai_model", "gpt-4o")

    bedrock_access = request.form.get("bedrock_access_key", "")
    bedrock_secret = request.form.get("bedrock_secret_key", "")
    if bedrock_access:
        config["ai"]["bedrock"]["access_key"] = bedrock_access
    if bedrock_secret:
        config["ai"]["bedrock"]["secret_key"] = bedrock_secret
    config["ai"]["bedrock"]["region"] = request.form.get("bedrock_region", "us-east-1")
    config["ai"]["bedrock"]["model"] = request.form.get("bedrock_model", "anthropic.claude-sonnet-4-6-20250514-v1:0")

    config_path = ROOT_DIR / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    flash("AI settings saved.", "success")
    return redirect(url_for("ai.settings"))


@ai_bp.route("/api/ai/chat", methods=["POST"])
@login_required
def chat_api():
    from home_os.services.ai_service import create_provider
    from home_os.services.ai_tools import TOOL_DEFINITIONS, AIToolExecutor
    from home_os.services.rate_limiter import RateLimiter

    _ai_limiter = getattr(chat_api, '_limiter', None)
    if not _ai_limiter:
        _ai_limiter = RateLimiter(max_attempts=20, window_seconds=60)
        chat_api._limiter = _ai_limiter

    user_key = f"ai_{current_user.id}"
    if _ai_limiter.is_limited(user_key):
        return jsonify({"ok": False, "error": "Rate limited. Try again in a minute."}), 429
    _ai_limiter.record(user_key)

    config = current_app.config["_raw_config"]
    data = request.get_json()
    user_message = data.get("message", "").strip()
    history = data.get("history", [])

    if not user_message:
        return jsonify({"ok": False, "error": "Message required"}), 400

    try:
        provider = create_provider(config)
    except (ValueError, KeyError) as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    executor = AIToolExecutor(config["storage"]["root"])

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    max_rounds = 5
    for _ in range(max_rounds):
        try:
            text, tool_calls = provider.complete(messages, tools=TOOL_DEFINITIONS)
        except Exception as e:
            return jsonify({"ok": False, "error": f"AI error: {e}"}), 500

        if not tool_calls:
            return jsonify({"ok": True, "data": {"response": text}})

        messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})

        for tc in tool_calls:
            func = tc["function"]
            args = func["arguments"] if isinstance(func["arguments"], dict) else json.loads(func["arguments"])
            result = executor.execute(func["name"], args)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(result),
            })

    return jsonify({"ok": True, "data": {"response": text or "I wasn't able to complete that request."}})
