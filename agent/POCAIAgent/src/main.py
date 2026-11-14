"""uvicorn main:app  (run from ./src)"""
from presentation.api import create_app

app = create_app()
