from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, PasswordField, Form
from wtforms.validators import DataRequired, URL,Email
from flask_ckeditor import CKEditorField

##WTForm
class CreatePostForm(FlaskForm):
    title = StringField("Blog Post Title", validators=[DataRequired()])
    subtitle = StringField("Subtitle", validators=[DataRequired()])
    img_url = StringField("Blog Image URL", validators=[DataRequired(), URL()])
    author = StringField('Author', validators=[DataRequired()])
    body = CKEditorField("Blog Content", validators=[DataRequired()])
    submit = SubmitField("Submit Post")


class RegisterForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired()])
    password = PasswordField('password',validators=[DataRequired()])
    name = StringField('name', validators=[DataRequired()])
    sign_up = SubmitField('sign me up')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired()])
    password = PasswordField('password', validators=[DataRequired()])
    login = SubmitField("Log in")


class CommentForm(FlaskForm):
    body = CKEditorField("Blog comment", validators=[DataRequired()])
    submit = SubmitField("Submit Comment")