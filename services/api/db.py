import os
import time
import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def _get_env(name: str, retries=5, delay=2) -> str:
    v = os.getenv(name)
    if v:
        return v
    for _ in range(retries):
        time.sleep(delay)
        v = os.getenv(name)
        if v:
            return v
    # fallback: try SSM parameter (optional)
    try:
        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION"))
        param = ssm.get_parameter(Name=f"/mortgage/{name.lower()}", WithDecryption=True)
        return param["Parameter"]["Value"]
    except Exception:
        raise RuntimeError(f"Missing env var: {name}")

def make_db_url() -> str:
    host = _get_env("DB_HOST")
    port = _get_env("DB_PORT")
    db   = _get_env("DB_NAME")
    user = _get_env("DB_USER")
    pw   = _get_env("DB_PASSWORD")
    # psycopg2
    return f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"

engine = create_engine(
    make_db_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
