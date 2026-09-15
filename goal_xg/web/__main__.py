"""``python -m goal_xg.web`` → uvicorn on :8000."""

from goal_xg.web.app import run

if __name__ == "__main__":
    run()
