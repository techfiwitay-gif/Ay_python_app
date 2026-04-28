from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, URL
from flask_ckeditor import CKEditorField

##WTForm
class CreatePostForm(FlaskForm):
    title = StringField("Blog Post Title", validators=[DataRequired(), Length(max=250)])
    subtitle = StringField("Subtitle", validators=[DataRequired(), Length(max=250)])
    img_url = StringField("Blog Image URL", validators=[DataRequired(), URL()])
    author = StringField('Author', validators=[DataRequired(), Length(max=250)])
    body = CKEditorField("Blog Content", validators=[DataRequired()])
    submit = SubmitField("Submit Post")


class GenerateArticleForm(FlaskForm):
    topic = StringField("Article Topic", validators=[DataRequired(), Length(max=180)])
    audience = SelectField(
        "Audience",
        choices=[
            ("developers", "Developers"),
            ("founders", "Founders"),
            ("beginners", "Beginners"),
            ("general", "General readers"),
        ],
        default="developers",
    )
    angle = TextAreaField(
        "Article Direction",
        validators=[Optional(), Length(max=600)],
        description="Optional notes, keywords, or points you want included.",
    )
    event_query = StringField(
        "Real Event Search",
        validators=[Optional(), Length(max=180)],
        description="Optional search phrase for current news. Defaults to the article topic.",
    )
    use_real_events = BooleanField("Use recent real-world events", default=True)
    img_url = StringField(
        "Cover Image URL",
        validators=[Optional(), URL(), Length(max=250)],
        default="https://images.unsplash.com/photo-1515879218367-8466d910aaa4?auto=format&fit=crop&w=1400&q=80",
    )
    submit = SubmitField("Generate Article")


class RegisterForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=250)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8, max=250)])
    name = StringField('Name', validators=[DataRequired(), Length(max=250)])
    sign_up = SubmitField('Create Account')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=250)])
    password = PasswordField('Password', validators=[DataRequired(), Length(max=250)])
    login = SubmitField("Log in")


class CommentForm(FlaskForm):
    body = TextAreaField("Comment", validators=[DataRequired(), Length(max=2000)])
    submit = SubmitField("Submit Comment")
