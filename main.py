
from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, LoginManager, login_required, current_user, logout_user
from forms import CommentForm, CreatePostForm, ForgotPasswordForm, GenerateArticleForm, LoginForm, RegisterForm, ResetPasswordForm
from functools import wraps
from models import BlogPost, Comment, Users, db
from sqlalchemy import func, inspect, or_, text
import os
import json
import re
from pathlib import Path
from smtplib import SMTP, SMTPException
from html import escape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

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
)
ckeditor = CKEditor(app)
Bootstrap(app)
db.init_app(app)


import hashlib

CONTENT_POSTS_PATH = Path(app.root_path) / "content" / "generated_posts.json"
DEFAULT_AUTOMATION_AUTHOR_EMAIL = "ayncode@gmail.com"
DEFAULT_AUTOMATION_AUTHOR_NAME = "Ayotunde Oyeniyi"
DEFAULT_ADMIN_EMAIL = DEFAULT_AUTOMATION_AUTHOR_EMAIL
PASSWORD_RESET_SALT = "ayncoder-password-reset"


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


def get_or_create_automation_author():
    email = (os.environ.get("AUTO_POST_AUTHOR_EMAIL") or DEFAULT_AUTOMATION_AUTHOR_EMAIL).strip()
    name = (os.environ.get("AUTO_POST_AUTHOR_NAME") or DEFAULT_AUTOMATION_AUTHOR_NAME).strip()
    author = Users.query.filter_by(email=email).first()
    if author:
        return author

    password_seed = os.environ.get("AUTO_POST_AUTHOR_PASSWORD", os.urandom(24).hex())
    author = Users(
        email=email,
        name=name or DEFAULT_AUTOMATION_AUTHOR_NAME,
        password=generate_password_hash(password_seed, method="pbkdf2:sha256", salt_length=8),
    )
    db.session.add(author)
    db.session.commit()
    return author


def configured_admin_email():
    return (os.environ.get("ADMIN_EMAIL") or os.environ.get("AUTO_POST_AUTHOR_EMAIL") or DEFAULT_ADMIN_EMAIL).strip().lower()


def configured_admin_name():
    return (os.environ.get("ADMIN_NAME") or DEFAULT_AUTOMATION_AUTHOR_NAME).strip()


def is_admin_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    admin_email = configured_admin_email()
    user_email = (getattr(user, "email", "") or "").strip().lower()
    return bool(admin_email and user_email == admin_email)


def ensure_admin_user():
    password = (os.environ.get("ADMIN_PASSWORD") or os.environ.get("AUTO_POST_AUTHOR_PASSWORD") or "").strip()
    if not password:
        return None

    email = configured_admin_email()
    if not email:
        return None

    name = configured_admin_name()
    user = Users.query.filter_by(email=email).first()
    password_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=8)
    if not user:
        user = Users(email=email, name=name, password=password_hash)
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
    if changed:
        db.session.commit()
    return user


def sync_generated_content_posts():
    posts = load_generated_content_posts()
    if not posts:
        return 0

    author = get_or_create_automation_author()
    imported_count = 0
    required_fields = {"title", "subtitle", "body", "img_url", "date"}

    for post_data in posts:
        if not required_fields.issubset(post_data):
            app.logger.warning("Skipping generated post with missing fields: %s", post_data.get("title", "Untitled"))
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
    ensure_engagement_columns()
    ensure_admin_user()
    sync_generated_content_posts()


def is_safe_redirect_url(target):
    return bool(target) and target.startswith("/") and not target.startswith("//")


def password_reset_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])


def generate_password_reset_token(user):
    return password_reset_serializer().dumps(user.email, salt=PASSWORD_RESET_SALT)


def verify_password_reset_token(token):
    try:
        email = password_reset_serializer().loads(
            token,
            salt=PASSWORD_RESET_SALT,
            max_age=app.config["PASSWORD_RESET_MAX_AGE"],
        )
    except (BadSignature, SignatureExpired):
        return None
    return Users.query.filter_by(email=email).first()


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


def fetch_recent_events(query, limit=4, hours=None):
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

    if any(term in topic_lower for term in ("security", "review", "government", "safety", "model")):
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
    return {"date": date.today().year, "is_admin": is_admin_user(current_user)}


def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_user(current_user):
            abort(403, "You do not have permission to access this resource.")
        return f(*args, **kwargs)
    return decorated_function

login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):#This callback is used to reload the user object from the user ID stored in the session
    return db.session.get(Users, int(user_id))

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

    posts = decorate_posts(sort_posts_latest_first(posts_query.all()))
    stats = {
        "posts": BlogPost.query.count(),
        "comments": Comment.query.count(),
        "views": db.session.query(func.coalesce(func.sum(BlogPost.views), 0)).scalar(),
        "likes": db.session.query(func.coalesce(func.sum(BlogPost.likes), 0)).scalar(),
        "minutes": sum(post.reading_time for post in posts),
    }
    return render_template(
        "index.html",
        all_posts=posts,
        logged_in=current_user.is_authenticated,
        query=query,
        stats=stats,
    )


@app.route('/register',methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data
        user = Users.query.filter_by(email=email).first()
        if not user:
            password = form.password.data
            name= form.name.data
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=8)
            user = Users(email=email, password=hashed_password,name=name)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("get_all_posts"))
        flash("Email already exist.Please login")
        return redirect(url_for("login"))
    return render_template("register.html",form=form)



@app.route('/login',methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data
        user = Users.query.filter_by(email=email).first()
        if user:
            password = form.password.data
            validate_paswd = check_password_hash(user.password,password)

            if validate_paswd:
                login_user(user)#logs in user
                next_url = request.args.get("next")
                if is_safe_redirect_url(next_url):
                    return redirect(next_url)
                return redirect(url_for("get_all_posts"))
            flash('Wrong password. Please try again')
        else:
            flash('This email is not signed up yet register to login.')  #flashes msg on screen when email not found
            return redirect(url_for("register"))

    return render_template("login.html",form=form)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = Users.query.filter_by(email=form.email.data).first()
        if user:
            token = generate_password_reset_token(user)
            reset_url = url_for("reset_password", token=token, _external=True)
            if not send_password_reset_email(user, reset_url):
                flash("Password reset email is temporarily unavailable. Please try again later.")
                return render_template("forgot-password.html", form=form, logged_in=False)

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
        user.password = generate_password_hash(form.password.data, method="pbkdf2:sha256", salt_length=8)
        db.session.commit()
        flash("Password updated. I can log in with the new password now.")
        return redirect(url_for("login"))

    return render_template("reset-password.html", form=form, logged_in=False)



@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('get_all_posts'))


@app.route("/generated-cover/<audience>/<path:slug>.svg")
def generated_cover(audience, slug):
    topic = re.sub(r"-[a-f0-9]{10}$", "", slug)
    svg = render_topic_cover_svg(topic, audience)
    return Response(svg, mimetype="image/svg+xml")

@app.route("/post/<int:post_id>", methods=['GET', 'POST'])
def show_post(post_id):
    requested_post = db.get_or_404(BlogPost, post_id)
    if request.method == "GET" and not request.args.get("reacted"):
        requested_post.views = (requested_post.views or 0) + 1
        db.session.commit()
    decorate_posts([requested_post])
    comment_data = CommentForm()

    if comment_data.validate_on_submit():
        if not current_user.is_authenticated:
            flash('Please login or Register to comment')
            return redirect(url_for("login"))
        else:

            comment = Comment(
                text=comment_data.body.data,
                comment_author=current_user,
                parent_post=requested_post
            )

            db.session.add(comment)
            db.session.commit()
            return redirect(url_for("show_post", post_id=requested_post.id))

    return render_template("post.html", post=requested_post,
                           logged_in=current_user.is_authenticated,
                           form=comment_data)


@app.route("/post/<int:post_id>/react/<reaction>", methods=["POST"])
def react_to_post(post_id, reaction):
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


@app.route('/contact',methods=['GET','POST'])
def contact():
    confirm = False
    if request.method == 'POST':
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        num = request.form.get("phone", "").strip()
        msg = request.form.get("message", "").strip()
        password = os.environ.get('GMAIL_PASSWORD')
        my_email = os.environ.get('CONTACT_EMAIL', 'ayncode@gmail.com')

        if not password:
            flash('Contact service is temporarily unavailable. Please try again later.')
            return render_template("contact.html", logged_in=current_user.is_authenticated, confirm=False)

        with SMTP('smtp.gmail.com', 587) as smtp:
            smtp.starttls()
            smtp.login(my_email, password)
            smtp.sendmail(
                my_email,
                my_email,
                msg=f"Subject:{name or 'Website Contact'}\n\nNumber:{num}\n\nEmail from: {email}\n\n{msg}",
            )
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
            img_url=form.img_url.data or generate_topic_cover(form.topic.data, form.audience.data),
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
