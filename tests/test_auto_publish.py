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


def test_choose_generation_topic_prefers_product_news_over_stock_lists(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI tech news",
        [
            {"title": "Best AI Stocks to Buy in 2026: 10 Top Picks & How to Invest - The Motley Fool"},
            {"title": "Google, Microsoft, xAI to give US government early access to AI models for security review - The Verge"},
        ],
    )

    assert topic == "Google, Microsoft, xAI to give US government early access to AI models for security review"


def test_choose_generation_topic_penalizes_low_quality_sources(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI tech news",
        [
            {"title": "Artificial Intelligence Software Platform Market to Boom - openPR.com", "source": "openPR.com"},
            {"title": "Alibaba launches new AI cloud tools for enterprise developers - The Verge", "source": "The Verge"},
        ],
    )

    assert topic == "Alibaba launches new AI cloud tools for enterprise developers"


def test_choose_generation_topic_returns_base_topic_when_only_low_fit_events(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI tech news",
        [
            {
                "title": "How China’s AI race is transforming Alibaba’s business model - Latest news from Azerbaijan",
                "source": "Latest news from Azerbaijan",
            },
            {"title": "Microsoft Stock Rises As AI Trade Reignites", "source": "TechStock²"},
        ],
    )

    assert topic == "AI tech news"


def test_blank_github_action_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AUTO_POST_MODE", "")
    monkeypatch.setenv("AUTO_POST_TOPIC", "")
    monkeypatch.setenv("AUTO_POST_EVENT_HOURS", "")
    monkeypatch.setenv("AUTO_POST_USE_REAL_EVENTS", "")

    assert auto_publish.env_str("AUTO_POST_MODE", "skip") == "skip"
    assert auto_publish.env_str("AUTO_POST_TOPIC", auto_publish.DEFAULT_TOPIC) == auto_publish.DEFAULT_TOPIC
    assert auto_publish.env_int("AUTO_POST_EVENT_HOURS", 24) == 24
    assert auto_publish.env_bool("AUTO_POST_USE_REAL_EVENTS", True) is True
