#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Any


DEFAULT_MODEL = "openai-codex/gpt-5.4"


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise RuntimeError("No JSON payload received on stdin.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object on stdin.")
    return payload


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


def extract_text(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("output_text", "text", "content"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        output = response.get("output")
        if isinstance(output, list):
            text_parts = []
            for item in output:
                if isinstance(item, dict):
                    content = item.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                text_value = part.get("text") or part.get("output_text")
                                if isinstance(text_value, str) and text_value.strip():
                                    text_parts.append(text_value.strip())
                elif isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
            if text_parts:
                return "\n".join(text_parts)

    if isinstance(response, list):
        for item in response:
            extracted = extract_text(item)
            if extracted:
                return extracted

    raise RuntimeError("Could not extract model text from OpenClaw JSON response.")


def main() -> int:
    payload = read_payload()
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
    article = json.loads(text_output)

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
