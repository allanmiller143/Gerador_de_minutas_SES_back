import os
from pathlib import Path

from dotenv import load_dotenv
from app import create_app

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

app = create_app()

if __name__ == '__main__':
    host = os.getenv("FLASK_RUN_HOST") or os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT") or os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") not in {"0", "false", "False"}

    app.run(host=host, port=port, debug=debug)
