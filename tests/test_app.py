import importlib
import sys

import pytest
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
    assert b"Your blog is ready" in response.data
    assert b"Posts" in response.data


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
