from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, Regexp


USERNAME_VALIDATORS = [
    DataRequired(),
    Length(min=3, max=80),
    Regexp(
        r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        message="Use only letters, numbers, dots, hyphens, and underscores.",
    ),
]


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")


class SetupForm(FlaskForm):
    username = StringField(
        "Username", validators=USERNAME_VALIDATORS
    )
    email = StringField("Email", validators=[Optional(), Email()])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8)]
    )
    password_confirm = PasswordField(
        "Confirm Password", validators=[DataRequired(), EqualTo("password")]
    )


class CreateUserForm(FlaskForm):
    username = StringField(
        "Username", validators=USERNAME_VALIDATORS
    )
    email = StringField("Email", validators=[Optional(), Email()])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8)]
    )
    role = SelectField(
        "Role", choices=[("user", "User"), ("admin", "Admin"), ("guest", "Guest")]
    )
