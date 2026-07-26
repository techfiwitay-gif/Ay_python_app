
from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, LoginManager, login_required, current_user, logout_user
from forms import AdminUserActionForm, CommentForm, CreatePostForm, DeleteAccountForm, ForgotPasswordForm, GenerateArticleForm, LoginForm, LogoutForm, RegisterForm, ResendVerificationForm, ResetPasswordForm
from functools import wraps
from models import BlogPost, Comment, DeletedGeneratedPost, LoginThrottle, Users, db
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import IntegrityError
import base64
import hmac
import os
import json
import re
import secrets
from pathlib import Path
from smtplib import SMTP, SMTPException
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

app = Flask(__name__, static_url_path='/static')
default_database_url = "sqlite:////tmp/ayblog.db" if os.environ.get("VERCEL") else "sqlite:///ayblog.db"
database_url = os.environ.get("DATABASE_URL") or default_database_url
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config.from_mapping(
    SECRET_KEY=os.environ.get("SECRET_KEY") or "dev-secret-key",
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    PASSWORD_RESET_MAX_AGE=int(os.environ.get("PASSWORD_RESET_MAX_AGE", "3600")),
    EMAIL_VERIFICATION_MAX_AGE=int(os.environ.get("EMAIL_VERIFICATION_MAX_AGE", "86400")),
    SESSION_COOKIE_SECURE=os.environ.get(
        "SESSION_COOKIE_SECURE",
        "1" if os.environ.get("VERCEL") else "0",
    ) == "1",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
ckeditor = CKEditor(app)
Bootstrap(app)
db.init_app(app)


import hashlib

CONTENT_POSTS_PATH = Path(app.root_path) / "content" / "generated_posts.json"
DEFAULT_AUTOMATION_AUTHOR_EMAIL = "ayncode@gmail.com"
DEFAULT_AUTOMATION_AUTHOR_NAME = "Ayotunde Oyeniyi"
DEFAULT_ADMIN_EMAIL = DEFAULT_AUTOMATION_AUTHOR_EMAIL
DEFAULT_GITHUB_REPOSITORY = "techfiwitay-gif/Ay_python_app"
PASSWORD_RESET_SALT = "ayncoder-password-reset"
EMAIL_VERIFICATION_SALT = "ayncoder-email-verification"
ARTICLE_ARCHIVE_AGE_DAYS = 7
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK_TIME = timedelta(minutes=15)
LOGIN_EMAIL_LIMIT = 5
LOGIN_IP_LIMIT = 30
COMMENT_COOLDOWN = timedelta(seconds=10)
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def ensure_engagement_columns():
    inspector = inspect(db.engine)
    if not inspector.has_table("blog_posts"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("blog_posts")}
    engagement_columns = {
        "views": "INTEGER NOT NULL DEFAULT 0",
        "likes": "INTEGER NOT NULL DEFAULT 0",
        "upvotes": "INTEGER NOT NULL DEFAULT 0",
        "downvotes": "INTEGER NOT NULL DEFAULT 0",
        "published_at": "VARCHAR(250) NOT NULL DEFAULT ''",
    }

    dialect = db.engine.dialect.name
    with db.engine.begin() as connection:
        for column_name, column_definition in engagement_columns.items():
            if column_name in existing_columns:
                continue
            if dialect == "postgresql":
                connection.execute(
                    text(f"ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
                )
            else:
                connection.execute(text(f"ALTER TABLE blog_posts ADD COLUMN {column_name} {column_definition}"))


def ensure_user_security_columns():
    inspector = inspect(db.engine)
    if not inspector.has_table("users"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        security_columns = {
            "role": "VARCHAR(32) NOT NULL DEFAULT 'user'",
            "email_verified": "BOOLEAN NOT NULL DEFAULT TRUE",
            "email_verification_nonce": "VARCHAR(64)",
            "password_reset_nonce": "VARCHAR(64)",
            "is_disabled": "BOOLEAN NOT NULL DEFAULT FALSE",
            "created_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        }
    else:
        security_columns = {
            "role": "VARCHAR(32) NOT NULL DEFAULT 'user'",
            "email_verified": "BOOLEAN NOT NULL DEFAULT 1",
            "email_verification_nonce": "VARCHAR(64)",
            "password_reset_nonce": "VARCHAR(64)",
            "is_disabled": "BOOLEAN NOT NULL DEFAULT 0",
            "created_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        }

    with db.engine.begin() as connection:
        for column_name, column_definition in security_columns.items():
            if column_name in existing_columns:
                continue
            if dialect == "postgresql":
                connection.execute(
                    text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
                )
            else:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_definition}"))


def ensure_comment_columns():
    inspector = inspect(db.engine)
    if not inspector.has_table("comments"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("comments")}
    dialect = db.engine.dialect.name
    comment_columns = {
        "parent_id": "INTEGER",
        "created_at": (
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            if dialect == "postgresql"
            else "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ),
    }
    with db.engine.begin() as connection:
        for column_name, column_definition in comment_columns.items():
            if column_name in existing_columns:
                continue
            if dialect == "postgresql":
                connection.execute(
                    text(f"ALTER TABLE comments ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
                )
            else:
                connection.execute(text(f"ALTER TABLE comments ADD COLUMN {column_name} {column_definition}"))


def load_generated_content_posts():
    if not CONTENT_POSTS_PATH.exists():
        return []

    try:
        with CONTENT_POSTS_PATH.open("r", encoding="utf-8") as content_file:
            posts = json.load(content_file)
    except (OSError, json.JSONDecodeError):
        app.logger.warning("Could not load generated content posts from %s", CONTENT_POSTS_PATH)
        return []

    if not isinstance(posts, list):
        app.logger.warning("Generated content posts file must contain a JSON list.")
        return []

    return [post for post in posts if isinstance(post, dict)]


def generated_post_matches(post_data, slug, title):
    return (slug and post_data.get("slug") == slug) or post_data.get("title") == title


def github_repo_name():
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("AUTO_POST_GITHUB_REPOSITORY")
    if repo:
        return repo

    owner = os.environ.get("VERCEL_GIT_REPO_OWNER")
    repo_name = os.environ.get("VERCEL_GIT_REPO_SLUG")
    if owner and repo_name:
        return f"{owner}/{repo_name}"

    return DEFAULT_GITHUB_REPOSITORY


def github_write_token():
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("AUTO_POST_GITHUB_TOKEN")
    )


def remove_generated_post_from_local_content(slug, title):
    posts = load_generated_content_posts()
    if not posts:
        return False

    filtered_posts = [post for post in posts if not generated_post_matches(post, slug, title)]
    if len(filtered_posts) == len(posts):
        return False

    try:
        CONTENT_POSTS_PATH.write_text(
            json.dumps(filtered_posts, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        app.logger.warning("Could not update generated content file: %s", exc)
        return False

    return True


def remove_generated_post_from_github(slug, title):
    token = github_write_token()
    repo = github_repo_name()
    if not token or not repo:
        return False

    branch = (
        os.environ.get("GITHUB_BRANCH")
        or os.environ.get("VERCEL_GIT_COMMIT_REF")
        or "main"
    )
    repo_path = os.environ.get("GENERATED_POSTS_REPO_PATH", "content/generated_posts.json")
    encoded_path = quote(repo_path, safe="/")
    api_url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={quote(branch)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "ayncode-app",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        get_request = Request(api_url, headers=headers)
        with urlopen(get_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        current_content = base64.b64decode(payload["content"]).decode("utf-8")
        posts = json.loads(current_content)
        if not isinstance(posts, list):
            return False

        filtered_posts = [post for post in posts if not generated_post_matches(post, slug, title)]
        if len(filtered_posts) == len(posts):
            return False

        updated_content = json.dumps(filtered_posts, indent=2, ensure_ascii=False) + "\n"
        put_payload = json.dumps(
            {
                "message": f"Delete generated post: {title[:120]}",
                "content": base64.b64encode(updated_content.encode("utf-8")).decode("ascii"),
                "sha": payload["sha"],
                "branch": branch,
            }
        ).encode("utf-8")
        put_request = Request(
            api_url.split("?", 1)[0],
            data=put_payload,
            headers={**headers, "Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(put_request, timeout=20):
            return True
    except (HTTPError, URLError, OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
        app.logger.warning("Could not delete generated post from GitHub: %s", exc)
        return False


def remove_generated_post_from_source(slug, title):
    removed_locally = remove_generated_post_from_local_content(slug, title)
    removed_remotely = remove_generated_post_from_github(slug, title)
    return removed_locally or removed_remotely


def normalize_email(value):
    return (value or "").strip().lower()


def get_or_create_automation_author():
    email = normalize_email(os.environ.get("AUTO_POST_AUTHOR_EMAIL") or DEFAULT_AUTOMATION_AUTHOR_EMAIL)
    name = (os.environ.get("AUTO_POST_AUTHOR_NAME") or DEFAULT_AUTOMATION_AUTHOR_NAME).strip()
    author = Users.query.filter(func.lower(Users.email) == email).first()
    if author:
        return author

    password_seed = os.environ.get("AUTO_POST_AUTHOR_PASSWORD", os.urandom(24).hex())
    author = Users(
        email=email,
        name=name or DEFAULT_AUTOMATION_AUTHOR_NAME,
        password=generate_password_hash(password_seed),
        email_verified=True,
    )
    db.session.add(author)
    db.session.commit()
    return author


def configured_admin_email():
    return normalize_email(os.environ.get("ADMIN_EMAIL") or os.environ.get("AUTO_POST_AUTHOR_EMAIL") or DEFAULT_ADMIN_EMAIL)


def configured_admin_name():
    return (os.environ.get("ADMIN_NAME") or DEFAULT_AUTOMATION_AUTHOR_NAME).strip()


def is_admin_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", "user") == "admin" and not getattr(user, "is_disabled", False)


def ensure_admin_user():
    password = (os.environ.get("ADMIN_PASSWORD") or os.environ.get("AUTO_POST_AUTHOR_PASSWORD") or "").strip()
    if not password:
        return None

    email = configured_admin_email()
    if not email:
        return None

    name = configured_admin_name()
    user = Users.query.filter(func.lower(Users.email) == email).first()
    password_hash = generate_password_hash(password)
    if not user:
        user = Users(
            email=email,
            name=name,
            password=password_hash,
            role="admin",
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    changed = False
    if user.name != name:
        user.name = name
        changed = True
    if not check_password_hash(user.password, password):
        user.password = password_hash
        changed = True
    if user.role != "admin":
        user.role = "admin"
        changed = True
    if not user.email_verified:
        user.email_verified = True
        changed = True
    if user.is_disabled:
        user.is_disabled = False
        changed = True
    if changed:
        db.session.commit()
    return user


def ensure_admin_role():
    email = configured_admin_email()
    if not email:
        return None
    user = Users.query.filter(func.lower(Users.email) == email).first()
    if not user:
        return None
    changed = False
    if user.role != "admin":
        user.role = "admin"
        changed = True
    if not user.email_verified:
        user.email_verified = True
        changed = True
    if user.is_disabled:
        user.is_disabled = False
        changed = True
    if changed:
        db.session.commit()
    return user


def generated_post_key_for_title(title):
    for post_data in load_generated_content_posts():
        if post_data.get("title") == title:
            return post_data.get("slug", ""), post_data.get("title", title)
    return "", title


def remember_deleted_generated_post(post):
    slug, title = generated_post_key_for_title(post.title)
    if DeletedGeneratedPost.query.filter_by(title=title).first():
        return

    db.session.add(
        DeletedGeneratedPost(
            title=title,
            slug=slug,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
    )


def sync_generated_content_posts():
    posts = load_generated_content_posts()
    if not posts:
        return 0

    author = get_or_create_automation_author()
    imported_count = 0
    required_fields = {"title", "subtitle", "body", "img_url", "date"}
    deleted_posts = DeletedGeneratedPost.query.all()
    deleted_titles = {post.title for post in deleted_posts}
    deleted_slugs = {post.slug for post in deleted_posts if post.slug}

    for post_data in posts:
        if not required_fields.issubset(post_data):
            app.logger.warning("Skipping generated post with missing fields: %s", post_data.get("title", "Untitled"))
            continue
        if post_data.get("title") in deleted_titles or post_data.get("slug") in deleted_slugs:
            continue
        image_url = str(post_data.get("img_url") or "").strip()
        if not image_url or "/generated-cover/" in image_url:
            app.logger.warning("Skipping generated post without a real image: %s", post_data.get("title", "Untitled"))
            continue
        existing_post = BlogPost.query.filter_by(title=post_data["title"]).first()
        if existing_post:
            updates = {
                "subtitle": post_data["subtitle"],
                "body": post_data["body"],
                "img_url": post_data["img_url"],
                "date": post_data["date"],
                "published_at": post_data.get("published_at", post_data["date"]),
            }
            changed = False
            for field, value in updates.items():
                if getattr(existing_post, field) != value:
                    setattr(existing_post, field, value)
                    changed = True
            if changed:
                imported_count += 1
            continue

        db.session.add(
            BlogPost(
                title=post_data["title"],
                subtitle=post_data["subtitle"],
                body=post_data["body"],
                img_url=post_data["img_url"],
                author=author,
                date=post_data["date"],
                published_at=post_data.get("published_at", post_data["date"]),
            )
        )
        imported_count += 1

    if imported_count:
        db.session.commit()

    return imported_count


with app.app_context():
    db.create_all()
    ensure_user_security_columns()
    ensure_comment_columns()
    ensure_engagement_columns()
    ensure_admin_user()
    sync_generated_content_posts()
    ensure_admin_role()


def is_safe_redirect_url(target):
    return bool(target) and target.startswith("/") and not target.startswith("//")


def password_reset_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])


def generate_password_reset_token(user):
    user.password_reset_nonce = secrets.token_urlsafe(24)
    db.session.commit()
    return password_reset_serializer().dumps(
        {"user_id": user.id, "nonce": user.password_reset_nonce},
        salt=PASSWORD_RESET_SALT,
    )


def verify_password_reset_token(token):
    try:
        payload = password_reset_serializer().loads(
            token,
            salt=PASSWORD_RESET_SALT,
            max_age=app.config["PASSWORD_RESET_MAX_AGE"],
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user = db.session.get(Users, payload.get("user_id"))
    if (
        not user
        or user.is_disabled
        or not user.password_reset_nonce
        or not hmac.compare_digest(user.password_reset_nonce, payload.get("nonce", ""))
    ):
        return None
    return user


def generate_email_verification_token(user):
    user.email_verification_nonce = secrets.token_urlsafe(24)
    db.session.commit()
    return password_reset_serializer().dumps(
        {"user_id": user.id, "nonce": user.email_verification_nonce},
        salt=EMAIL_VERIFICATION_SALT,
    )


def verify_email_verification_token(token):
    try:
        payload = password_reset_serializer().loads(
            token,
            salt=EMAIL_VERIFICATION_SALT,
            max_age=app.config["EMAIL_VERIFICATION_MAX_AGE"],
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user = db.session.get(Users, payload.get("user_id"))
    if (
        not user
        or user.is_disabled
        or user.email_verified
        or not user.email_verification_nonce
        or not hmac.compare_digest(user.email_verification_nonce, payload.get("nonce", ""))
    ):
        return None
    return user


def send_password_reset_email(user, reset_url):
    password = (os.environ.get("GMAIL_PASSWORD") or "").replace(" ", "").strip()
    my_email = (
        os.environ.get("GMAIL_EMAIL")
        or os.environ.get("SMTP_USERNAME")
        or os.environ.get("CONTACT_EMAIL")
        or DEFAULT_ADMIN_EMAIL
    ).strip()
    if not password:
        return False

    message = (
        "Subject:Reset your AyNcode password\n\n"
        f"Hi {user.name},\n\n"
        "I received a request to reset the password for this AyNcode account.\n\n"
        f"Reset password: {reset_url}\n\n"
        "This link expires in one hour. If this was not requested, this email can be ignored.\n"
    )

    try:
        with SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(my_email, password)
            smtp.sendmail(my_email, user.email, msg=message)
    except (OSError, SMTPException) as exc:
        app.logger.warning("Password reset email failed: %s", exc)
        return False
    return True


def send_email_verification(user):
    password = (os.environ.get("GMAIL_PASSWORD") or "").replace(" ", "").strip()
    my_email = (
        os.environ.get("GMAIL_EMAIL")
        or os.environ.get("SMTP_USERNAME")
        or os.environ.get("CONTACT_EMAIL")
        or DEFAULT_ADMIN_EMAIL
    ).strip()
    if not password:
        return False

    token = generate_email_verification_token(user)
    verify_url = url_for("verify_email", token=token, _external=True)
    message = (
        "Subject:Verify your AyNcode email\n\n"
        f"Hi {user.name},\n\n"
        "Confirm this email address to activate your AyNcode account.\n\n"
        f"Verify email: {verify_url}\n\n"
        "This link expires in 24 hours. If this account was not created by you, this email can be ignored.\n"
    )
    try:
        with SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(my_email, password)
            smtp.sendmail(my_email, user.email, msg=message)
    except (OSError, SMTPException) as exc:
        app.logger.warning("Email verification failed: %s", exc)
        return False
    return True


def text_word_count(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return len(re.findall(r"\b\w+\b", text))


def reading_time_minutes(html):
    return max(1, round(text_word_count(html) / 220))


def decorate_posts(posts):
    for post in posts:
        post.word_count = text_word_count(post.body)
        post.reading_time = reading_time_minutes(post.body)
        post.comment_count = len(post.comments)
    return posts


def parse_post_timestamp(post):
    for value in (getattr(post, "published_at", ""), getattr(post, "date", "")):
        if not value:
            continue
        for date_format in ("%B %d, %Y %I:%M %p", "%B %d, %Y"):
            try:
                return datetime.strptime(value, date_format)
            except ValueError:
                continue
    return datetime.min


def sort_posts_latest_first(posts):
    return sorted(posts, key=lambda post: (parse_post_timestamp(post), post.id or 0), reverse=True)


def title_case_topic(topic):
    small_words = {"a", "an", "and", "as", "at", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    words = re.findall(r"[A-Za-z0-9']+|[^A-Za-z0-9']+", topic.strip())
    titled = []
    word_index = 0
    for part in words:
        if re.match(r"[A-Za-z0-9']+", part):
            lower = part.lower()
            if word_index > 0 and lower in small_words:
                titled.append(lower)
            else:
                titled.append(part[:1].upper() + part[1:].lower())
            word_index += 1
        else:
            titled.append(part)
    return "".join(titled).strip()


def unique_post_title(title):
    candidate = title
    counter = 2
    while BlogPost.query.filter_by(title=candidate).first():
        candidate = f"{title} ({counter})"
        counter += 1
    return candidate


def safe_filename(value):
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:60] or "article"


def topic_initials(topic):
    words = re.findall(r"[A-Za-z0-9]+", topic)
    if not words:
        return "A"
    return "".join(word[0].upper() for word in words[:3])


def wrap_svg_text(text, max_chars=28, max_lines=3):
    words = re.findall(r"\S+", text)
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) == max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines and words:
        used_word_count = sum(len(re.findall(r"\S+", line)) for line in lines)
        if used_word_count < len(words):
            lines[-1] = lines[-1].rstrip(" .") + "..."

    return lines or ["Article"]


def generate_topic_cover(topic, audience):
    digest = hashlib.sha256(f"{topic}|{audience}".encode("utf-8")).hexdigest()
    audience_slug = safe_filename(audience)
    topic_slug = safe_filename(topic)
    return f"/generated-cover/{audience_slug}/{topic_slug}-{digest[:10]}.svg"


def article_image_url(post):
    """Return a real article image URL, never a generated placeholder cover."""
    image_url = str(getattr(post, "img_url", "") or "").strip()
    if not image_url or "/generated-cover/" in image_url:
        return ""
    return image_url


def post_has_real_image(post):
    return bool(article_image_url(post))


def post_is_archived(post, reference_time=None):
    published_at = parse_post_timestamp(post)
    if published_at == datetime.min:
        return True
    cutoff = (reference_time or datetime.now()) - timedelta(days=ARTICLE_ARCHIVE_AGE_DAYS)
    return published_at < cutoff


def render_topic_cover_svg(topic, audience):
    digest = hashlib.sha256(f"{topic}|{audience}".encode("utf-8")).hexdigest()
    palettes = [
        ("#0f766e", "#123c69", "#d98921"),
        ("#255f85", "#101623", "#e25544"),
        ("#4f46e5", "#0f172a", "#14b8a6"),
        ("#0e7490", "#1e293b", "#f59e0b"),
    ]
    primary, secondary, accent = palettes[int(digest[:2], 16) % len(palettes)]
    title_lines = wrap_svg_text(title_case_topic(topic.replace("-", " ")))
    title_font_size = 74 if len(title_lines) == 1 else 62 if len(title_lines) == 2 else 52
    title_start_y = 380 if len(title_lines) == 1 else 340 if len(title_lines) == 2 else 315
    title_line_gap = title_font_size + 18
    title_tspans = "\n".join(
        f'    <tspan x="150" dy="{0 if index == 0 else title_line_gap}">{escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    safe_audience = escape(audience.replace("-", " ").title())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{primary}"/>
      <stop offset="58%" stop-color="{secondary}"/>
      <stop offset="100%" stop-color="#101623"/>
    </linearGradient>
    <radialGradient id="glow" cx="78%" cy="22%" r="55%">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.65"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <rect width="1600" height="900" fill="url(#glow)"/>
  <circle cx="1240" cy="165" r="220" fill="#ffffff" opacity="0.08"/>
  <circle cx="1375" cy="650" r="360" fill="#ffffff" opacity="0.055"/>
  <path d="M0 705 C300 600 530 805 805 690 C1080 575 1270 585 1600 470 L1600 900 L0 900 Z" fill="#ffffff" opacity="0.08"/>
  <rect x="112" y="112" width="1376" height="676" rx="32" fill="#ffffff" opacity="0.08" stroke="#ffffff" stroke-opacity="0.22"/>
  <text x="150" y="200" fill="#ffffff" opacity="0.78" font-family="Inter, Arial, sans-serif" font-size="34" font-weight="800" letter-spacing="6">AYNCODE / {safe_audience}</text>
  <text x="150" y="{title_start_y}" fill="#ffffff" font-family="Inter, Arial, sans-serif" font-size="{title_font_size}" font-weight="900">
{title_tspans}
  </text>
  <text x="150" y="585" fill="#ffffff" opacity="0.82" font-family="Inter, Arial, sans-serif" font-size="34" font-weight="600">Generated cover for a focused article draft</text>
</svg>'''


def clean_research_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def summarize_research_text(text, max_sentences=3):
    cleaned = clean_research_text(text)
    if not cleaned:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    useful = []
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 45:
            continue
        useful.append(sentence)
        if len(useful) >= max_sentences:
            break
    summary = " ".join(useful) or cleaned
    return summary[:900].rstrip()


def fetch_article_research(link):
    try:
        request_obj = Request(
            link,
            headers={
                "User-Agent": "Mozilla/5.0 AyNcodeResearchBot/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(request_obj, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return ""
            html = response.read(350_000)
    except Exception:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()

    meta_description = soup.find("meta", attrs={"name": "description"})
    meta_text = meta_description.get("content", "") if meta_description else ""
    paragraphs = [
        clean_research_text(paragraph.get_text(" "))
        for paragraph in soup.find_all("p")
    ]
    paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 60]
    return summarize_research_text(" ".join([meta_text, *paragraphs]), max_sentences=4)


def enrich_events_with_research(events, limit=4):
    enriched = []
    for event in events[:limit]:
        research = event.get("summary") or event.get("description") or ""
        article_research = fetch_article_research(event.get("link", ""))
        if article_research:
            research = article_research
        enriched.append({**event, "research": summarize_research_text(research, max_sentences=4)})
    return enriched + events[limit:]


def fetch_recent_events(query, limit=12, hours=None):
    search_query = quote_plus(query.strip())
    feed_url = f"https://news.google.com/rss/search?q={search_query}&hl=en-US&gl=US&ceid=US:en"
    request_obj = Request(feed_url, headers={"User-Agent": "AyNcodeArticleGenerator/1.0"})
    with urlopen(request_obj, timeout=8) as response:
        feed = response.read()

    root = ET.fromstring(feed)
    events = []
    cutoff = None
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    for item in root.findall("./channel/item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        published = item.findtext("pubDate", "").strip()
        source = item.findtext("source", "").strip()
        description = clean_research_text(item.findtext("description", ""))
        if cutoff and published:
            try:
                published_at = parsedate_to_datetime(published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                if published_at < cutoff:
                    continue
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
        if title and link:
            events.append(
                {
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": source or "Google News",
                    "description": description,
                }
            )
        if len(events) >= limit:
            break
    return events


def render_event_section(events):
    if not events:
        return ""

    event_items = []
    for event in events:
        source = escape(event["source"])
        title = escape(event["title"])
        link = escape(event["link"], quote=True)
        published = escape(event["published"])
        event_items.append(
            f'<li><a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
            f' <span>({source}{", " + published if published else ""})</span></li>'
        )

    return f"""
<h2>Source context</h2>
<p>These are the live headlines I used as the source frame for this note:</p>
<ul>
{''.join(event_items)}
</ul>
""".strip()


def article_lens_for_topic(topic):
    topic_lower = topic.lower()
    if "market size" in topic_lower or "market accelerating" in topic_lower:
        return {
            "subtitle": "My read on the latest AI software market headline and what it says about demand for useful automation.",
            "intro": (
                "I read this kind of market-size headline as a demand signal, not as proof that every AI product is valuable. "
                "The important question is why buyers keep allocating attention and budget to AI software in the first place."
            ),
            "why": (
                "For builders, market growth only matters when it maps to a concrete customer problem. "
                "A large category can still punish vague products. The useful opportunity is to find the operational pain behind the spending and build something that makes the work easier to measure."
            ),
            "sections": [
                (
                    "The signal underneath the market number",
                    "A rising AI software market points to demand, but demand is not evenly distributed. Buyers are most likely to keep paying for tools that reduce repetitive work, improve decision speed, or make existing systems more useful.",
                ),
                (
                    "Where I would focus",
                    "I would focus on workflows that already have budget and urgency: support operations, internal search, developer tooling, reporting, compliance review, and sales or finance handoffs. Those are places where AI can be judged by practical output rather than novelty.",
                ),
                (
                    "What to avoid",
                    "The trap is assuming category growth will carry a weak product. It will not. A strong AI product needs a narrow use case, clean integration, clear controls, and a result the customer can explain without repeating the market headline.",
                ),
            ],
            "final": (
                "My practical takeaway: market growth is useful context, but the product still has to earn trust one workflow at a time."
            ),
        }

    if any(term in topic_lower for term in ("stock", "stocks", "shares", "invest", "analyst")):
        return {
            "subtitle": "My read on the latest AI market headlines, focused on what builders can learn from where attention and capital are moving.",
            "intro": (
                "I do not read this as a stock recommendation. I read it as a signal about the pressure around AI: "
                "public markets keep rewarding companies that can connect AI to revenue, distribution, and credible operating leverage."
            ),
            "why": (
                "For builders, that matters because market attention usually follows a simpler question: where is AI creating measurable value? "
                "A headline about AI stocks is less useful as an investment checklist and more useful as a reminder that demos are not enough. "
                "The work has to show up in revenue, retention, margin, or a workflow customers already care about."
            ),
            "sections": [
                (
                    "The signal underneath the market story",
                    "When AI stock coverage sits next to bullish Microsoft coverage and market-size reports, my takeaway is that investors are still hunting for durable AI monetization. The useful lesson is not which ticker gets attention today. It is that the market is trying to separate real operating advantage from generic AI positioning.",
                ),
                (
                    "What I would watch as a builder",
                    "I would watch whether a company can turn AI into a specific workflow advantage: faster support, better developer tooling, stronger cloud usage, cleaner analytics, or more useful automation inside existing teams. That is where the story becomes practical. The products that matter will make work visibly easier, not merely attach AI language to the same old interface.",
                ),
                (
                    "The risk in chasing the headline",
                    "The weak version of this trend is building for the narrative instead of the customer. If the product cannot explain what task improves, what cost falls, or what decision gets better, the AI angle will fade quickly. My bias is to build around a narrow operational promise first, then let the market language follow the proof.",
                ),
            ],
            "final": (
                "My practical takeaway: I would not treat AI stock headlines as advice on what to buy. "
                "I would treat them as evidence that the market is still asking which AI products can produce real economic gravity."
            ),
        }

    if any(term in topic_lower for term in ("security", "review", "government", "safety", "ai model", "models")):
        return {
            "subtitle": "My read on the latest AI oversight headline and what it means for builders shipping model-driven products.",
            "intro": (
                "The useful part of this story is not just that large AI labs are dealing with more review. "
                "It is that model capability is moving close enough to real infrastructure that trust, access, and accountability now sit beside performance."
            ),
            "why": (
                "For builders, this matters because users will not judge AI products only by how impressive the output looks. "
                "They will also ask who can inspect the system, how mistakes are contained, and whether the product behaves predictably in sensitive workflows."
            ),
            "sections": [
                (
                    "AI trust is becoming product infrastructure",
                    "Security review and model oversight are becoming part of the product surface. That means the strongest AI products need logs, permission boundaries, fallbacks, and clear explanations for what the system is allowed to do.",
                ),
                (
                    "What smaller teams can copy",
                    "A small team does not need a government review process, but it can copy the discipline: define risky actions, require human approval where the downside is high, and keep enough traceability that errors can be understood rather than guessed at.",
                ),
                (
                    "The product lesson",
                    "The companies that make AI feel dependable will have an advantage over companies that only make it feel powerful. Reliability is becoming a feature, especially when models touch customer data, internal systems, or business-critical decisions.",
                ),
            ],
            "final": (
                "My practical takeaway: every AI feature needs a trust plan. Capability gets attention, but accountability is what keeps the product usable."
            ),
        }

    if any(term in topic_lower for term in ("copilot", "vs code", "developer", "commit", "tooling")):
        return {
            "subtitle": "My read on the latest developer-tooling headline and what it says about trust in AI-assisted work.",
            "intro": (
                "Developer AI tools are becoming normal parts of the workflow, which means small trust failures matter more than they used to. "
                "When tooling touches commits, authorship, or production code, the product has to be precise about what it did and what the human did."
            ),
            "why": (
                "For builders, the lesson is simple: AI assistance needs clear boundaries. A tool can be useful and still create confusion if attribution, review, or ownership is muddy."
            ),
            "sections": [
                (
                    "Trust is part of the workflow",
                    "The best developer tools reduce friction without making ownership unclear. If an AI system drafts code, suggests changes, or helps shape commits, the surrounding workflow should make review and responsibility obvious.",
                ),
                (
                    "Where product teams should focus",
                    "I would focus on visibility: what changed, why it changed, who approved it, and how to reverse it. Those details are not polish. They are the difference between a useful assistant and a tool teams hesitate to adopt.",
                ),
                (
                    "The broader signal",
                    "AI coding tools are moving from novelty to infrastructure. As that happens, the winning products will feel boring in the best way: predictable, inspectable, and respectful of how teams already ship software.",
                ),
            ],
            "final": (
                "My practical takeaway: AI developer tools win when they make the human more effective without blurring accountability."
            ),
        }

    return {
        "subtitle": f"My practical read on {topic}, focused on what the latest AI headline means for builders and operators.",
        "intro": (
            "I am reading this as another sign that AI is moving from broad hype into concrete operating decisions. "
            "The useful question is not whether AI is important. The useful question is where it changes the way products are built, sold, or run."
        ),
        "why": (
            "For founders and operators, that matters because the market is getting less patient with vague AI promises. "
            "The strongest opportunities are tied to real workflows, clearer product value, and measurable improvements in how teams do their work."
        ),
        "sections": [
            (
                "The practical signal",
                "The headline is useful because it points to where attention is shifting. AI is becoming part of product strategy, pricing, internal operations, and customer expectations rather than sitting off to the side as a feature experiment.",
            ),
            (
                "What I would do with it",
                "I would translate the news into one product question: what workflow becomes easier, faster, or more reliable because of this shift? If the answer is not specific, the idea probably needs more work before it becomes a strong product bet.",
            ),
            (
                "What to avoid",
                "The trap is reacting to every headline with another generic AI feature. A better move is to pick a narrow customer pain, add AI only where it improves the job, and make the result easy to verify.",
            ),
        ],
        "final": (
            "My practical takeaway: the headline matters only if it helps clarify what to build, what to measure, and what to ignore."
        ),
    }


def event_note(event):
    return summarize_research_text(
        event.get("research") or event.get("description") or event.get("title", ""),
        max_sentences=2,
    )


def extract_focus_terms(topic, events):
    text = " ".join([topic, *[event.get("title", "") for event in events]])
    candidates = re.findall(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3}", text)
    stop_phrases = {
        "AI",
        "How",
        "Why",
        "What",
        "The",
        "Latest",
        "Google News",
        "Source",
        "Azerbaijan",
        "Latest News",
        "Latest News From Azerbaijan",
    }
    terms = []
    seen = set()
    for candidate in candidates:
        cleaned = candidate.strip(" -:,.")
        if cleaned in stop_phrases or len(cleaned) < 3:
            continue
        normalized = cleaned.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(cleaned)
        if len(terms) >= 5:
            break
    return terms


def render_research_context(events):
    if not events:
        return ""

    items = []
    for event in events:
        source = escape(event.get("source") or "Source")
        title = escape(event.get("title") or "Untitled")
        link = escape(event.get("link") or "#", quote=True)
        published = escape(event.get("published") or "")
        note = escape(event_note(event))
        note_markup = f"<p>{note}</p>" if note else ""
        items.append(
            f'<li><a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a> '
            f'<span>({source}{", " + published if published else ""})</span>{note_markup}</li>'
        )

    return f"""
<h2>What I found in the sources</h2>
<p>I used the source notes below as the factual boundary for this article.</p>
<ul>
{''.join(items)}
</ul>
""".strip()


def build_researched_article(topic, audience_text, events):
    source_events = [event for event in events if event.get("title")]
    top_event = source_events[0]
    terms = extract_focus_terms(topic, source_events)
    terms_text = ", ".join(terms[:3]) if terms else "this AI story"
    top_source = top_event.get("source") or "the lead source"
    top_title = top_event.get("title") or topic
    top_note = event_note(top_event)
    secondary_notes = [event_note(event) for event in source_events[1:3] if event_note(event)]

    subtitle = f"My read on {topic}, based on the latest source context around {terms_text}."
    research_context = render_research_context(source_events[:4])

    if top_note:
        lead = (
            f"I am reading this through the lead source from {escape(top_source)}: "
            f"{escape(top_title)}. The useful part is not the headline by itself, but the specific pattern it points to around {escape(terms_text)}."
        )
        source_detail = f"<p>{escape(top_note)}</p>"
    else:
        lead = (
            f"I am reading {escape(topic)} as a concrete AI business signal, not as a broad trend note. "
            f"The useful part is what it suggests around {escape(terms_text)}."
        )
        source_detail = ""

    if secondary_notes:
        secondary = " ".join(secondary_notes)
        corroboration = (
            f"<p>The surrounding sources add useful context: {escape(summarize_research_text(secondary, max_sentences=3))}</p>"
        )
    else:
        corroboration = (
            "<p>I am being careful not to stretch this beyond the available source material. One headline can be useful without becoming a complete market thesis.</p>"
        )

    body = f"""
<p>{lead}</p>

<h2>What the reporting points to</h2>
{source_detail}
{corroboration}

{research_context}

<h2>Why I think it matters</h2>
<p>For {escape(audience_text)}, the practical question is what changes if this story keeps developing. I am looking at whether it changes pricing power, customer expectations, platform control, product distribution, or the cost of building with AI.</p>

<h2>The builder read</h2>
<p>My read is that {escape(topic)} should be treated as a product and operations signal first. If a company is changing its business model, accelerating AI software demand, opening model access, or shifting developer tooling, the important question is where that change touches real workflows.</p>

<h2>What I would watch next</h2>
<p>I would watch for evidence that the story moves from announcement to behavior: customers adopting the product differently, developers changing their tooling choices, enterprises changing budgets, or regulators forcing new controls. That is where a headline becomes useful signal.</p>

<h2>Final thought</h2>
<p>The article is strongest when it stays close to the sources. My takeaway is that {escape(terms_text)} deserves attention only where it changes what builders can ship, how customers buy, or how teams manage risk.</p>
""".strip()
    return subtitle, body


def generate_article(topic, audience, angle, events=None):
    clean_topic = re.sub(r"\s+", " ", topic).strip()
    safe_topic = escape(clean_topic)
    title_topic = title_case_topic(clean_topic)
    title = unique_post_title(f"What {title_topic} Signals for Builders")
    audience_labels = {
        "developers": "developers who want practical steps",
        "founders": "founders turning ideas into useful products",
        "beginners": "beginners learning the fundamentals",
        "general": "curious readers who want a clear overview",
    }
    audience_text = audience_labels.get(audience, audience_labels["general"])
    researched_events = [event for event in (events or []) if event.get("research") or event.get("description")]
    if researched_events:
        subtitle, body = build_researched_article(clean_topic, audience_text, researched_events)
        return title, subtitle, body

    lens = article_lens_for_topic(clean_topic)
    subtitle = lens["subtitle"]
    event_section = render_event_section(events or [])
    source_bridge = ""
    if events:
        source_bridge = "<p>I am keeping the analysis tied to the source headlines below and avoiding claims the links do not support.</p>"

    section_markup = "\n\n".join(
        f"<h2>{escape(heading)}</h2>\n<p>{escape(paragraph)}</p>"
        for heading, paragraph in lens["sections"]
    )

    body = f"""
<p>{escape(lens["intro"])}</p>

<h2>Why it matters</h2>
<p>{escape(lens["why"])}</p>

<p>For {escape(audience_text)}, the practical value is in turning the headline into a sharper product decision instead of treating it as background noise.</p>

{source_bridge}

{event_section}

{section_markup}

<h2>Final thought</h2>
<p>{escape(lens["final"])}</p>
""".strip()
    return title, subtitle, body


def gravatar_url(email, size=100, rating='g', default='retro'):
    hash_value = hashlib.md5(email.strip().lower().encode('utf-8')).hexdigest()
    return f"https://www.gravatar.com/avatar/{hash_value}?s={size}&d={default}&r={rating}"

app.jinja_env.filters['gravatar'] = gravatar_url


@app.context_processor
def inject_template_globals():
    return {
        "date": date.today().year,
        "is_admin": is_admin_user(current_user),
        "article_image_url": article_image_url,
    }


def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_user(current_user):
            abort(403, "You do not have permission to access this resource.")
        return f(*args, **kwargs)
    return decorated_function


def login_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",", 1)[0].strip() or request.remote_addr or "unknown")[:128]


def login_bucket(scope, value):
    key = app.config["SECRET_KEY"].encode("utf-8")
    digest = hmac.new(key, f"{scope}:{value}".encode("utf-8"), "sha256").hexdigest()
    return f"{scope}:{digest}"


def login_throttle_buckets(email):
    return (
        (login_bucket("email", normalize_email(email)), LOGIN_EMAIL_LIMIT),
        (login_bucket("ip", login_client_ip()), LOGIN_IP_LIMIT),
    )


def is_login_throttled(email):
    now = datetime.utcnow()
    for bucket, _limit in login_throttle_buckets(email):
        throttle = LoginThrottle.query.filter_by(bucket=bucket).first()
        if throttle and throttle.blocked_until and throttle.blocked_until > now:
            return True
    return False


def record_login_failure(email):
    now = datetime.utcnow()
    LoginThrottle.query.filter(
        LoginThrottle.last_attempt < now - timedelta(days=1)
    ).delete(synchronize_session=False)
    for bucket, limit in login_throttle_buckets(email):
        throttle = LoginThrottle.query.filter_by(bucket=bucket).first()
        if not throttle:
            throttle = LoginThrottle(
                bucket=bucket,
                attempts=0,
                window_started=now,
                last_attempt=now,
            )
            db.session.add(throttle)
        if now - throttle.window_started > LOGIN_WINDOW:
            throttle.attempts = 0
            throttle.window_started = now
            throttle.blocked_until = None
        throttle.attempts += 1
        throttle.last_attempt = now
        if throttle.attempts >= limit:
            throttle.blocked_until = now + LOGIN_LOCK_TIME
    db.session.commit()


def clear_login_throttles(email):
    buckets = [bucket for bucket, _limit in login_throttle_buckets(email)]
    LoginThrottle.query.filter(LoginThrottle.bucket.in_(buckets)).delete(synchronize_session=False)
    db.session.commit()


def password_hash_needs_upgrade(password_hash):
    return not (password_hash or "").startswith("scrypt:")


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.session_protection = "strong"

@login_manager.user_loader
def load_user(user_id):#This callback is used to reload the user object from the user ID stored in the session
    user = db.session.get(Users, int(user_id))
    if not user or user.is_disabled:
        return None
    return user


@app.context_processor
def account_context():
    return {
        "logged_in": current_user.is_authenticated,
        "is_admin": is_admin_user(current_user),
        "logout_form": LogoutForm(),
    }


def get_or_create_deleted_user():
    deleted_email = "deleted-user@ayncode.invalid"
    user = Users.query.filter_by(email=deleted_email).first()
    if user:
        return user
    user = Users(
        email=deleted_email,
        password=generate_password_hash(secrets.token_urlsafe(32)),
        name="Deleted user",
        role="system",
        email_verified=True,
        is_disabled=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def remove_user_account(user):
    if user.role in {"admin", "system"}:
        return False

    deleted_user = get_or_create_deleted_user()
    replacement_author = Users.query.filter(
        Users.role == "admin",
        Users.is_disabled.is_(False),
        Users.id != user.id,
    ).first()
    if user.posts and not replacement_author:
        db.session.rollback()
        return False

    for comment in list(user.comments):
        comment.comment_author = deleted_user
    for post in list(user.posts):
        post.author = replacement_author

    db.session.flush()
    db.session.delete(user)
    db.session.commit()
    return True


@app.route('/')

def get_all_posts():
    query = request.args.get("q", "").strip()
    posts_query = BlogPost.query
    if query:
        like_query = f"%{query}%"
        posts_query = posts_query.filter(
            or_(
                BlogPost.title.ilike(like_query),
                BlogPost.subtitle.ilike(like_query),
                BlogPost.body.ilike(like_query),
            )
        )

    posts = [
        post
        for post in sort_posts_latest_first(posts_query.all())
        if post_has_real_image(post) and (query or not post_is_archived(post))
    ]
    posts = decorate_posts(posts)
    stats = {
        "posts": len(posts),
        "comments": sum(len(post.comments) for post in posts),
        "views": sum(post.views or 0 for post in posts),
        "likes": sum(post.likes or 0 for post in posts),
        "minutes": sum(post.reading_time for post in posts),
    }
    return render_template(
        "index.html",
        all_posts=posts,
        logged_in=current_user.is_authenticated,
        query=query,
        stats=stats,
    )


@app.route('/archive')
def archive():
    query = request.args.get("q", "").strip()
    posts_query = BlogPost.query
    if query:
        like_query = f"%{query}%"
        posts_query = posts_query.filter(
            or_(
                BlogPost.title.ilike(like_query),
                BlogPost.subtitle.ilike(like_query),
                BlogPost.body.ilike(like_query),
            )
        )

    posts = [
        post
        for post in sort_posts_latest_first(posts_query.all())
        if post_has_real_image(post) and post_is_archived(post)
    ]
    return render_template(
        "archive.html",
        all_posts=decorate_posts(posts),
        logged_in=current_user.is_authenticated,
        query=query,
    )


@app.route('/register',methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        user = Users.query.filter(func.lower(Users.email) == email).first()
        if not user:
            user = Users(
                email=email,
                password=generate_password_hash(form.password.data),
                name=form.name.data.strip(),
                role="user",
                email_verified=False,
            )
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                user = None
            if user and not send_email_verification(user):
                app.logger.warning("Verification email could not be delivered for user id %s", user.id)
        flash("If this email can be registered, a verification message will arrive shortly.")
        return redirect(url_for("login"))
    return render_template("register.html",form=form)


@app.route("/verify-email/<token>")
def verify_email(token):
    user = verify_email_verification_token(token)
    if not user:
        flash("That verification link is invalid, expired, or already used.")
        return redirect(url_for("resend_verification"))
    user.email_verified = True
    user.email_verification_nonce = None
    db.session.commit()
    flash("Email verified. You can now log in.")
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))
    form = ResendVerificationForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        user = Users.query.filter(func.lower(Users.email) == email).first()
        if user and not user.email_verified and not user.is_disabled:
            if not send_email_verification(user):
                app.logger.warning("Verification resend could not be delivered for user id %s", user.id)
        flash("If an unverified account exists, a new verification message will arrive shortly.")
        return redirect(url_for("login"))
    return render_template("resend-verification.html", form=form, logged_in=False)



@app.route('/login',methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    form = LoginForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        if is_login_throttled(email):
            flash("Sign-in is temporarily unavailable after several attempts. Please wait and try again.")
            return render_template("login.html", form=form), 429

        user = Users.query.filter(func.lower(Users.email) == email).first()
        password_hash = user.password if user else DUMMY_PASSWORD_HASH
        password_matches = check_password_hash(password_hash, form.password.data)
        can_login = bool(
            user
            and password_matches
            and user.email_verified
            and not user.is_disabled
        )

        if can_login:
            clear_login_throttles(email)
            if password_hash_needs_upgrade(user.password):
                user.password = generate_password_hash(form.password.data)
                db.session.commit()
            session.clear()
            session.permanent = True
            login_user(user, fresh=True)
            next_url = request.args.get("next")
            if is_safe_redirect_url(next_url):
                return redirect(next_url)
            return redirect(url_for("get_all_posts"))

        record_login_failure(email)
        flash("Sign-in could not be completed. Check your credentials and verify your email.")

    return render_template("login.html",form=form)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        user = Users.query.filter(func.lower(Users.email) == email).first()
        if user and not user.is_disabled:
            token = generate_password_reset_token(user)
            reset_url = url_for("reset_password", token=token, _external=True)
            if not send_password_reset_email(user, reset_url):
                app.logger.warning("Password reset email could not be delivered for user id %s", user.id)

        flash("If that email is registered, I sent a password reset link.")
        return redirect(url_for("login"))

    return render_template("forgot-password.html", form=form, logged_in=False)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    user = verify_password_reset_token(token)
    if not user:
        flash("That password reset link is invalid or expired.")
        return redirect(url_for("forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.password = generate_password_hash(form.password.data)
        user.password_reset_nonce = None
        clear_login_throttles(user.email)
        db.session.commit()
        flash("Password updated. I can log in with the new password now.")
        return redirect(url_for("login"))

    return render_template("reset-password.html", form=form, logged_in=False)



@app.route('/logout', methods=["POST"])
@login_required
def logout():
    form = LogoutForm()
    if not form.validate_on_submit():
        abort(400)
    logout_user()
    session.clear()
    return redirect(url_for('get_all_posts'))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    form = DeleteAccountForm()
    if form.validate_on_submit():
        user = current_user._get_current_object()
        if is_admin_user(user):
            flash("The primary administrator account cannot be deleted here.")
            return render_template("account.html", form=form)
        if not check_password_hash(user.password, form.password.data):
            flash("The current password was not correct.")
            return render_template("account.html", form=form)
        logout_user()
        session.clear()
        if remove_user_account(user):
            flash("Your account was deleted. Existing comments now appear under Deleted user.")
            return redirect(url_for("get_all_posts"))
        flash("The account could not be deleted safely. Please contact AyNcode.")
    return render_template("account.html", form=form)


@app.route("/admin/users")
@login_required
@admin_only
def admin_users():
    users = Users.query.filter(Users.role != "system").order_by(Users.created_at.desc()).all()
    action_form = AdminUserActionForm()
    return render_template("admin-users.html", users=users, action_form=action_form)


@app.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@login_required
@admin_only
def admin_user_action(user_id, action):
    form = AdminUserActionForm()
    if not form.validate_on_submit():
        abort(400)
    user = db.get_or_404(Users, user_id)
    if user.role in {"admin", "system"}:
        flash("Administrator and system accounts cannot be changed here.")
        return redirect(url_for("admin_users"))
    if action == "disable":
        user.is_disabled = True
        db.session.commit()
        flash("User access disabled.")
    elif action == "enable":
        user.is_disabled = False
        db.session.commit()
        flash("User access restored.")
    elif action == "delete":
        if remove_user_account(user):
            flash("User account deleted and existing comments anonymized.")
        else:
            flash("The user could not be deleted safely.")
    else:
        abort(404)
    return redirect(url_for("admin_users"))


@app.route("/generated-cover/<audience>/<path:slug>.svg")
def generated_cover(audience, slug):
    topic = re.sub(r"-[a-f0-9]{10}$", "", slug)
    svg = render_topic_cover_svg(topic, audience)
    return Response(svg, mimetype="image/svg+xml")

@app.route("/post/<int:post_id>", methods=['GET', 'POST'])
def show_post(post_id):
    requested_post = db.get_or_404(BlogPost, post_id)
    if not post_has_real_image(requested_post):
        abort(404)
    if request.method == "GET" and not request.args.get("reacted"):
        requested_post.views = (requested_post.views or 0) + 1
        db.session.commit()
    decorate_posts([requested_post])
    comment_data = CommentForm()

    if comment_data.validate_on_submit():
        if not current_user.is_authenticated:
            flash('Please login or Register to comment')
            return redirect(url_for("login", next=url_for("show_post", post_id=requested_post.id)))

        latest_comment = Comment.query.filter_by(author_id=current_user.id).order_by(
            Comment.created_at.desc()
        ).first()
        now = datetime.utcnow()
        if (
            latest_comment
            and latest_comment.created_at
            and now - latest_comment.created_at < COMMENT_COOLDOWN
        ):
            flash("Please wait a few seconds before posting another comment.")
            return redirect(url_for("show_post", post_id=requested_post.id) + "#discussion")

        parent = None
        if comment_data.parent_id.data:
            try:
                parent_id = int(comment_data.parent_id.data)
            except (TypeError, ValueError):
                abort(400)
            parent = db.session.get(Comment, parent_id)
            if not parent or parent.post_id != requested_post.id:
                abort(400)
            if parent.parent is not None:
                parent = parent.parent

        comment = Comment(
            text=comment_data.body.data.strip(),
            comment_author=current_user,
            parent_post=requested_post,
            parent=parent,
        )
        db.session.add(comment)
        db.session.commit()
        return redirect(
            url_for("show_post", post_id=requested_post.id) + f"#comment-{comment.id}"
        )

    comment_threads = sorted(
        (comment for comment in requested_post.comments if comment.parent_id is None),
        key=lambda comment: comment.created_at or datetime.min,
    )
    return render_template(
        "post.html",
        post=requested_post,
        logged_in=current_user.is_authenticated,
        form=comment_data,
        comment_threads=comment_threads,
        comment_count=len(requested_post.comments),
        comment_action_form=AdminUserActionForm(),
        reaction_form=AdminUserActionForm(),
    )


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    form = AdminUserActionForm()
    if not form.validate_on_submit():
        abort(400)
    comment = db.get_or_404(Comment, comment_id)
    if comment.author_id != current_user.id and not is_admin_user(current_user):
        abort(403)

    post_id = comment.post_id
    if comment.replies:
        comment.text = "Comment removed by its author."
        comment.comment_author = get_or_create_deleted_user()
    else:
        db.session.delete(comment)
    db.session.commit()
    flash("Comment removed.")
    return redirect(url_for("show_post", post_id=post_id) + "#discussion")


@app.route("/post/<int:post_id>/react/<reaction>", methods=["POST"])
def react_to_post(post_id, reaction):
    form = AdminUserActionForm()
    if not form.validate_on_submit():
        abort(400)
    post = db.get_or_404(BlogPost, post_id)
    reaction_fields = {
        "like": "likes",
        "upvote": "upvotes",
        "downvote": "downvotes",
    }
    field = reaction_fields.get(reaction)
    if not field:
        abort(404)

    setattr(post, field, (getattr(post, field) or 0) + 1)
    db.session.commit()
    return redirect(url_for("show_post", post_id=post.id, reacted=1))



@app.route('/openclaw')
@app.route('/openclaw/')
@app.route('/open-claw')
@app.route('/openclawweb')
@app.route('/open-claw-web')
@app.route('/OpenClaw')
@app.route('/claw')
@app.route('/ayncode')
def openclaw():
    openclaw_url = os.environ.get('OPENCLAW_URL', 'https://clawflow-studio-site.vercel.app/')
    return render_template(
        'openclaw.html',
        logged_in=current_user.is_authenticated,
        openclaw_url=openclaw_url,
    )

@app.route('/about')
def about():
    return render_template("about.html",logged_in=current_user.is_authenticated)


@app.route('/products')
def products():
    return render_template(
        "products.html",
        logged_in=current_user.is_authenticated,
        openclaw_url=os.environ.get('OPENCLAW_URL', 'https://clawflow-studio-site.vercel.app/'),
    )


@app.route('/vocalframe')
def vocalframe():
    return render_template("vocalframe.html", logged_in=current_user.is_authenticated)


@app.route('/contact',methods=['GET','POST'])
def contact():
    confirm = False
    if request.method == 'POST':
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        num = request.form.get("phone", "").strip()
        msg = request.form.get("message", "").strip()
        password = (os.environ.get('GMAIL_PASSWORD') or '').replace(" ", "").strip()
        my_email = (
            os.environ.get('GMAIL_EMAIL')
            or os.environ.get('MAIL_USERNAME')
            or os.environ.get('CONTACT_EMAIL')
            or DEFAULT_ADMIN_EMAIL
        ).strip()

        if not password or not my_email:
            flash('Contact service is temporarily unavailable. Please try again later.')
            return render_template("contact.html", logged_in=current_user.is_authenticated, confirm=False)

        try:
            with SMTP('smtp.gmail.com', 587) as smtp:
                smtp.starttls()
                smtp.login(my_email, password)
                smtp.sendmail(
                    my_email,
                    my_email,
                    msg=f"Subject:{name or 'Website Contact'}\n\nNumber:{num}\n\nEmail from: {email}\n\n{msg}",
                )
        except (SMTPException, OSError) as exc:
            app.logger.warning("Contact email failed: %s", exc)
            flash('Contact service is temporarily unavailable. Please try again later.')
            return render_template("contact.html", logged_in=current_user.is_authenticated, confirm=False)
        confirm = True
    return render_template("contact.html",logged_in=current_user.is_authenticated,confirm=confirm)


@app.route("/new-post",methods=["GET", "POST"])
@login_required
@admin_only
def add_new_post():
    form = CreatePostForm()
    if form.validate_on_submit():
        new_post = BlogPost(
            title=form.title.data,
            subtitle=form.subtitle.data,
            body=form.body.data,
            img_url=form.img_url.data,
            author=current_user,
            date=date.today().strftime("%B %d, %Y"),
            published_at=datetime.now().strftime("%B %d, %Y %I:%M %p")
        )
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("get_all_posts"))
    return render_template("make-post.html", form=form,logged_in=current_user.is_authenticated)


@app.route("/generate-post", methods=["GET", "POST"])
@login_required
@admin_only
def generate_post():
    form = GenerateArticleForm()
    if form.validate_on_submit():
        image_url = (form.img_url.data or "").strip()
        if not image_url:
            flash("Add a real image URL before publishing. Placeholder cover images are disabled.")
            return render_template("generate-post.html", form=form, logged_in=current_user.is_authenticated)

        events = []
        if form.use_real_events.data:
            event_query = form.event_query.data or form.topic.data
            try:
                events = fetch_recent_events(event_query)
            except Exception:
                flash("Could not fetch live events right now. Generated a general article draft instead.")

        title, subtitle, body = generate_article(
            form.topic.data,
            form.audience.data,
            form.angle.data,
            events=events,
        )
        new_post = BlogPost(
            title=title,
            subtitle=subtitle,
            body=body,
            img_url=image_url,
            author=current_user,
            date=date.today().strftime("%B %d, %Y"),
            published_at=datetime.now().strftime("%B %d, %Y %I:%M %p")
        )
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("show_post", post_id=new_post.id))
    return render_template("generate-post.html", form=form, logged_in=current_user.is_authenticated)


@app.route("/edit-post/<int:post_id>",methods=["GET", "POST"])
@login_required
@admin_only
def edit_post(post_id):
    post = db.get_or_404(BlogPost, post_id)
    edit_form = CreatePostForm(
        title=post.title,
        subtitle=post.subtitle,
        img_url=post.img_url,
        author=post.author.name,
        body=post.body
    )
    if edit_form.validate_on_submit():
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = edit_form.img_url.data
        post.author = current_user
        post.body = edit_form.body.data
        db.session.commit()
        return redirect(url_for("show_post", post_id=post.id))

    return render_template("make-post.html", form=edit_form,logged_in=current_user.is_authenticated)


@app.route("/delete/<int:post_id>")
@login_required
@admin_only
def delete_post(post_id):
    post_to_delete = db.get_or_404(BlogPost, post_id)
    slug, title = generated_post_key_for_title(post_to_delete.title)
    remember_deleted_generated_post(post_to_delete)
    remove_generated_post_from_source(slug, title)
    db.session.delete(post_to_delete)
    db.session.commit()
    return redirect(url_for('get_all_posts'))
@app.route('/download')
def download():
    return send_from_directory(
        os.path.join(app.root_path, "static", "edu"),
        "Ayotunde_Oyeniyi.pdf",
        as_attachment=True,
        download_name="Ayotunde_Oyeniyi.pdf",
    )

if __name__ == "__main__":
    app.run(debug=True)
