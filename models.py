# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.String(255), primary_key=True)  # use Google sub as id
    name = db.Column(db.String(150))
    email = db.Column(db.String(150), unique=True, nullable=False)
    picture = db.Column(db.String(300))

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    transcript = db.Column(db.Text)
    filepath = db.Column(db.String(1024))
    transcript_path = db.Column(db.String(1024))
    article = db.Column(db.Text)
    article_path = db.Column(db.String(1024))
    thumbnail_path = db.Column(db.String(1024))  # ✅ New field for extracted frame
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user_id = db.Column(db.String(255), db.ForeignKey('user.id'))
    user = db.relationship('User', backref='videos')
    video_metadata = db.Column(db.Text)  # <- renamed