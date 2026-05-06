import importlib
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
    assert b"Posts" in response.data


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


def test_generated_cover_wraps_long_titles(client):
    response = client.get(
        "/generated-cover/founders/"
        "microsoft-s-ai-business-hits-37b-as-nadella-bets-on-agentic-computing.svg"
    )

    assert response.status_code == 200
    assert response.data.count(b"<tspan") >= 2
    assert b"font-size=\"52\"" in response.data or b"font-size=\"62\"" in response.data


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
    assert b"Posts" in login_response.data


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
