# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.String(255), primary_key=True)  # Google sub or custom ID
    name = db.Column(db.String(150))
    email = db.Column(db.String(150), unique=True, nullable=False)
    picture = db.Column(db.String(300))

    # Credit system
    credits = db.Column(db.Integer, default=5)  # 1 credit = 1 minute of video

    # Relationships
    videos = db.relationship('Video', back_populates='user', cascade="all, delete-orphan")
    payments = db.relationship('Payment', back_populates='user', cascade="all, delete-orphan")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def has_enough_credits(self, video_duration_minutes):
        return self.credits >= video_duration_minutes

    def deduct_credits(self, video_duration_minutes):
        if self.has_enough_credits(video_duration_minutes):
            self.credits -= video_duration_minutes
            return True
        return False

    def add_credits(self, amount):
        self.credits += int(amount)

    def __repr__(self):
        return f"<User {self.name or self.email} - Credits: {self.credits}>"


class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(1024), nullable=False)
    duration = db.Column(db.Float, nullable=False)
    transcript = db.Column(db.Text)
    transcript_path = db.Column(db.String(1024))
    article = db.Column(db.Text)
    article_path = db.Column(db.String(1024))
    thumbnail_path = db.Column(db.String(1024))
    video_metadata = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Link to user
    user_id = db.Column(db.String(255), db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', back_populates='videos')

    def __repr__(self):
        return f"<Video {self.filename} - User: {self.user_id}>"


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), db.ForeignKey('user.id'), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)  # amount paid
    credits_added = db.Column(db.Integer, nullable=False)  # credits added
    payment_provider_id = db.Column(db.String(255), nullable=False)  # Stripe ID
    status = db.Column(db.String(50), default="pending")  # pending, succeeded, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='payments')

    def __repr__(self):
        return f"<Payment {self.id} User:{self.user_id} Credits:{self.credits_added} Status:{self.status}>"
