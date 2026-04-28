#!/usr/bin/env python3
import os
import sys
from datetime import date

from main import app, db, BlogPost, Users, fetch_recent_events, generate_article, generate_topic_cover


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


def main() -> int:
    topic = os.environ.get("AUTO_POST_TOPIC", DEFAULT_TOPIC).strip()
    audience = os.environ.get("AUTO_POST_AUDIENCE", DEFAULT_AUDIENCE).strip() or DEFAULT_AUDIENCE
    angle = os.environ.get("AUTO_POST_ANGLE", DEFAULT_ANGLE).strip()
    img_url = os.environ.get("AUTO_POST_IMAGE_URL", "").strip()
    author_email = os.environ.get("AUTO_POST_AUTHOR_EMAIL", "ayncode@gmail.com").strip()
    use_real_events = env_bool("AUTO_POST_USE_REAL_EVENTS", True)
    event_query = os.environ.get("AUTO_POST_EVENT_QUERY", "").strip() or topic
    mode = os.environ.get("AUTO_POST_MODE", "skip").strip().lower()

    if mode not in {"skip", "update"}:
        print("AUTO_POST_MODE must be 'skip' or 'update'", file=sys.stderr)
        return 2

    with app.app_context():
        author = Users.query.filter_by(email=author_email).first()
        if not author:
            print(f"Author not found for AUTO_POST_AUTHOR_EMAIL={author_email}", file=sys.stderr)
            return 3

        dated_title = build_today_title(topic)
        existing_post = BlogPost.query.filter_by(title=dated_title).first()
        if existing_post and mode == "skip":
            print(f"Post already exists for today, skipping: {dated_title}")
            return 0

        events = []
        if use_real_events:
            try:
                events = fetch_recent_events(event_query)
            except Exception as exc:
                print(f"Warning: could not fetch live events: {exc}")

        generated_title, subtitle, body = generate_article(topic, audience, angle, events=events)
        final_title = dated_title
        final_img_url = img_url or generate_topic_cover(topic, audience)
        today_label = date.today().strftime("%B %d, %Y")

        if existing_post and mode == "update":
            existing_post.title = final_title
            existing_post.subtitle = subtitle
            existing_post.body = body
            existing_post.img_url = final_img_url
            existing_post.author = author
            existing_post.date = today_label
            db.session.commit()
            print(f"Updated existing post: {existing_post.id} {final_title}")
            return 0

        post = BlogPost(
            title=final_title,
            subtitle=subtitle,
            body=body,
            img_url=final_img_url,
            author=author,
            date=today_label,
        )
        db.session.add(post)
        db.session.commit()
        print(f"Created post: {post.id} {final_title}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
