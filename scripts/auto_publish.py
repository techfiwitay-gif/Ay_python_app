#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import (
    CONTENT_POSTS_PATH,
    app,
    fetch_recent_events,
    generate_article,
    generate_topic_cover,
    safe_filename,
)


DEFAULT_TOPIC = "AI automation for everyday business workflows"
DEFAULT_AUDIENCE = "developers"
DEFAULT_ANGLE = "Focus on practical, real-world implementation steps, tradeoffs, and useful examples."


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_today_title(topic: str) -> str:
    return f"{topic.strip()} ({date.today().isoformat()})"


def build_post_slug(topic: str) -> str:
    return f"{date.today().isoformat()}-{safe_filename(topic)}"


def clean_event_topic(title: str) -> str:
    cleaned = title.strip()
    cleaned = cleaned.split(" - ")[0].strip()
    return cleaned[:140] or DEFAULT_TOPIC


def candidate_topics_from_events(events: list[dict]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for event in events:
        topic = clean_event_topic(event.get("title", ""))
        normalized = topic.casefold().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(topic)
    return candidates


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


def event_context(events: list[dict]) -> str:
    if not events:
        return "No recent source headlines were found."

    lines = []
    for index, event in enumerate(events, start=1):
        lines.append(
            "\n".join(
                [
                    f"{index}. {event.get('title', '').strip()}",
                    f"   Source: {event.get('source', 'Unknown')}",
                    f"   Published: {event.get('published', 'Unknown')}",
                    f"   URL: {event.get('link', '').strip()}",
                ]
            )
        )
    return "\n".join(lines)


def article_generation_payload(topic: str, audience: str, angle: str, events: list[dict]) -> dict:
    return {
        "topic": topic,
        "audience": audience,
        "angle": angle,
        "events": events,
        "instructions": (
            "Use OpenClaw's Codex 5.4 model to write one publish-ready AyNcode article. "
            "Return JSON only with title, subtitle, and body. The body must be clean HTML. "
            "Use only the provided event headlines for current-event claims. Do not invent facts, "
            "numbers, quotes, or events. Include a short source-context section with links. "
            "Target 700 to 1100 words and make the article practical for software builders."
        ),
    }


def generate_article_with_command(topic: str, audience: str, angle: str, events: list[dict]) -> tuple[str, str, str]:
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
    for field in ("title", "subtitle", "body"):
        if not article.get(field):
            raise RuntimeError(f"Generator response is missing '{field}'.")
    return article["title"], article["subtitle"], article["body"]


def run_git_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=app.root_path, text=True, capture_output=True, check=False)


def git_has_changes(path: Path) -> bool:
    result = run_git_command(["git", "status", "--short", "--", str(path.relative_to(app.root_path))])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return bool(result.stdout.strip())


def commit_and_push(path: Path, message: str, push: bool) -> None:
    relative_path = str(path.relative_to(app.root_path))
    commands = [
        ["git", "config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "AyNcode Bot")],
        ["git", "config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "ayncode-bot@example.com")],
        ["git", "add", relative_path],
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
    topic = os.environ.get("AUTO_POST_TOPIC", DEFAULT_TOPIC).strip()
    audience = os.environ.get("AUTO_POST_AUDIENCE", DEFAULT_AUDIENCE).strip() or DEFAULT_AUDIENCE
    angle = os.environ.get("AUTO_POST_ANGLE", DEFAULT_ANGLE).strip()
    img_url = os.environ.get("AUTO_POST_IMAGE_URL", "").strip()
    use_real_events = env_bool("AUTO_POST_USE_REAL_EVENTS", True)
    event_hours = int(os.environ.get("AUTO_POST_EVENT_HOURS", "24"))
    event_query = os.environ.get("AUTO_POST_EVENT_QUERY", "").strip() or topic
    mode = os.environ.get("AUTO_POST_MODE", "skip").strip().lower()
    use_generator_command = env_bool("AUTO_POST_USE_GENERATOR_COMMAND", True)
    require_generator = env_bool("AUTO_POST_REQUIRE_GENERATOR", False)
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
            events = fetch_recent_events(event_query, hours=event_hours)
        except Exception as exc:
            print(f"Warning: could not fetch live events: {exc}")

    topic_for_generation = choose_generation_topic(topic, events, existing_posts=posts)
    used_generator = False
    if use_generator_command:
        try:
            generated_title, subtitle, body = generate_article_with_command(topic_for_generation, audience, angle, events)
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

    title_source = generated_title if used_generator else topic_for_generation
    post_slug = build_post_slug(title_source)
    final_title = build_today_title(title_source)
    published_at = date.today().strftime("%B %d, %Y")
    from datetime import datetime
    published_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

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
        "img_url": img_url or generate_topic_cover(topic_for_generation, audience),
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

    if should_commit:
        try:
            if git_has_changes(CONTENT_POSTS_PATH):
                commit_and_push(
                    CONTENT_POSTS_PATH,
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
