import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "auto_publish.py"
SPEC = importlib.util.spec_from_file_location("auto_publish", MODULE_PATH)
auto_publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto_publish)


def test_choose_generation_topic_uses_top_event_by_default(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI automation for small business",
        [{"title": "Meta, Google, OpenAI staff leave to launch AI startups - CNBC"}],
    )

    assert topic == "Meta, Google, OpenAI staff leave to launch AI startups"


def test_choose_generation_topic_can_keep_static_topic(monkeypatch):
    monkeypatch.setenv("AUTO_POST_DYNAMIC_TOPIC", "false")
    topic = auto_publish.choose_generation_topic(
        "AI automation for small business",
        [{"title": "Meta, Google, OpenAI staff leave to launch AI startups - CNBC"}],
    )

    assert topic == "AI automation for small business"


def test_choose_generation_topic_skips_topics_already_used(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI automation for small business",
        [
            {"title": "Meta, Google, OpenAI staff leave to launch AI startups - CNBC"},
            {"title": "Microsoft expands enterprise AI agents - The Verge"},
        ],
        existing_posts=[{"topic": "Meta, Google, OpenAI staff leave to launch AI startups"}],
    )

    assert topic == "Microsoft expands enterprise AI agents"
