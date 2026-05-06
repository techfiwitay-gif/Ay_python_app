
from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, LoginManager, login_required, current_user, logout_user
from forms import CommentForm, CreatePostForm, GenerateArticleForm, LoginForm, RegisterForm
from functools import wraps
from models import BlogPost, Comment, Users, db
from sqlalchemy import func, inspect, or_, text
import os
import json
import re
from pathlib import Path
from smtplib import SMTP
from html import escape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

app = Flask(__name__, static_url_path='/static')
default_database_url = "sqlite:////tmp/ayblog.db" if os.environ.get("VERCEL") else "sqlite:///ayblog.db"
database_url = os.environ.get("DATABASE_URL", default_database_url)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config.from_mapping(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key"),
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
ckeditor = CKEditor(app)
Bootstrap(app)
db.init_app(app)


import hashlib

CONTENT_POSTS_PATH = Path(app.root_path) / "content" / "generated_posts.json"
DEFAULT_AUTOMATION_AUTHOR_EMAIL = "ayncode@gmail.com"
DEFAULT_AUTOMATION_AUTHOR_NAME = "Ayotunde Oyeniyi"


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
                    text(f"ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS {column_name} INTEGER NOT NULL DEFAULT 0")
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
    email = os.environ.get("AUTO_POST_AUTHOR_EMAIL", DEFAULT_AUTOMATION_AUTHOR_EMAIL).strip()
    name = os.environ.get("AUTO_POST_AUTHOR_NAME", DEFAULT_AUTOMATION_AUTHOR_NAME).strip()
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
        if BlogPost.query.filter_by(title=post_data["title"]).first():
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
    sync_generated_content_posts()


def is_safe_redirect_url(target):
    return bool(target) and target.startswith("/") and not target.startswith("//")


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
    safe_topic_text = escape(title_case_topic(topic.replace("-", " ")))
    safe_audience = escape(audience.replace("-", " ").title())
    initials = escape(topic_initials(topic))
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
  <text x="150" y="440" fill="#ffffff" font-family="Inter, Arial, sans-serif" font-size="86" font-weight="900">{safe_topic_text}</text>
  <text x="150" y="535" fill="#ffffff" opacity="0.82" font-family="Inter, Arial, sans-serif" font-size="36" font-weight="600">Generated cover for a focused article draft</text>
  <text x="1210" y="695" fill="#ffffff" opacity="0.9" font-family="Inter, Arial, sans-serif" font-size="148" font-weight="900">{initials}</text>
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
<h2>Recent real-world context</h2>
<p>The points below are grounded in recent public headlines related to this topic. They should be reviewed before publishing because live events can change quickly.</p>
<ul>
{''.join(event_items)}
</ul>
""".strip()


def generate_article(topic, audience, angle, events=None):
    clean_topic = re.sub(r"\s+", " ", topic).strip()
    safe_topic = escape(clean_topic)
    safe_topic_lower = escape(clean_topic.lower())
    title_topic = title_case_topic(clean_topic)
    title = unique_post_title(f"{title_topic}: A Practical Guide")
    audience_labels = {
        "developers": "developers who want practical steps",
        "founders": "founders turning ideas into useful products",
        "beginners": "beginners learning the fundamentals",
        "general": "curious readers who want a clear overview",
    }
    audience_text = audience_labels.get(audience, audience_labels["general"])
    angle_text = re.sub(r"\s+", " ", angle or "").strip()
    angle_paragraph = ""
    if angle_text:
        angle_paragraph = f"<p>This article focuses on {escape(angle_text)}. Use that lens to decide what matters, what can wait, and what should be measured after launch.</p>"

    subtitle = f"My practical read on {clean_topic}, with notes for {audience_text}."
    event_section = render_event_section(events or [])
    event_reference = ""
    if events:
        event_reference = "<p>Recent headlines show that this topic is not theoretical. The linked examples below give the article a real-world starting point, while the analysis focuses on practical lessons rather than guessing at facts not shown in the sources.</p>"

    body = f"""
<p>I find {safe_topic} easier to understand when I treat it as a practical workflow instead of a vague idea. I am not trying to chase every tool or trend. I am looking for the steady decisions that improve how people build, learn, and ship.</p>

<h2>Why it matters</h2>
<p>My read is that {safe_topic_lower} matters because it connects daily execution with long-term progress. A good process reduces guesswork, makes tradeoffs visible, and helps teams move from scattered effort to repeatable outcomes.</p>

{event_reference}

{event_section}

{angle_paragraph}

<h2>Start with the problem</h2>
<p>I like to define the problem in plain language before choosing a solution. The useful question is what feels slow, confusing, risky, or expensive today. A strong article, product, or technical plan usually starts with one specific pain point and builds outward from there.</p>

<h2>Build a simple first version</h2>
<p>My first version usually needs to prove the core idea with the fewest moving parts. I keep the scope small, document what changed, and make sure the result can be tested by a real person. That makes improvement easier because feedback arrives early.</p>

<h2>Measure what improves</h2>
<p>Useful progress needs evidence. I care about whether the work saves time, improves clarity, reduces errors, or creates a better experience. When the result is measurable, it becomes easier to decide what to keep, remove, or refine.</p>

<h2>Final thought</h2>
<p>{safe_topic} works best when it stays grounded in real needs. I start with a focused problem, ship a small improvement, and use what I learn to guide the next version.</p>
""".strip()
    return title, subtitle, body


def gravatar_url(email, size=100, rating='g', default='retro'):
    hash_value = hashlib.md5(email.strip().lower().encode('utf-8')).hexdigest()
    return f"https://www.gravatar.com/avatar/{hash_value}?s={size}&d={default}&r={rating}"

app.jinja_env.filters['gravatar'] = gravatar_url


@app.context_processor
def inject_template_globals():
    return {"date": date.today().year}


def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If id is not 1 then return abort with 403 error
        if not current_user.is_authenticated or current_user.id != 1:
            abort(403, "You do not have permission to access this resource.")
        # Otherwise continue with the route function
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

    posts = decorate_posts(posts_query.order_by(BlogPost.id.desc()).all())
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
