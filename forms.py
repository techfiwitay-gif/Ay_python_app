from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, URL
from flask_ckeditor import CKEditorField

##WTForm
class CreatePostForm(FlaskForm):
    title = StringField("Blog Post Title", validators=[DataRequired(), Length(max=250)])
    subtitle = StringField("Subtitle", validators=[DataRequired(), Length(max=250)])
    img_url = StringField("Blog Image URL", validators=[DataRequired(), URL()])
    author = StringField('Author', validators=[DataRequired(), Length(max=250)])
    body = CKEditorField("Blog Content", validators=[DataRequired()])
    submit = SubmitField("Submit Post")


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
    body = CKEditorField("Blog comment", validators=[DataRequired(), Length(max=5000)])
    submit = SubmitField("Submit Comment")
