
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, LoginManager, login_required, current_user, logout_user
from forms import CommentForm, CreatePostForm, GenerateArticleForm, LoginForm, RegisterForm
from functools import wraps
from models import BlogPost, Comment, Users, db
from sqlalchemy import func, inspect, or_, text
import os
import re
from smtplib import SMTP
from html import escape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

app = Flask(__name__, static_url_path='/static')
database_url = os.environ.get("DATABASE_URL", "sqlite:///ayblog.db")
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


with app.app_context():
    db.create_all()
    ensure_engagement_columns()


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


def fetch_recent_events(query, limit=4):
    search_query = quote_plus(query.strip())
    feed_url = f"https://news.google.com/rss/search?q={search_query}&hl=en-US&gl=US&ceid=US:en"
    request_obj = Request(feed_url, headers={"User-Agent": "AyNcodeArticleGenerator/1.0"})
    with urlopen(request_obj, timeout=8) as response:
        feed = response.read()

    root = ET.fromstring(feed)
    events = []
    for item in root.findall("./channel/item")[:limit]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        published = item.findtext("pubDate", "").strip()
        source = item.findtext("source", "").strip()
        if title and link:
            events.append(
                {
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": source or "Google News",
                }
            )
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

    subtitle = f"A clear, useful breakdown of {clean_topic} for {audience_text}."
    event_section = render_event_section(events or [])
    event_reference = ""
    if events:
        event_reference = "<p>Recent headlines show that this topic is not theoretical. The linked examples below give the article a real-world starting point, while the analysis focuses on practical lessons rather than guessing at facts not shown in the sources.</p>"

    body = f"""
<p>{safe_topic} is easier to understand when it is treated as a practical workflow instead of a vague idea. The goal is not to chase every tool or trend, but to make steady decisions that improve the way people build, learn, and ship.</p>

<h2>Why it matters</h2>
<p>For {audience_text}, {safe_topic_lower} matters because it connects daily execution with long-term progress. A good process reduces guesswork, makes tradeoffs visible, and helps teams move from scattered effort to repeatable outcomes.</p>

{event_reference}

{event_section}

{angle_paragraph}

<h2>Start with the problem</h2>
<p>Before choosing a solution, define the problem in plain language. Ask what is slow, confusing, risky, or expensive today. A strong article, product, or technical plan usually starts with one specific pain point and builds outward from there.</p>

<h2>Build a simple first version</h2>
<p>The first version should prove the core idea with the fewest moving parts. Keep the scope small, document what changed, and make sure the result can be tested by a real person. This makes improvement easier because feedback arrives early.</p>

<h2>Measure what improves</h2>
<p>Useful progress needs evidence. Track whether the work saves time, improves clarity, reduces errors, or creates a better experience for users. When the result is measurable, it becomes easier to decide what to keep, remove, or refine.</p>

<h2>Final thought</h2>
<p>{safe_topic} works best when it stays grounded in real needs. Start with a focused problem, ship a small improvement, and use what you learn to guide the next version.</p>
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
    openclaw_url = os.environ.get('OPENCLAW_URL', 'https://ayncode.com')
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
            date=date.today().strftime("%B %d, %Y")
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
            img_url=form.img_url.data or form.img_url.default,
            author=current_user,
            date=date.today().strftime("%B %d, %Y")
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
