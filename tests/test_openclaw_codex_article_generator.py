import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "openclaw_codex_article_generator.py"
SPEC = importlib.util.spec_from_file_location("openclaw_codex_article_generator", MODULE_PATH)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_extract_text_from_openclaw_outputs_text_shape():
    response = {
        "ok": True,
        "capability": "model.run",
        "outputs": [
            {
                "type": "text",
                "text": '{"title":"Title","subtitle":"Sub","body":"<p>Body</p>"}',
            }
        ],
    }

    assert generator.extract_text(response) == '{"title":"Title","subtitle":"Sub","body":"<p>Body</p>"}'


def test_extract_text_from_nested_output_content_shape():
    response = {
        "ok": True,
        "outputs": [
            {
                "message": {
                    "content": [
                        {
                            "type": "output_text",
                            "output_text": '{"title":"Title","subtitle":"Sub","body":"<p>Body</p>"}',
                        }
                    ]
                }
            }
        ],
    }

    assert generator.extract_text(response) == '{"title":"Title","subtitle":"Sub","body":"<p>Body</p>"}'


def test_parse_article_json_removes_markdown_fence():
    article = generator.parse_article_json(
        """
```json
{"title":"Title","subtitle":"Sub","body":"<p>Body</p>"}
```
""".strip()
    )

    assert article["title"] == "Title"
    assert article["subtitle"] == "Sub"
    assert article["body"] == "<p>Body</p>"


def test_build_social_agent_prompt_contains_payload_context():
    payload = {
        "topic": "AI founders",
        "audience": "founders",
        "angle": "practical",
        "events": [{"title": "Headline", "source": "Source", "published": "Today", "link": "https://example.com"}],
    }

    prompt = generator.build_social_agent_prompt(payload)

    assert "JSON only" in prompt
    assert "AI founders" in prompt
    assert "Headline" in prompt
