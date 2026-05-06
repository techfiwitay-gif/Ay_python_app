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


def test_blank_github_action_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AUTO_POST_MODE", "")
    monkeypatch.setenv("AUTO_POST_TOPIC", "")
    monkeypatch.setenv("AUTO_POST_EVENT_HOURS", "")
    monkeypatch.setenv("AUTO_POST_USE_REAL_EVENTS", "")

    assert auto_publish.env_str("AUTO_POST_MODE", "skip") == "skip"
    assert auto_publish.env_str("AUTO_POST_TOPIC", auto_publish.DEFAULT_TOPIC) == auto_publish.DEFAULT_TOPIC
    assert auto_publish.env_int("AUTO_POST_EVENT_HOURS", 24) == 24
    assert auto_publish.env_bool("AUTO_POST_USE_REAL_EVENTS", True) is True
