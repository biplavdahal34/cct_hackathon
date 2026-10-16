"""Development entry point:  python run.py
Production (gunicorn) targets flask_app:app directly — see Dockerfile."""
import os

from flask_app import app

if __name__ == "__main__":
    # debug only when explicitly requested — never in deployment
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")