"""Local development entry point.

Usage:
    python main.py
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, log_level="info", reload=False)
