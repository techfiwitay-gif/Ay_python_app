import base64
import importlib
import json
import sys

import pytest
import smtplib
from werkzeug.security import generate_password_hash


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("GMAIL_PASSWORD", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("AUTO_POST_GITHUB_TOKEN", raising=False)

    for module_name in ["main", "models"]:
        sys.modules.pop(module_name, None)

    main = importlib.import_module("main")
    main.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with main.app.app_context():
        main.db.drop_all()
        main.db.create_all()

    yield main

    with main.app.app_context():
        main.db.session.remove()
        main.db.drop_all()


@pytest.fixture()
def client(app_module):
    return app_module.app.test_client()


def create_user(main, email="admin@example.com", name="Admin", password="password123"):
    user = main.Users(
        email=email,
        name=name,
        password=generate_password_hash(password, method="pbkdf2:sha256", salt_length=8),
    )
    main.db.session.add(user)
    main.db.session.commit()
    return user


def create_post(main, author, title="Flask Search", subtitle="A useful tutorial", body="<p>Hello Flask search world.</p>"):
    post = main.BlogPost(
        title=title,
        subtitle=subtitle,
        body=body,
        img_url="https://example.com/image.jpg",
        author=author,
        date="April 26, 2026",
        published_at="April 26, 2026 09:15 AM",
    )
    main.db.session.add(post)
    main.db.session.commit()
    return post


def login(client, email="admin@example.com", password="password123"):
    return client.post(
        "/login",
        data={"email": email, "password": password, "login": "Log in"},
        follow_redirects=True,
    )


def test_homepage_shows_empty_state(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"The blog is ready" in response.data
    assert b"Latest writing" in response.data


def test_sync_generated_content_posts_imports_repo_content(client, app_module, monkeypatch, tmp_path):
    content_path = tmp_path / "generated_posts.json"
    content_path.write_text(
        """
[
  {
    "slug": "2026-04-28-ai-news",
    "title": "AI News (2026-04-28)",
    "subtitle": "A useful update.",
    "date": "April 28, 2026",
    "img_url": "/generated-cover/general/ai-news.svg",
    "body": "<p>Recent AI news.</p>"
  }
]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_module, "CONTENT_POSTS_PATH", content_path)

    with app_module.app.app_context():
        imported_count = app_module.sync_generated_content_posts()
        second_import_count = app_module.sync_generated_content_posts()
        post = app_module.BlogPost.query.filter_by(title="AI News (2026-04-28)").first()
        author_email = post.author.email if post else None

    assert imported_count == 1
    assert second_import_count == 0
    assert post is not None
    assert author_email == "ayncode@gmail.com"


def test_subscribe_adds_daily_email_subscriber(client, app_module):
    response = client.post(
        "/subscribe",
        data={"email": "Reader@Example.com", "submit": "Subscribe"},
        follow_redirects=True,
    )

    with app_module.app.app_context():
        subscriber = app_module.Subscriber.query.filter_by(email="reader@example.com").first()

    assert response.status_code == 200
    assert subscriber is not None
    assert subscriber.is_active is True
    assert b"subscribed to daily AyNcode articles" in response.data


def test_subscribe_reactivates_existing_subscriber(client, app_module):
    with app_module.app.app_context():
        app_module.db.session.add(
            app_module.Subscriber(
                email="reader@example.com",
                created_at="2026-05-25T09:00:00+00:00",
                is_active=False,
            )
        )
        app_module.db.session.commit()

    response = client.post(
        "/subscribe",
        data={"email": "reader@example.com", "submit": "Subscribe"},
        follow_redirects=True,
    )

    with app_module.app.app_context():
        subscribers = app_module.Subscriber.query.filter_by(email="reader@example.com").all()

    assert response.status_code == 200
    assert len(subscribers) == 1
    assert subscribers[0].is_active is True


def test_new_post_email_notification_is_sent_once(client, app_module, monkeypatch):
    sent_messages = []

    def fake_send_site_email(recipient, subject, text_body, html_body=None):
        sent_messages.append((recipient, subject, text_body, html_body))
        return True

    monkeypatch.setattr(app_module, "send_site_email", fake_send_site_email)

    with app_module.app.app_context():
        author = create_user(app_module)
        post = create_post(app_module, author, title="Daily AI Brief")
        app_module.db.session.add(
            app_module.Subscriber(
                email="reader@example.com",
                created_at="2026-05-25T09:00:00+00:00",
            )
        )
        app_module.db.session.commit()

        first_count = app_module.notify_subscribers_for_new_post(post)
        second_count = app_module.notify_subscribers_for_new_post(post)
        delivery = app_module.PostEmailDelivery.query.filter_by(post_id=post.id).first()

    assert first_count == 1
    assert second_count == 0
    assert delivery is not None
    assert delivery.recipient_count == 1
    assert sent_messages[0][0] == "reader@example.com"
    assert "Daily AI Brief" in sent_messages[0][1]
    assert "https://www.ayncode.com/post/" in sent_messages[0][2]


def test_unsubscribe_deactivates_subscriber(client, app_module):
    with app_module.app.app_context():
        app_module.db.session.add(
            app_module.Subscriber(
                email="reader@example.com",
                created_at="2026-05-25T09:00:00+00:00",
            )
        )
        app_module.db.session.commit()
        token = app_module.generate_subscription_token("reader@example.com")

    response = client.get(f"/unsubscribe/{token}", follow_redirects=True)

    with app_module.app.app_context():
        subscriber = app_module.Subscriber.query.filter_by(email="reader@example.com").first()

    assert response.status_code == 200
    assert subscriber.is_active is False
    assert b"unsubscribed from daily AyNcode article emails" in response.data


def test_deleted_generated_post_is_not_reimported(client, app_module, monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    content_path = tmp_path / "generated_posts.json"
    content_path.write_text(
        """
[
  {
    "slug": "2026-04-28-ai-news",
    "title": "AI News (2026-04-28)",
    "subtitle": "A useful update.",
    "date": "April 28, 2026",
    "img_url": "/generated-cover/general/ai-news.svg",
    "body": "<p>Recent AI news.</p>"
  }
]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONTENT_POSTS_PATH", content_path)

    with app_module.app.app_context():
        create_user(app_module, email="admin@example.com", name="Admin")
        app_module.sync_generated_content_posts()
        post = app_module.BlogPost.query.filter_by(title="AI News (2026-04-28)").first()
        post_id = post.id

    client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password123", "login": "Log in"},
        follow_redirects=True,
    )
    response = client.get(f"/delete/{post_id}", follow_redirects=True)

    with app_module.app.app_context():
        app_module.sync_generated_content_posts()
        restored_post = app_module.BlogPost.query.filter_by(title="AI News (2026-04-28)").first()
        deleted_marker = app_module.DeletedGeneratedPost.query.filter_by(title="AI News (2026-04-28)").first()
        content_posts = app_module.load_generated_content_posts()

    assert response.status_code == 200
    assert restored_post is None
    assert deleted_marker is not None
    assert content_posts == []


def test_remove_generated_post_from_github_commits_filtered_content(app_module, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "techfiwitay-gif/Ay_python_app")
    monkeypatch.setenv("GITHUB_BRANCH", "main")
    posts = [
        {"slug": "keep-me", "title": "Keep Me"},
        {"slug": "delete-me", "title": "Delete Me"},
    ]
    get_payload = {
        "sha": "abc123",
        "content": base64.b64encode(json.dumps(posts).encode("utf-8")).decode("ascii"),
    }
    put_payloads = []

    class FakeResponse:
        def __init__(self, payload=None):
            self.payload = payload or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return FakeResponse(get_payload)
        put_payloads.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr(app_module, "urlopen", fake_urlopen)

    removed = app_module.remove_generated_post_from_github("delete-me", "Delete Me")

    assert removed is True
    assert put_payloads
    updated_posts = json.loads(base64.b64decode(put_payloads[0]["content"]).decode("utf-8"))
    assert updated_posts == [{"slug": "keep-me", "title": "Keep Me"}]
    assert put_payloads[0]["branch"] == "main"


def test_generated_cover_wraps_long_titles(client):
    response = client.get(
        "/generated-cover/founders/"
        "microsoft-s-ai-business-hits-37b-as-nadella-bets-on-agentic-computing.svg"
    )

    assert response.status_code == 200
    assert response.data.count(b"<tspan") >= 2
    assert b"font-size=\"52\"" in response.data or b"font-size=\"62\"" in response.data


def test_fallback_article_is_topic_specific(client, app_module):
    with app_module.app.app_context():
        _title, subtitle, body = app_module.generate_article(
            "Best AI Stocks to Buy in 2026: 10 Top Picks & How to Invest",
            "founders",
            "Focus on practical, real-world implementation steps, tradeoffs, and useful examples.",
            events=[
                {
                    "title": "Best AI Stocks to Buy in 2026: 10 Top Picks & How to Invest - The Motley Fool",
                    "link": "https://example.com/ai-stocks",
                    "published": "Thu, 07 May 2026 15:14:00 GMT",
                    "source": "The Motley Fool",
                }
            ],
        )

    assert "stock recommendation" in body
    assert "Source context" in body
    assert "This article focuses on Focus" not in body
    assert "Start with the problem" not in body
    assert "workflow instead of a vague idea" not in body
    assert "capital are moving" in subtitle


def test_researched_article_uses_source_specific_context(client, app_module):
    with app_module.app.app_context():
        _title, subtitle, body = app_module.generate_article(
            "How China’s AI race is transforming Alibaba’s business model",
            "founders",
            "",
            events=[
                {
                    "title": "How China’s AI race is transforming Alibaba’s business model - Example News",
                    "link": "https://example.com/alibaba-ai",
                    "published": "Thu, 14 May 2026 06:55:11 GMT",
                    "source": "Example News",
                    "research": (
                        "Alibaba is changing how it sells cloud services and AI products as China’s AI competition intensifies. "
                        "The report says the company is tying model capability, enterprise demand, and commerce infrastructure more tightly together."
                    ),
                }
            ],
        )

    assert "Alibaba" in body
    assert "China" in body
    assert "What I found in the sources" in body
    assert "AI trust is becoming product infrastructure" not in body
    assert "latest AI oversight headline" not in subtitle


def test_homepage_search_filters_posts(client, app_module):
    with app_module.app.app_context():
        author = create_user(app_module)
        create_post(app_module, author, title="Flask Search")
        create_post(app_module, author, title="Deployment Notes", body="<p>Shipping notes.</p>")

    response = client.get("/?q=search")

    assert response.status_code == 200
    assert b"Search results for" in response.data
    assert b"Flask Search" in response.data
    assert b"Deployment Notes" not in response.data
    assert b"min read" in response.data
    assert b"comments" in response.data


def test_duplicate_registration_redirects_to_login(client, app_module):
    with app_module.app.app_context():
        create_user(app_module, email="taken@example.com")

    response = client.post(
        "/register",
        data={
            "email": "taken@example.com",
            "password": "password123",
            "name": "Taken",
            "sign_up": "sign me up",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Email already exist.Please login" in response.data


def test_forgot_password_without_smtp_credentials_does_not_crash(client, app_module):
    with app_module.app.app_context():
        create_user(app_module, email="reset@example.com")

    response = client.post(
        "/forgot-password",
        data={"email": "reset@example.com", "submit": "Send Reset Link"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Password reset email is temporarily unavailable" in response.data


def test_forgot_password_with_bad_smtp_credentials_does_not_crash(client, app_module, monkeypatch):
    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *_args):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    with app_module.app.app_context():
        create_user(app_module, email="reset@example.com")

    monkeypatch.setenv("GMAIL_PASSWORD", "bad-password")
    monkeypatch.setattr(app_module, "SMTP", FailingSMTP)

    response = client.post(
        "/forgot-password",
        data={"email": "reset@example.com", "submit": "Send Reset Link"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Password reset email is temporarily unavailable" in response.data


def test_reset_password_updates_password(client, app_module):
    with app_module.app.app_context():
        create_user(app_module, email="reset@example.com", password="oldpassword123")
        user = app_module.Users.query.filter_by(email="reset@example.com").first()
        token = app_module.generate_password_reset_token(user)

    response = client.post(
        f"/reset-password/{token}",
        data={
            "password": "newpassword123",
            "confirm_password": "newpassword123",
            "submit": "Update Password",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Password updated" in response.data

    login_response = client.post(
        "/login",
        data={"email": "reset@example.com", "password": "newpassword123", "login": "Log in"},
        follow_redirects=True,
    )

    assert login_response.status_code == 200
    assert b"Latest writing" in login_response.data


def test_logged_in_user_can_comment(client, app_module):
    with app_module.app.app_context():
        author = create_user(app_module)
        post = create_post(app_module, author)
        post_id = post.id

    login(client)
    response = client.post(
        f"/post/{post_id}",
        data={"body": "Nice write up", "submit": "Submit Comment"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Nice write up" in response.data


def test_post_view_increments_view_count(client, app_module):
    with app_module.app.app_context():
        author = create_user(app_module)
        post = create_post(app_module, author)
        post_id = post.id

    response = client.get(f"/post/{post_id}")

    assert response.status_code == 200
    assert b"1 views" in response.data
    assert b"09:15 AM" in response.data


def test_post_reactions_increment_counts(client, app_module):
    with app_module.app.app_context():
        author = create_user(app_module)
        post = create_post(app_module, author)
        post_id = post.id

    like_response = client.post(f"/post/{post_id}/react/like", follow_redirects=True)
    upvote_response = client.post(f"/post/{post_id}/react/upvote", follow_redirects=True)
    downvote_response = client.post(f"/post/{post_id}/react/downvote", follow_redirects=True)

    assert like_response.status_code == 200
    assert upvote_response.status_code == 200
    assert downvote_response.status_code == 200
    assert b"1</strong>" in downvote_response.data
    assert b"Likes" in downvote_response.data
    assert b"Upvotes" in downvote_response.data
    assert b"Downvotes" in downvote_response.data


def test_admin_routes_are_protected(client, app_module):
    with app_module.app.app_context():
        create_user(app_module, email="admin@example.com", name="Admin")
        create_user(app_module, email="reader@example.com", name="Reader")

    client.post(
        "/login",
        data={"email": "reader@example.com", "password": "password123", "login": "Log in"},
        follow_redirects=True,
    )
    response = client.get("/new-post")

    assert response.status_code == 403


def test_configured_admin_email_can_access_admin_routes(client, app_module, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")

    with app_module.app.app_context():
        create_user(app_module, email="reader@example.com", name="Reader")
        admin = create_user(app_module, email="admin@example.com", name="Admin")
        admin_id = admin.id

    assert admin_id != 1

    client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password123", "login": "Log in"},
        follow_redirects=True,
    )
    response = client.get("/new-post")

    assert response.status_code == 200
    assert b"Blog Post Title" in response.data


def test_admin_sees_delete_button_on_post_page(client, app_module, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")

    with app_module.app.app_context():
        admin = create_user(app_module, email="admin@example.com", name="Admin")
        post = create_post(app_module, admin)
        post_id = post.id

    client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password123", "login": "Log in"},
        follow_redirects=True,
    )
    response = client.get(f"/post/{post_id}")

    assert response.status_code == 200
    assert b"Delete post" in response.data
    assert f"/delete/{post_id}".encode() in response.data


def test_ensure_admin_user_repairs_existing_automation_account(app_module, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "ayncode@gmail.com")
    monkeypatch.setenv("ADMIN_NAME", "Ayotunde Oyeniyi")
    monkeypatch.setenv("ADMIN_PASSWORD", "new-admin-password")

    with app_module.app.app_context():
        create_user(app_module, email="ayncode@gmail.com", name="Old Bot", password="old-password")
        app_module.ensure_admin_user()
        user = app_module.Users.query.filter_by(email="ayncode@gmail.com").first()

    assert user.name == "Ayotunde Oyeniyi"
    assert app_module.check_password_hash(user.password, "new-admin-password")


def test_contact_without_smtp_credentials_does_not_crash(client):
    response = client.post(
        "/contact",
        data={
            "name": "Visitor",
            "email": "visitor@example.com",
            "phone": "555-1212",
            "message": "Hello",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Contact service is temporarily unavailable" in response.data


def test_contact_with_bad_smtp_credentials_does_not_crash(client, app_module, monkeypatch):
    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *_args):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setenv("GMAIL_EMAIL", "ayncode@gmail.com")
    monkeypatch.setenv("GMAIL_PASSWORD", "bad-password")
    monkeypatch.setattr(app_module, "SMTP", FailingSMTP)

    response = client.post(
        "/contact",
        data={
            "name": "Visitor",
            "email": "visitor@example.com",
            "phone": "555-1212",
            "message": "Hello",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Contact service is temporarily unavailable" in response.data
