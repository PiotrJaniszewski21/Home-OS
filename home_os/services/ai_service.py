import json
from abc import ABC, abstractmethod

import httpx


class AIProvider(ABC):
    @abstractmethod
    def complete(self, messages, tools=None):
        ...


class OllamaProvider(AIProvider):
    def __init__(self, url="http://localhost:11434", model="llama3"):
        self.url = url.rstrip("/")
        self.model = model

    def complete(self, messages, tools=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        resp = httpx.post(
            f"{self.url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        return msg.get("content", ""), msg.get("tool_calls", [])


class ClaudeProvider(AIProvider):
    def __init__(self, api_key, model="claude-sonnet-4-6-20250514"):
        self.api_key = api_key
        self.model = model

    def complete(self, messages, tools=None):
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        system_msg = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                chat_messages.append(m)

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": chat_messages,
        }
        if system_msg:
            payload["system"] = system_msg
        if tools:
            payload["tools"] = self._convert_tools(tools)

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        text_parts = []
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "function": {"name": block["name"], "arguments": block["input"]},
                })

        return "\n".join(text_parts), tool_calls

    def _convert_tools(self, tools):
        converted = []
        for t in tools:
            converted.append({
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {}),
            })
        return converted


class OpenAIProvider(AIProvider):
    def __init__(self, api_key, model="gpt-4o"):
        self.api_key = api_key
        self.model = model

    def complete(self, messages, tools=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]

        content = choice.get("content", "") or ""
        tool_calls = []
        for tc in choice.get("tool_calls", []):
            tool_calls.append({
                "id": tc["id"],
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": json.loads(tc["function"]["arguments"]),
                },
            })

        return content, tool_calls


class BedrockProvider(AIProvider):
    def __init__(self, access_key, secret_key, region="us-east-1", model="anthropic.claude-sonnet-4-6-20250514-v1:0"):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.model = model

    def _sign_request(self, method, url, headers, body):
        """AWS Signature V4 signing for Bedrock."""
        import hashlib
        import hmac
        from datetime import datetime, timezone
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname
        path = parsed.path

        now = datetime.now(timezone.utc)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")

        service = "bedrock"
        credential_scope = f"{datestamp}/{self.region}/{service}/aws4_request"

        payload_hash = hashlib.sha256(body.encode()).hexdigest()

        canonical_headers = f"content-type:application/json\nhost:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"

        canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

        def sign(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = sign(f"AWS4{self.secret_key}".encode(), datestamp)
        k_region = sign(k_date, self.region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

        headers.update({
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": auth_header,
        })
        return headers

    def complete(self, messages, tools=None):
        url = f"https://bedrock-runtime.{self.region}.amazonaws.com/model/{self.model}/converse"

        system_msg = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            elif m["role"] == "user":
                chat_messages.append({"role": "user", "content": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                chat_messages.append({"role": "assistant", "content": [{"text": m.get("content", "")}]})

        payload = {
            "modelId": self.model,
            "messages": chat_messages,
            "inferenceConfig": {"maxTokens": 4096},
        }
        if system_msg:
            payload["system"] = [{"text": system_msg}]
        if tools:
            tool_config = []
            for t in tools:
                tool_config.append({
                    "toolSpec": {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "inputSchema": {"json": t["function"].get("parameters", {})},
                    }
                })
            payload["toolConfig"] = {"tools": tool_config}

        body = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        headers = self._sign_request("POST", url, headers, body)

        resp = httpx.post(url, content=body, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        text_parts = []
        tool_calls = []
        for block in data.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tool_calls.append({
                    "id": block["toolUse"]["toolUseId"],
                    "function": {
                        "name": block["toolUse"]["name"],
                        "arguments": block["toolUse"]["input"],
                    },
                })

        return "\n".join(text_parts), tool_calls


def create_provider(config):
    provider = config["ai"]["provider"]

    if not provider:
        raise ValueError("AI provider not configured. Go to AI Settings to set up.")

    if provider == "ollama":
        cfg = config["ai"]["ollama"]
        return OllamaProvider(url=cfg["url"], model=cfg["model"])
    elif provider == "claude":
        cfg = config["ai"]["claude"]
        if not cfg.get("api_key"):
            raise ValueError("Claude API key not configured")
        return ClaudeProvider(api_key=cfg["api_key"], model=cfg["model"])
    elif provider == "openai":
        cfg = config["ai"]["openai"]
        if not cfg.get("api_key"):
            raise ValueError("OpenAI API key not configured")
        return OpenAIProvider(api_key=cfg["api_key"], model=cfg["model"])
    elif provider == "bedrock":
        cfg = config["ai"]["bedrock"]
        if not cfg.get("access_key") or not cfg.get("secret_key"):
            raise ValueError("AWS Bedrock credentials not configured")
        return BedrockProvider(
            access_key=cfg["access_key"],
            secret_key=cfg["secret_key"],
            region=cfg.get("region", "us-east-1"),
            model=cfg["model"],
        )
    else:
        raise ValueError(f"Unknown AI provider: {provider}")
