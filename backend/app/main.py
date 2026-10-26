"""Stage 2 application. Legacy algorithm routes remain in source for later migration."""
from app.documents.api import create_app
app=create_app()
