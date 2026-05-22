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


def test_choose_generation_topic_prefers_diverse_company_after_microsoft_run(monkeypatch):
    monkeypatch.delenv("AUTO_POST_DYNAMIC_TOPIC", raising=False)
    topic = auto_publish.choose_generation_topic(
        "AI tech news",
        [
            {"title": "Microsoft expands enterprise AI agents - The Verge", "source": "The Verge"},
            {"title": "Anthropic acquires developer tool startup Stainless - TechCrunch", "source": "TechCrunch"},
        ],
        existing_posts=[
            {"topic": "Microsoft business software faces UK antitrust probe over bundling, AI lock-in"},
            {"topic": "Microsoft Reportedly Hunts for AI Startups as It Prepares for a Future Beyond OpenAI"},
            {"topic": "Calabrio Workforce Management Earns the Solutions Partner with Certified Software Designation From Microsoft"},
        ],
    )

    assert topic == "Anthropic acquires developer tool startup Stainless"


def test_has_diverse_candidate_detects_microsoft_only_candidates(monkeypatch):
    posts = [
        {"topic": "Microsoft expands AI agents"},
        {"topic": "Azure AI platform update"},
    ]

    assert auto_publish.has_diverse_candidate(["Microsoft launches new AI tools"], posts) is False
    assert auto_publish.has_diverse_candidate(["Anthropic acquires developer tools startup"], posts) is True


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


def test_collect_fallback_events_stops_after_credible_targeted_query(monkeypatch):
    calls = []

    def fake_fetch(query, limit=12, hours=None):
        calls.append((query, limit, hours))
        if query == "OpenAI AI news":
            return [
                {
                    "title": "OpenAI and Dell Technologies partner to bring Codex to enterprise environments - OpenAI",
                    "source": "OpenAI",
                    "link": "https://example.com/openai-dell",
                }
            ]
        return []

    monkeypatch.setattr(auto_publish, "fetch_recent_events", fake_fetch)

    events = auto_publish.collect_fallback_events("AI tech news", limit=12, hours=24)

    assert events[0]["title"].startswith("OpenAI and Dell Technologies")
    assert calls == [("OpenAI AI news", 12, 48)]


def test_events_for_topic_keeps_article_on_selected_story(monkeypatch):
    events = [
        {"title": "Google's AI Products Are Becoming Part of a Larger AGI System - TechTrendsKE", "source": "TechTrendsKE"},
        {"title": "Microsoft Reshapes Workforce As AI Spending Raises New Execution Questions - simplywall.st", "source": "simplywall.st"},
        {"title": "Fortune Adds Sebastian Herrera to Cover AI and Technology Infrastructure - citybiz", "source": "citybiz"},
        {"title": "Google DeepMind updates Gemini robotics controls - Reuters", "source": "Reuters"},
    ]

    focused = auto_publish.events_for_topic(
        "Google's AI Products Are Becoming Part of a Larger AGI System",
        events,
    )

    assert [event["source"] for event in focused] == ["TechTrendsKE"]


def test_blank_github_action_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AUTO_POST_MODE", "")
    monkeypatch.setenv("AUTO_POST_TOPIC", "")
    monkeypatch.setenv("AUTO_POST_EVENT_HOURS", "")
    monkeypatch.setenv("AUTO_POST_USE_REAL_EVENTS", "")

    assert auto_publish.env_str("AUTO_POST_MODE", "skip") == "skip"
    assert auto_publish.env_str("AUTO_POST_TOPIC", auto_publish.DEFAULT_TOPIC) == auto_publish.DEFAULT_TOPIC
    assert auto_publish.env_int("AUTO_POST_EVENT_HOURS", 24) == 24
    assert auto_publish.env_bool("AUTO_POST_USE_REAL_EVENTS", True) is True


def test_article_quality_rejects_generic_source_artifacts():
    body = """
<p>I am reading this through the lead source from The Indian Express. The useful part is not the headline by itself, but the specific pattern it points to around Anthropic, Claude AI, The Indian Express Why.</p>
<h2>What the reporting points to</h2>
<p>Anthropic launches new suite of Claude AI automation tools for small businesses &amp;nbsp;&amp;nbsp; The Indian Express</p>
<h2>What I found in the sources</h2>
<p>I used the source notes below as the factual boundary for this article.</p>
<ul><li><a href="https://example.com">Anthropic launches tools - The Indian Express</a></li></ul>
<h2>Why I think it matters</h2>
<p>For developers, the practical question is what changes if this story keeps developing.</p>
<h2>Final thought</h2>
<p>The article is strongest when it stays close to the sources.</p>
""".strip()

    issues = auto_publish.article_quality_issues(
        "What Anthropic Launches New Suite Signals for Builders",
        "My read based on The Indian Express Why.",
        body,
        [{"source": "The Indian Express"}],
    )

    assert any("generic/template phrase" in issue for issue in issues)
    assert any("artifact" in issue for issue in issues)


def test_article_quality_accepts_specific_sourced_article():
    body = """
<p>I read the Anthropic small-business headline as a workflow story first. If the reporting is accurate, the useful signal is that AI vendors are moving closer to everyday business tasks such as drafting, support, research, operations, handoffs, and internal coordination.</p>
<h2>What the reporting points to</h2>
<p>The lead source is The Indian Express, which describes Anthropic launching Claude automation tools for small businesses. I am keeping the article inside that boundary because the interesting part is not a broad prediction about all AI. It is the narrower product question: how does a model company turn general intelligence into repeatable business workflow?</p>
<h2>Why I think it matters</h2>
<p>Small businesses usually need fewer dropped tasks, faster first drafts, cleaner customer follow-up, and a way to turn scattered information into decisions. That is why this kind of product move matters. The product has to fit into daily work without asking the team to become an AI operations department.</p>
<p>My builder read is simple: the winning version of this product is not the one that promises the most autonomy. It is the one that makes each step visible enough to trust. A useful workflow should show what context it used, what action it proposes, and where a person can approve the result before it touches a customer or a business record.</p>
<p>I would watch whether Anthropic packages the work as named jobs rather than abstract AI capability. Handling an inbound lead, preparing a weekly report, summarizing support themes, and drafting a response from approved company context are clearer than saying a model can help with productivity. Specific jobs create adoption because buyers can picture the before and after.</p>
<h2>Source context</h2>
<p>The source frame for this article is intentionally narrow:</p>
<ul><li><a href="https://example.com">Anthropic launches Claude tools - The Indian Express</a></li></ul>
<p>My takeaway is that the practical AI opportunity for small businesses is not replacing the team. It is turning repeated work into reliable systems that people can still inspect, adjust, and trust before the work reaches customers.</p>
""".strip()

    issues = auto_publish.article_quality_issues(
        "What Anthropic's Claude Automation Push Means for Builders",
        "My read on Anthropic's reported Claude automation tools for small businesses.",
        body,
        [{"source": "The Indian Express"}],
    )

    assert issues == []


def test_image_search_queries_preserve_model_query_before_company_fallback():
    queries = auto_publish.image_search_queries(
        "Microsoft AI bundling probe",
        "software antitrust hearing courtroom",
        [{"title": "Microsoft faces AI bundling probe - Reuters"}],
    )

    assert queries[0] == "software antitrust hearing courtroom"
    assert "cloud computing data center" in queries
    assert queries.index("software antitrust hearing courtroom") < queries.index("cloud computing data center")


def test_find_wikimedia_header_image_skips_urls_already_used(monkeypatch):
    first_url = "https://upload.wikimedia.org/example/microsoft-building.jpg?width=1600"
    second_url = "https://upload.wikimedia.org/example/azure-datacenter.jpg?width=1600"
    payload = {
        "query": {
            "pages": [
                {
                    "title": "File:Microsoft building.jpg",
                    "imageinfo": [
                        {
                            "width": 1600,
                            "height": 900,
                            "mime": "image/jpeg",
                            "thumburl": first_url,
                            "url": first_url,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Microsoft_building.jpg",
                            "extmetadata": {
                                "ImageDescription": {"value": "Microsoft headquarters"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            },
                        }
                    ],
                },
                {
                    "title": "File:Azure datacenter.jpg",
                    "imageinfo": [
                        {
                            "width": 1600,
                            "height": 900,
                            "mime": "image/jpeg",
                            "thumburl": second_url,
                            "url": second_url,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Azure_datacenter.jpg",
                            "extmetadata": {
                                "ImageDescription": {"value": "Azure cloud datacenter"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            },
                        }
                    ],
                },
            ]
        }
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            import json

            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(auto_publish, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    image = auto_publish.find_wikimedia_header_image(
        "Microsoft AI",
        used_image_urls={auto_publish.normalized_image_url(first_url)},
    )

    assert image["url"] == second_url


def test_wikimedia_header_image_rejects_prompt_study_charts(monkeypatch):
    chart_url = "https://upload.wikimedia.org/example/ai-psychology-study.png"
    data_center_url = "https://upload.wikimedia.org/example/data-center.jpg"
    payload = {
        "query": {
            "pages": [
                {
                    "title": "File:Artificial intelligence Psychology study.png",
                    "imageinfo": [
                        {
                            "width": 1600,
                            "height": 900,
                            "mime": "image/png",
                            "thumburl": chart_url,
                            "url": chart_url,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:AI_study.png",
                            "extmetadata": {
                                "ImageDescription": {"value": "Single prompt pair psychological study with bar charts"},
                                "LicenseShortName": {"value": "CC BY 4.0"},
                            },
                        }
                    ],
                },
                {
                    "title": "File:Enterprise data center.jpg",
                    "imageinfo": [
                        {
                            "width": 1600,
                            "height": 900,
                            "mime": "image/jpeg",
                            "thumburl": data_center_url,
                            "url": data_center_url,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Data_center.jpg",
                            "extmetadata": {
                                "ImageDescription": {"value": "Enterprise data center server racks"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            },
                        }
                    ],
                },
            ]
        }
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            import json

            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(auto_publish, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    image = auto_publish.find_wikimedia_header_image("enterprise data center")

    assert image["url"] == data_center_url


def test_openclaw_publish_default_query_is_diverse():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "openclaw_publish.sh").read_text(encoding="utf-8")

    assert "AUTO_POST_TOPIC=\"${AUTO_POST_TOPIC:-AI tech news}\"" in script
    assert "AUTO_POST_AUDIENCE=\"${AUTO_POST_AUDIENCE:-founders}\"" in script
    assert "OpenAI" in script
    assert "Anthropic" in script
    assert "Google DeepMind" in script
    assert "Microsoft AI" in script
    assert "Nvidia" in script
    assert "focus on practical implications for builders, founders, and operators" in script
