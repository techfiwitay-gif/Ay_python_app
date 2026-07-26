from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from datetime import datetime


db = SQLAlchemy()


class Users(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(250), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False)
    name = db.Column(db.String(250), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="user")
    email_verified = db.Column(db.Boolean, nullable=False, default=True)
    email_verification_nonce = db.Column(db.String(64), nullable=True)
    password_reset_nonce = db.Column(db.String(64), nullable=True)
    is_disabled = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    posts = relationship("BlogPost", back_populates="author")
    comments = relationship("Comment", back_populates="comment_author")

    def __init__(
        self,
        email,
        password,
        name,
        role="user",
        email_verified=True,
        email_verification_nonce=None,
        password_reset_nonce=None,
        is_disabled=False,
    ):
        self.email = email
        self.password = password
        self.name = name
        self.role = role
        self.email_verified = email_verified
        self.email_verification_nonce = email_verification_nonce
        self.password_reset_nonce = password_reset_nonce
        self.is_disabled = is_disabled

    @property
    def is_active(self):
        return not self.is_disabled


class LoginThrottle(db.Model):
    __tablename__ = "login_throttles"

    id = db.Column(db.Integer, primary_key=True)
    bucket = db.Column(db.String(96), unique=True, nullable=False, index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_attempt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    blocked_until = db.Column(db.DateTime, nullable=True)


class BlogPost(db.Model):
    __tablename__ = "blog_posts"

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    author = relationship("Users", back_populates="posts")
    title = db.Column(db.String(250), unique=True, nullable=False)
    subtitle = db.Column(db.String(250), nullable=False)
    date = db.Column(db.String(250), nullable=False)
    published_at = db.Column(db.String(250), nullable=False, default="")
    body = db.Column(db.Text, nullable=False)
    img_url = db.Column(db.String(250), nullable=False)
    views = db.Column(db.Integer, nullable=False, default=0)
    likes = db.Column(db.Integer, nullable=False, default=0)
    upvotes = db.Column(db.Integer, nullable=False, default=0)
    downvotes = db.Column(db.Integer, nullable=False, default=0)
    comments = relationship(
        "Comment",
        back_populates="parent_post",
        cascade="all, delete-orphan",
    )

    def __init__(self, title, subtitle, body, img_url, author, date, published_at=""):
        self.title = title
        self.subtitle = subtitle
        self.body = body
        self.img_url = img_url
        self.author = author
        self.date = date
        self.published_at = published_at or date
        self.views = 0
        self.likes = 0
        self.upvotes = 0
        self.downvotes = 0


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    comment_author = relationship("Users", back_populates="comments")
    post_id = db.Column(db.Integer, db.ForeignKey("blog_posts.id", ondelete="CASCADE"))
    parent_post = relationship("BlogPost", back_populates="comments")
    parent_id = db.Column(db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    parent = relationship("Comment", remote_side=[id], back_populates="replies")
    replies = relationship(
        "Comment",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Comment.created_at",
    )
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __init__(self, text, comment_author, parent_post, parent=None):
        self.text = text
        self.comment_author = comment_author
        self.parent_post = parent_post
        self.parent = parent


class DeletedGeneratedPost(db.Model):
    __tablename__ = "deleted_generated_posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(250), unique=True, nullable=False)
    slug = db.Column(db.String(250), unique=True, nullable=True)
    deleted_at = db.Column(db.String(250), nullable=False)

    def __init__(self, title, slug="", deleted_at=""):
        self.title = title
        self.slug = slug or None
        self.deleted_at = deleted_at
