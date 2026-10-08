import json
from datetime import date
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from scripts import editorial_pipeline as pipeline
from scripts.configure_journal_cron import updated_crontab


def article():
    return {"title": "A concrete product change", "subtitle": "A specific decision for customers",
            "body": '<p>A reported change <a href="https://openai.com/story">OpenAI</a>.</p>',
            "image_prompt": "Official announcement artwork", "image_query": "the actual product"}


def sources():
    return [{"title": "Product announcement", "link": "https://openai.com/story", "source": "OpenAI",
             "research": "Verified details " * 250, "image_url": "https://images.example.com/story.png"}]


def test_research_rejects_headline_only_pages(monkeypatch):
    monkeypatch.setattr(pipeline, "download", lambda *a: ("https://openai.com/story", b"<article><p>A short headline and nothing more.</p></article>", "text/html"))
    with pytest.raises(pipeline.EditorialError, match="too little readable evidence"):
        pipeline.read_source("https://openai.com/story")


def test_research_keeps_full_evidence_and_exact_source_image(monkeypatch):
    text = "A documented feature has a defined operational constraint. " * 40
    html = f'<title>Product launch</title><meta property="og:image" content="/art.png"><nav>Noise</nav><article><p>{text}</p></article>'
    monkeypatch.setattr(pipeline, "download", lambda *a: ("https://openai.com/story", html.encode(), "text/html"))
    source = pipeline.read_source("https://openai.com/story")
    assert source["research"] == text.strip()
    assert source["source"] == "OpenAI"
    assert source["image_url"] == "https://openai.com/art.png"
    assert "Noise" not in source["research"]


@pytest.mark.parametrize("body", ['<script>alert(1)</script>', '<p onclick="x">Test</p>', '<a href="https://fake.example/facts">Claim</a>', '<img src="x">'])
def test_untrusted_html_and_unverified_links_are_rejected(body):
    draft = {**article(), "body": body}
    with pytest.raises(pipeline.EditorialError):
        pipeline.validate_html(draft, sources())


def test_verified_html_is_accepted():
    pipeline.validate_html(article(), sources())


def test_editorial_review_failure_never_publishes_filler():
    class Editor:
        def call(self, instructions, content, schema=None, **kwargs):
            return article() if schema == pipeline.ARTICLE_SCHEMA else {"facts_supported": False, "specific_and_useful": True, "sources_match_story": False, "issues": ["Wrong product"]}
    with pytest.raises(pipeline.EditorialError, match="after one revision"):
        pipeline.write_story(Editor(), "Dots AI", "developers", "Useful", sources(), lambda *a: None)


@pytest.mark.parametrize("relevant,confidence", [(False, 0.99), (True, 0.5), (True, True)])
def test_visual_reviewer_rejects_unrelated_or_uncertain_images(monkeypatch, relevant, confidence):
    monkeypatch.setattr(pipeline, "download", lambda *a: ("https://img.example/p.png", b"pixels", "image/png"))
    class Editor:
        def call(self, *args):
            return {"relevant": relevant, "confidence": confidence, "description": "A postage envelope", "reason": "Namesake mismatch"}
    assert pipeline.review_image(Editor(), {"url": "https://img.example/p.png"}, "Dots AI", article()) is None


def test_no_acceptable_image_stops_instead_of_stock_fallback(monkeypatch):
    monkeypatch.setattr(pipeline, "review_image", lambda *a: None)
    with pytest.raises(pipeline.EditorialError, match="No image passed"):
        pipeline.select_image(None, "GPT-6", article(), sources(), lambda *a, **k: {"url": "https://img.example/chip.png"}, [])


def test_publisher_artwork_review_precedes_commons(monkeypatch):
    monkeypatch.setattr(pipeline, "review_image", lambda editor, candidate, *a: {**candidate, "alt": "Verified product artwork"})
    image = pipeline.select_image(None, "Product", article(), sources(), lambda *a, **k: pytest.fail("No fallback needed"), [])
    assert image["source_url"] == "https://openai.com/story"


def test_refusal_and_truncated_provider_outputs_fail_closed():
    with pytest.raises(pipeline.EditorialError):
        pipeline.response_text({"status": "incomplete", "output": []})
    with pytest.raises(pipeline.EditorialError):
        pipeline.response_text({"status": "completed", "output": [{"content": [{"type": "refusal"}]}]})


def test_api_key_not_required_for_openclaw(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AUTO_POST_EDITORIAL_BACKEND", "openclaw")
    assert isinstance(pipeline.configured_editor(), pipeline.OpenClawEditor)


@pytest.mark.parametrize("tools,accepted", [([], False), (["view_image"], True)])
def test_openclaw_must_actually_open_image_before_approving(monkeypatch, tools, accepted):
    answer = {"relevant": True, "confidence": 0.99, "description": "Artwork", "reason": "Exact product"}
    envelope = {"result": {"payloads": [{"text": json.dumps(answer)}],
                           "meta": {"agentMeta": {"terminalReceipt": {"successfulToolNames": tools}}}}}
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(envelope)))
    content = [{"type": "input_image", "image_url": "data:image/png;base64,cGl4ZWxz"}]
    editor = pipeline.OpenClawEditor()
    if accepted:
        assert editor.call("Review", content, pipeline.IMAGE_SCHEMA)["relevant"] is True
    else:
        with pytest.raises(pipeline.EditorialError, match="did not inspect"):
            editor.call("Review", content, pipeline.IMAGE_SCHEMA)


def test_cron_update_preserves_other_jobs_and_is_idempotent():
    repo = PurePosixPath("/home/ayncode/Ay_python_app")
    current = "CRON_TZ=America/New_York\n58 23 * * * /home/ayncode/backup.sh\n5 9 * * * cd /home/ayncode/Ay_python_app && bash scripts/openclaw_publish.sh\n"
    updated = updated_crontab(current, repo)
    assert "58 23 * * * /home/ayncode/backup.sh" in updated
    assert "15 9 * * 5" in updated
    assert updated.count("scripts/openclaw_publish.sh") == 1
    assert updated_crontab(updated, repo) == updated


def test_cron_disable_removes_only_this_journal_job():
    repo = PurePosixPath("/home/ayncode/Ay_python_app")
    current = (
        "CRON_TZ=America/New_York\n"
        "58 23 * * * /home/ayncode/backup.sh\n"
        "15 9 * * 5 cd /home/ayncode/Ay_python_app && bash scripts/openclaw_publish.sh\n"
        "0 8 * * * cd /home/ayncode/other && bash scripts/openclaw_publish.sh\n"
        "# Previous journal: /home/ayncode/Ay_python_app/scripts/openclaw_publish.sh\n"
    )
    disabled = updated_crontab(current, repo, enabled=False)
    assert "15 9 * * 5" not in disabled
    assert "58 23 * * * /home/ayncode/backup.sh" in disabled
    assert "0 8 * * * cd /home/ayncode/other" in disabled
    assert "# Previous journal:" in disabled
    assert disabled.startswith("CRON_TZ=America/New_York\n")
    assert updated_crontab(disabled, repo, enabled=False) == disabled


def test_publisher_fails_closed_when_no_news(monkeypatch, tmp_path):
    from scripts import auto_publish
    monkeypatch.setattr(auto_publish, "parse_args", lambda: SimpleNamespace(commit=False, push=False, dry_run=True, preview_path=None))
    monkeypatch.setattr(auto_publish, "load_posts", lambda *a: [])
    monkeypatch.setattr(auto_publish, "fetch_recent_events", lambda *a, **k: [])
    monkeypatch.setattr(auto_publish, "collect_fallback_events", lambda *a, **k: [])
    monkeypatch.setattr(auto_publish, "save_posts", lambda *a: pytest.fail("Must not save filler"))
    monkeypatch.setenv("AUTO_POST_REQUIRE_GENERATOR", "true")
    monkeypatch.setenv("AUTO_POST_USE_REAL_EVENTS", "true")
    assert auto_publish.main() == 5


def test_dry_run_does_not_save_commit_or_push(monkeypatch, tmp_path):
    from scripts import auto_publish
    preview = tmp_path / "preview.json"
    monkeypatch.setattr(auto_publish, "parse_args", lambda: SimpleNamespace(commit=True, push=True, dry_run=True, preview_path=preview))
    monkeypatch.setattr(auto_publish, "load_posts", lambda *a: [])
    monkeypatch.setattr(auto_publish, "fetch_recent_events", lambda *a, **k: [{"title": "OpenAI launches a new developer tool", "source": "OpenAI"}])
    monkeypatch.setattr(auto_publish, "save_posts", lambda *a: pytest.fail("Dry run cannot save posts"))
    monkeypatch.setattr(auto_publish, "commit_and_push", lambda *a, **k: pytest.fail("Dry run cannot commit"))
    monkeypatch.setenv("AUTO_POST_REQUIRE_GENERATOR", "true")
    monkeypatch.setattr(pipeline, "configured_editor", lambda: SimpleNamespace(model="test", usage={}))
    monkeypatch.setattr(pipeline, "research_story", lambda *a: sources())
    monkeypatch.setattr(pipeline, "write_story", lambda *a: article())
    monkeypatch.setattr(pipeline, "select_image", lambda *a, **k: {"url": "https://images.example.com/real.png", "source_url": "https://openai.com/story", "credit": "OpenAI"})
    assert auto_publish.main() == 0
    assert json.loads(preview.read_text())["img_url"].endswith("real.png")
