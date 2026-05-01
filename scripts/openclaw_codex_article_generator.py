#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Any


DEFAULT_MODEL = "openai-codex/gpt-5.4"
DEFAULT_SOCIAL_AGENT_LABEL = "ayo-social-media-researcher"
TEXT_KEYS = ("output_text", "text", "content", "message", "completion", "response", "result", "stdout", "value")
PRIORITY_CONTAINER_KEYS = ("outputs", "output", "data", "choices", "messages", "message", "result", "response")


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise RuntimeError("No JSON payload received on stdin.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object on stdin.")
    return payload


def social_agent_enabled() -> bool:
    value = os.environ.get("AUTO_POST_USE_SOCIAL_AGENT", "true")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_social_agent_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are helping prepare a publish-ready AyNcode tech article. "
        "Use the supplied topic, audience, angle, and recent event list to produce JSON only "
        "with exactly these keys: title, subtitle, body. "
        "The body must be clean HTML using tags like <p>, <h2>, <ul>, <li>, <a>. "
        "Do not include markdown fences or extra commentary. "
        "Use only the provided event headlines for current-event claims. "
        "Do not invent facts, quotes, statistics, or company statements. "
        "Include a short source-context section with links.\n\n"
        f"Payload:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def try_social_agent(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not social_agent_enabled():
        return None

    session_key = os.environ.get("AUTO_POST_SOCIAL_AGENT_SESSION_KEY", "").strip()
    label = os.environ.get("AUTO_POST_SOCIAL_AGENT_LABEL", DEFAULT_SOCIAL_AGENT_LABEL).strip() or DEFAULT_SOCIAL_AGENT_LABEL
    timeout = os.environ.get("AUTO_POST_SOCIAL_AGENT_TIMEOUT", "180").strip()

    command = [
        "openclaw",
        "sessions",
        "send",
        "--json",
        "--timeout-seconds",
        timeout,
        "--message",
        build_social_agent_prompt(payload),
    ]
    if session_key:
        command.extend(["--session-key", session_key])
    else:
        command.extend(["--label", label])

    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return None

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    text_output = extract_text(envelope)
    article = parse_article_json(text_output)
    return article if isinstance(article, dict) else None


def build_prompt(payload: dict[str, Any]) -> str:
    topic = payload.get("topic", "")
    audience = payload.get("audience", "")
    angle = payload.get("angle", "")
    instructions = payload.get("instructions", "Return JSON only with title, subtitle, and body.")
    events = payload.get("events", []) or []

    event_lines = []
    for index, event in enumerate(events, start=1):
        event_lines.append(
            f"{index}. Title: {event.get('title', '')}\n"
            f"   Source: {event.get('source', 'Unknown')}\n"
            f"   Published: {event.get('published', 'Unknown')}\n"
            f"   URL: {event.get('link', '')}"
        )
    events_block = "\n\n".join(event_lines) if event_lines else "No recent events were provided."

    return f"""
You are writing a publish-ready AyNcode tech article.

Return JSON only, with exactly these top-level keys:
- title
- subtitle
- body

Requirements:
- body must be clean HTML using tags like <p>, <h2>, <ul>, <li>, <a>
- do not wrap the JSON in markdown
- do not include any prose before or after the JSON
- use only the provided event headlines for current-event claims
- do not invent facts, quotes, statistics, or company statements
- include a short source-context section with links
- target 700 to 1100 words
- make it practical and useful for the intended audience

Topic: {topic}
Audience: {audience}
Angle: {angle}

Instructions:
{instructions}

Recent events from the last 24 hours:
{events_block}
""".strip()


def first_text(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, list):
        text_parts = []
        for item in value:
            text_value = first_text(item)
            if text_value:
                text_parts.append(text_value)
        return "\n".join(text_parts).strip()

    if isinstance(value, dict):
        for key in PRIORITY_CONTAINER_KEYS:
            if key in value:
                text_value = first_text(value[key])
                if text_value:
                    return text_value

        for key in TEXT_KEYS:
            if key in value:
                text_value = first_text(value[key])
                if text_value:
                    return text_value

        for child_value in value.values():
            text_value = first_text(child_value)
            if text_value:
                return text_value

    return ""


def response_shape(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {key: response_shape(child, depth + 1) for key, child in list(value.items())[:12]}
    if isinstance(value, list):
        return [response_shape(item, depth + 1) for item in value[:3]]
    return type(value).__name__


def extract_text(response: Any) -> str:
    text = first_text(response)
    if text:
        return text

    shape = json.dumps(response_shape(response), ensure_ascii=False)
    raise RuntimeError(f"Could not extract model text from OpenClaw JSON response. Shape: {shape}")


def parse_article_json(text_output: str) -> dict[str, Any]:
    text = text_output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        article = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        article = json.loads(text[start:end + 1])

    if not isinstance(article, dict):
        raise RuntimeError("Generated article JSON must be an object.")
    return article


def main() -> int:
    payload = read_payload()

    social_article = try_social_agent(payload)
    if social_article:
        article = social_article
    else:
        prompt = build_prompt(payload)
        model = os.environ.get("OPENCLAW_ARTICLE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

        result = subprocess.run(
            [
                "openclaw",
                "infer",
                "model",
                "run",
                "--model",
                model,
                "--prompt",
                prompt,
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "OpenClaw infer command failed.")

        envelope = json.loads(result.stdout)
        text_output = extract_text(envelope)
        article = parse_article_json(text_output)

    for field in ("title", "subtitle", "body"):
        if not isinstance(article.get(field), str) or not article[field].strip():
            raise RuntimeError(f"Generated article is missing a non-empty '{field}'.")

    print(json.dumps({
        "title": article["title"].strip(),
        "subtitle": article["subtitle"].strip(),
        "body": article["body"].strip(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
