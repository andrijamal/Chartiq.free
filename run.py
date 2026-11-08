import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    dev_mode = os.environ.get("DEV", "0").strip().lower() in {"1", "true", "yes", "on"}

    print("=" * 60)
    print("🚀 Chartiq Free is starting...")
    print(f"📊 Buka browser Anda di: http://{host}:{port}")
    print("=" * 60)
    uvicorn.run("app:app", host=host, port=port, reload=dev_mode)

