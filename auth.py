import os
from flask import redirect, url_for, jsonify, render_template
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from authlib.jose import jwt
from models import db, User
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def init_auth(app):
    # Initialize LoginManager
    login_manager = LoginManager(app)
    login_manager.login_view = "login"

    # OAuth (Google OpenID Connect)
    oauth = OAuth(app)
    google = oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )

    # User loader
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, user_id)

    # Authentication routes
    # @app.route("/")
    # def index():
    #     if current_user.is_authenticated:
    #         return redirect(url_for("upload"))
    #     return render_template("login.html")

    @app.route("/login")
    def login():
        redirect_uri = url_for("authorize", _external=True)
        return google.authorize_redirect(redirect_uri)

    @app.route("/authorize")
    def authorize():
        try:
            token = google.authorize_access_token()
            userinfo = token.get("userinfo")
            if not userinfo and token.get("id_token"):
                id_token = token["id_token"]
                userinfo = jwt.decode(id_token, key=None, claims_options={"iss": {"essential": False}})
            
            if not userinfo:
                return jsonify({"error": "No user info returned from Google"}), 400

            user = User.query.filter_by(email=userinfo["email"]).first()
            if not user:
                user = User(
                    id=userinfo["sub"],
                    name=userinfo.get("name"),
                    email=userinfo["email"],
                    picture=userinfo.get("picture"),
                )
                db.session.add(user)
                db.session.commit()

            login_user(user)
            return redirect(url_for("upload"))
        
        except Exception as e:
            print(f"[ERROR] Authorization failed: {e}")
            return jsonify({"error": "Authentication failed", "details": str(e)}), 400

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    return login_manager, oauth, google