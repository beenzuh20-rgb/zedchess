import os


class Config:
    SECRET_KEY = os.urandom(24)  # Random secret key on each run (fine for dev)
    DEBUG = True
