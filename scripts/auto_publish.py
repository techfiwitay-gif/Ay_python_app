#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import (
    CONTENT_POSTS_PATH,
    app,
    enrich_events_with_research,
    fetch_recent_events,
    generate_article,
    generate_topic_cover,
    safe_filename,
)


DEFAULT_TOPIC = "AI automation for everyday business workflows"
DEFAULT_AUDIENCE = "developers"
DEFAULT_ANGLE = "Keep the article tightly tied to a recent tech news topic and focus on practical implications for builders, founders, and operators."
LOW_FIT_SOURCES = (
    "the motley fool",
    "tradingview",
    "invezz",
    "cryptorank",
    "analytics insight",
    "openpr",
    "ad hoc news",
    "latest news from azerbaijan",
)
LOW_FIT_TOPIC_TERMS = (
    "stock",
    "stocks",
    "shares",
    "invest",
    "top picks",
    "analyst",
    "price target",
)
QUALITY_REJECT_PHRASES = (
    "&nbsp;",
    "nbsp",
    "start with the problem",
    "this article focuses on",
    "the article is strongest when it stays close to the sources",
    "the useful part is not the headline by itself, but the specific pattern it points to",
    "deserves attention only where",
    "where attention is shifting",
    "if a company is changing its business model, accelerating ai software demand",
)
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
IMAGE_SEARCH_USER_AGENT = "AyNcodeBot/1.0 (https://ayncode.com)"
IMAGE_REJECT_TERMS = (
    "ai-generated",
    "ai generated",
    "dall-e",
    "midjourney",
    "stable diffusion",
    "bing image creator",
    "screenshot",
    "icon",
    "logo",
    "building92microsoft",
)
IMAGE_ENTITY_HINTS = {
    "alibaba": "Alibaba headquarters",
    "amazon": "Amazon offices",
    "anthropic": "Anthropic artificial intelligence",
    "apple": "Apple Park",
    "chatgpt": "OpenAI artificial intelligence",
    "china": "China technology district",
    "deepmind": "Google DeepMind",
    "google": "Google headquarters",
    "meta": "Meta headquarters",
    "microsoft": "cloud computing data center",
    "nvidia": "Nvidia headquarters",
    "openai": "OpenAI artificial intelligence",
    "robot": "robotics laboratory",
    "robotics": "robotics laboratory",
}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def env_int(name: str, default: int) -> int:
    value = env_str(name, "")
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def build_today_title(topic: str) -> str:
    return f"{topic.strip()} ({date.today().isoformat()})"


def build_post_slug(topic: str) -> str:
    return f"{date.today().isoformat()}-{safe_filename(topic)}"


def clean_event_topic(title: str) -> str:
    cleaned = title.strip()
    cleaned = cleaned.split(" - ")[0].strip()
    return cleaned[:140] or DEFAULT_TOPIC


def topic_relevance_score(topic: str, source: str = "") -> int:
    normalized = topic.casefold()
    normalized_source = source.casefold()
    preferred_terms = (
        "agent",
        "ai model",
        "foundation model",
        "large language model",
        "security",
        "review",
        "copilot",
        "developer",
        "vs code",
        "software",
        "cloud",
        "robot",
        "openai",
        "anthropic",
        "deepmind",
        "google",
        "microsoft",
        "nvidia",
        "xai",
        "perplexity",
        "alibaba",
        "china",
    )
    low_fit_terms = (*LOW_FIT_TOPIC_TERMS, "market size", "market to boom")
    preferred_sources = (
        "reuters",
        "associated press",
        "ap news",
        "the verge",
        "techcrunch",
        "wired",
        "ars technica",
        "mit technology review",
        "venturebeat",
        "the decoder",
        "cnbc",
        "bloomberg",
        "microsoft",
        "google",
        "openai",
        "anthropic",
        "nvidia",
    )
    score = 0
    score += sum(3 for term in preferred_terms if term in normalized)
    score -= sum(5 for term in low_fit_terms if term in normalized)
    score += sum(4 for term in preferred_sources if term in normalized_source)
    score -= sum(4 for term in LOW_FIT_SOURCES if term in normalized_source)
    return score


def is_low_fit_event(topic: str, source: str = "") -> bool:
    normalized = topic.casefold()
    normalized_source = source.casefold()
    return any(term in normalized for term in LOW_FIT_TOPIC_TERMS) or any(
        term in normalized_source for term in LOW_FIT_SOURCES
    )


def candidate_topics_from_events(events: list[dict]) -> list[str]:
    candidates: list[tuple[str, int]] = []
    seen: set[str] = set()
    for event in events:
        topic = clean_event_topic(event.get("title", ""))
        normalized = topic.casefold().strip()
        if not normalized or normalized in seen:
            continue
        if is_low_fit_event(topic, event.get("source", "")):
            continue
        seen.add(normalized)
        candidates.append((topic, topic_relevance_score(topic, event.get("source", ""))))
    return [topic for topic, _score in sorted(candidates, key=lambda item: item[1], reverse=True)]


def choose_generation_topic(topic: str, events: list[dict], existing_posts: list[dict] | None = None) -> str:
    if not env_bool("AUTO_POST_DYNAMIC_TOPIC", True):
        return topic
    if not events:
        return topic

    candidates = candidate_topics_from_events(events)
    if not candidates:
        return topic

    existing_posts = existing_posts or []
    existing_topics = {
        str(post.get("topic", "")).casefold().strip()
        for post in existing_posts
        if isinstance(post, dict) and post.get("topic")
    }

    for candidate in candidates:
        if candidate.casefold().strip() not in existing_topics:
            return candidate

    return candidates[0]


def load_posts(path: Path) -> list[dict]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as content_file:
        posts = json.load(content_file)

    if not isinstance(posts, list):
        raise ValueError(f"{path} must contain a JSON list.")

    return posts


def save_posts(path: Path, posts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as content_file:
        json.dump(posts, content_file, indent=2, ensure_ascii=False)
        content_file.write("\n")


def article_generation_payload(topic: str, audience: str, angle: str, events: list[dict]) -> dict:
    return {
        "topic": topic,
        "audience": audience,
        "angle": angle,
        "events": events,
        "instructions": (
            "Use OpenClaw's Codex 5.4 model to write one publish-ready AyNcode article. "
            "Return JSON only with title, subtitle, body, image_prompt, and image_query. The body must be clean HTML. "
            "Use only the provided event headlines, source names, links, and research notes for current-event claims. "
            "Do not invent facts, numbers, quotes, or events. Include a short source-context section with links. "
            "Also return a strong image_prompt for a matching editorial hero image. "
            "Also return image_query as a short search phrase for a real, relevant public-domain or freely licensed header image. "
            "Target 700 to 1100 words. Write in first person where natural, as if Ayotunde Oyeniyi wrote it. "
            "Focus on what the news means for builders, founders, and operators. Avoid second-person phrasing like 'you should' or 'your team should'. "
            "Prefer 'I think', 'I am watching', 'my read is', and direct analysis."
        ),
    }


def generate_article_with_command(topic: str, audience: str, angle: str, events: list[dict]) -> tuple[str, str, str, str, str]:
    command = os.environ.get("AUTO_POST_GENERATOR_COMMAND", "").strip()
    if not command:
        raise RuntimeError("AUTO_POST_GENERATOR_COMMAND is not set.")

    result = subprocess.run(
        command,
        cwd=app.root_path,
        input=json.dumps(article_generation_payload(topic, audience, angle, events)),
        text=True,
        capture_output=True,
        shell=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    article = json.loads(result.stdout)
    if not article.get("image_prompt"):
        article["image_prompt"] = f"Editorial technology illustration about {topic}, clean modern composition, premium lighting, no text overlays."
    if not article.get("image_query"):
        article["image_query"] = topic
    for field in ("title", "subtitle", "body"):
        if not article.get(field):
            raise RuntimeError(f"Generator response is missing '{field}'.")
    return article["title"], article["subtitle"], article["body"], article["image_prompt"], article["image_query"]


def html_word_count(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    return len(re.findall(r"\b[\w'-]+\b", text))


def article_quality_issues(title: str, subtitle: str, body: str, events: list[dict]) -> list[str]:
    issues: list[str] = []
    combined = f"{title}\n{subtitle}\n{body}"
    normalized = combined.casefold()

    missing_fields = [
        field_name
        for field_name, value in (("title", title), ("subtitle", subtitle), ("body", body))
        if not str(value).strip()
    ]
    if missing_fields:
        issues.append(f"missing required fields: {', '.join(missing_fields)}")

    word_count = html_word_count(body)
    if word_count < 450:
        issues.append(f"article is too short: {word_count} words")

    if body.count("<h2") < 4:
        issues.append("article needs at least four section headings")

    if "source context" not in normalized:
        issues.append("article needs a source-context section")

    if events and "href=" not in body:
        issues.append("article needs source links")

    for phrase in QUALITY_REJECT_PHRASES:
        if phrase in normalized:
            issues.append(f"contains generic/template phrase: {phrase}")

    if re.search(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+Why\b", combined):
        issues.append("contains a source/title extraction artifact ending in 'Why'")

    if re.search(r"\b(The Indian Express|Analytics Insight|The Verge|Reuters|Bloomberg|CNBC)\s+Why\b", combined):
        issues.append("contains a source-name extraction artifact")

    current_event_sources = {
        str(event.get("source", "")).strip().casefold()
        for event in events
        if str(event.get("source", "")).strip()
    }
    if events and current_event_sources:
        mentions_source = any(source and source in normalized for source in current_event_sources)
        if not mentions_source:
            issues.append("article does not mention any current source by name")

    return issues


def validate_article_quality(title: str, subtitle: str, body: str, events: list[dict]) -> None:
    issues = article_quality_issues(title, subtitle, body, events)
    if issues:
        raise RuntimeError("Article failed quality gate: " + "; ".join(issues))


def clean_image_search_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"[\u2018\u2019]", "'", text)
    text = re.sub(r"[\u201c\u201d]", '"', text)
    text = re.sub(r"[^A-Za-z0-9&+.' -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text[:120]


def image_search_queries(topic: str, image_query: str, events: list[dict]) -> list[str]:
    candidates: list[str] = []
    for value in (image_query, topic):
        cleaned = clean_image_search_text(value)
        if cleaned:
            candidates.append(cleaned)

    combined = f"{topic} {image_query}".casefold()
    for term, query in IMAGE_ENTITY_HINTS.items():
        if term in combined:
            candidates.append(query)

    for event in events[:3]:
        cleaned = clean_image_search_text(clean_event_topic(str(event.get("title", ""))))
        if cleaned:
            candidates.append(cleaned)

    candidates.extend([
        "artificial intelligence data center",
        "software engineering office",
        "cloud computing data center",
    ])

    unique_queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_queries.append(candidate)
    return unique_queries


def normalized_image_url(value: str) -> str:
    return str(value or "").split("?", 1)[0].strip().lower()


def used_image_urls_from_posts(posts: list[dict]) -> set[str]:
    return {
        normalized_image_url(post.get("img_url", ""))
        for post in posts
        if isinstance(post, dict) and post.get("img_url")
    }


def commons_metadata_value(image_info: dict, key: str) -> str:
    metadata = image_info.get("extmetadata") or {}
    value = metadata.get(key, {})
    if isinstance(value, dict):
        return re.sub(r"<[^>]+>", "", str(value.get("value", ""))).strip()
    return ""


def commons_candidate_score(page: dict, query: str) -> int:
    image_info = (page.get("imageinfo") or [{}])[0]
    width = int(image_info.get("width") or 0)
    height = int(image_info.get("height") or 0)
    title = str(page.get("title", ""))
    description = commons_metadata_value(image_info, "ImageDescription")
    categories = commons_metadata_value(image_info, "Categories")
    searchable = f"{title} {description} {categories}".casefold()

    if not image_info.get("thumburl") and not image_info.get("url"):
        return -100
    if not str(image_info.get("mime", "")).startswith("image/"):
        return -100
    if width < 700 or height < 350:
        return -100
    if any(term in searchable for term in IMAGE_REJECT_TERMS):
        return -100

    score = 0
    if width >= height:
        score += 15
    if width >= 1200:
        score += 8
    if height >= 675:
        score += 5
    for word in re.findall(r"[a-z0-9]{4,}", query.casefold()):
        if word in searchable:
            score += 4
    if commons_metadata_value(image_info, "LicenseShortName"):
        score += 3
    return score


def find_wikimedia_header_image(query: str, used_image_urls: set[str] | None = None) -> dict[str, str]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": str(env_int("AUTO_POST_IMAGE_SEARCH_LIMIT", 8)),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": "1600",
        "format": "json",
        "formatversion": "2",
    }
    request = Request(
        f"{COMMONS_API_URL}?{urlencode(params)}",
        headers={"User-Agent": IMAGE_SEARCH_USER_AGENT},
    )
    with urlopen(request, timeout=env_int("AUTO_POST_IMAGE_SEARCH_TIMEOUT", 15)) as response:
        payload = json.load(response)

    pages = payload.get("query", {}).get("pages", [])
    scored_pages = [
        (commons_candidate_score(page, query), page)
        for page in pages
        if isinstance(page, dict)
    ]
    scored_pages = [(score, page) for score, page in scored_pages if score >= 0]
    if not scored_pages:
        return {}

    used_image_urls = used_image_urls or set()
    for _score, page in sorted(scored_pages, key=lambda item: item[0], reverse=True):
        image_info = (page.get("imageinfo") or [{}])[0]
        image_url = str(image_info.get("thumburl") or image_info.get("url") or "")
        if normalized_image_url(image_url) in used_image_urls:
            continue
        artist = commons_metadata_value(image_info, "Artist")
        license_name = commons_metadata_value(image_info, "LicenseShortName")
        credit_parts = [part for part in (artist, license_name) if part]
        return {
            "url": image_url,
            "source_url": str(image_info.get("descriptionurl") or ""),
            "credit": " / ".join(credit_parts),
            "query": query,
        }
    return {}


def find_topic_header_image(topic: str, image_query: str, events: list[dict], existing_posts: list[dict] | None = None) -> dict[str, str]:
    if not env_bool("AUTO_POST_USE_IMAGE_SEARCH", True):
        return {}

    used_image_urls = used_image_urls_from_posts(existing_posts or [])
    for query in image_search_queries(topic, image_query, events):
        try:
            image = find_wikimedia_header_image(query, used_image_urls=used_image_urls)
        except Exception as exc:
            print(f"Warning: image search failed for '{query}': {exc}", file=sys.stderr)
            continue
        if image.get("url"):
            print(f"Selected Wikimedia header image for query: {query}")
            return image
    return {}


def generate_article_image(post_slug: str, image_prompt: str) -> str:
    if not env_bool("AUTO_POST_USE_IMAGE_GENERATION", False):
        return ""

    model = os.environ.get("AUTO_POST_IMAGE_MODEL", "comfy/workflow").strip() or "comfy/workflow"
    output_path = Path(app.root_path) / "static" / "generated" / f"{post_slug}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "openclaw", "infer", "image", "generate",
        "--model", model,
        "--prompt", image_prompt,
        "--output", str(output_path),
        "--json",
    ]
    aspect_ratio = os.environ.get("AUTO_POST_IMAGE_ASPECT_RATIO", "16:9").strip()
    if aspect_ratio:
        command.extend(["--aspect-ratio", aspect_ratio])

    result = subprocess.run(command, cwd=app.root_path, text=True, capture_output=True, check=False)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Image generation failed.")
    return f"/static/generated/{post_slug}.png"


def run_git_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=app.root_path, text=True, capture_output=True, check=False)


def git_has_changes(paths: list[Path]) -> bool:
    rel_paths = [str(path.relative_to(app.root_path)) for path in paths if path.exists()]
    result = run_git_command(["git", "status", "--short", "--", *rel_paths])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return bool(result.stdout.strip())


def commit_and_push(paths: list[Path], message: str, push: bool) -> None:
    relative_paths = [str(path.relative_to(app.root_path)) for path in paths if path.exists()]
    commands = [
        ["git", "config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "AyNcode Bot")],
        ["git", "config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "ayncode-bot@example.com")],
        ["git", "add", *relative_paths],
        ["git", "commit", "-m", message],
    ]

    for command in commands:
        result = run_git_command(command)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    if push:
        result = run_git_command(["git", "push"])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a real-event blog post into repo-tracked content.")
    parser.add_argument("--commit", action="store_true", help="Commit the generated content file when it changes.")
    parser.add_argument("--push", action="store_true", help="Push after committing. This also enables --commit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    topic = env_str("AUTO_POST_TOPIC", DEFAULT_TOPIC)
    audience = env_str("AUTO_POST_AUDIENCE", DEFAULT_AUDIENCE)
    angle = env_str("AUTO_POST_ANGLE", DEFAULT_ANGLE)
    img_url = env_str("AUTO_POST_IMAGE_URL", "")
    use_real_events = env_bool("AUTO_POST_USE_REAL_EVENTS", True)
    event_hours = env_int("AUTO_POST_EVENT_HOURS", 24)
    event_limit = env_int("AUTO_POST_EVENT_LIMIT", 12)
    research_limit = env_int("AUTO_POST_RESEARCH_LIMIT", 4)
    event_query = env_str("AUTO_POST_EVENT_QUERY", topic)
    mode = env_str("AUTO_POST_MODE", "skip").lower()
    use_generator_command = env_bool("AUTO_POST_USE_GENERATOR_COMMAND", True)
    require_generator = env_bool("AUTO_POST_REQUIRE_GENERATOR", False)
    enforce_quality = env_bool("AUTO_POST_ENFORCE_QUALITY", True)
    should_commit = args.commit or args.push or env_bool("AUTO_POST_GIT_COMMIT", False)
    should_push = args.push or env_bool("AUTO_POST_GIT_PUSH", False)

    if mode not in {"skip", "update"}:
        print("AUTO_POST_MODE must be 'skip' or 'update'", file=sys.stderr)
        return 2

    try:
        posts = load_posts(CONTENT_POSTS_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Could not load {CONTENT_POSTS_PATH}: {exc}", file=sys.stderr)
        return 3

    events = []
    if use_real_events:
        try:
            events = fetch_recent_events(event_query, limit=event_limit, hours=event_hours)
            if env_bool("AUTO_POST_RESEARCH_EVENTS", True):
                events = enrich_events_with_research(events, limit=research_limit)
        except Exception as exc:
            print(f"Warning: could not fetch live events: {exc}")

    topic_for_generation = choose_generation_topic(topic, events, existing_posts=posts)
    if (
        use_real_events
        and env_bool("AUTO_POST_DYNAMIC_TOPIC", True)
        and env_bool("AUTO_POST_REQUIRE_CREDIBLE_EVENT", True)
        and events
        and topic_for_generation == topic
    ):
        print("No credible live event candidate found, skipping auto publish.")
        return 0

    used_generator = False
    image_prompt = f"Editorial technology illustration about {topic_for_generation}, clean modern composition, premium lighting, no text overlays."
    image_query = topic_for_generation
    if use_generator_command:
        try:
            generated_title, subtitle, body, image_prompt, image_query = generate_article_with_command(topic_for_generation, audience, angle, events)
            print("Generated article with external generator command.")
            used_generator = True
        except Exception as exc:
            if require_generator:
                print(f"Error: external generator failed and AUTO_POST_REQUIRE_GENERATOR=true: {exc}", file=sys.stderr)
                return 5
            print(f"Warning: external generator unavailable, using local template generator: {exc}")
            with app.app_context():
                generated_title, subtitle, body = generate_article(topic_for_generation, audience, angle, events=events)
    else:
        with app.app_context():
            generated_title, subtitle, body = generate_article(topic_for_generation, audience, angle, events=events)

    if enforce_quality:
        try:
            validate_article_quality(generated_title, subtitle, body, events)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print("Skipping auto publish so a rough draft is not committed.", file=sys.stderr)
            return 6

    title_source = generated_title if used_generator else topic_for_generation
    post_slug = build_post_slug(title_source)
    final_title = build_today_title(title_source)
    published_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    searched_image = find_topic_header_image(topic_for_generation, image_query, events, existing_posts=posts) if not img_url else {}
    generated_img_url = ""
    if not img_url and not searched_image.get("url"):
        try:
            generated_img_url = generate_article_image(post_slug, image_prompt)
        except Exception:
            generated_img_url = ""

    new_post = {
        "slug": post_slug,
        "title": final_title,
        "generated_title": generated_title,
        "subtitle": subtitle,
        "date": date.today().strftime("%B %d, %Y"),
        "published_at": published_at,
        "topic": topic_for_generation,
        "audience": audience,
        "event_query": event_query,
        "img_url": img_url or searched_image.get("url") or generated_img_url or generate_topic_cover(topic_for_generation, audience),
        "image_prompt": image_prompt,
        "image_query": image_query,
        "image_source_url": searched_image.get("source_url", ""),
        "image_credit": searched_image.get("credit", ""),
        "body": body,
    }

    existing_index = next(
        (
            index
            for index, post in enumerate(posts)
            if post.get("slug") == post_slug or post.get("title") == final_title
        ),
        None,
    )

    if existing_index is not None and mode == "skip":
        print(f"Post already exists for today, skipping: {final_title}")
        return 0

    if existing_index is None:
        posts.append(new_post)
        action = "Created"
    else:
        posts[existing_index] = {**posts[existing_index], **new_post}
        action = "Updated"

    posts.sort(key=lambda post: post.get("slug") or post.get("date", ""), reverse=True)
    save_posts(CONTENT_POSTS_PATH, posts)
    print(f"{action} repo content post: {final_title}")

    changed_paths = [CONTENT_POSTS_PATH]
    if generated_img_url:
        changed_paths.append(Path(app.root_path) / generated_img_url.lstrip("/"))

    if should_commit:
        try:
            if git_has_changes(changed_paths):
                commit_and_push(
                    changed_paths,
                    f"Auto publish blog post for {date.today().isoformat()}",
                    push=should_push,
                )
                print("Committed generated post content.")
                if should_push:
                    print("Pushed generated post content.")
            else:
                print("No repo content changes to commit.")
        except RuntimeError as exc:
            print(f"Git publish failed: {exc}", file=sys.stderr)
            return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
