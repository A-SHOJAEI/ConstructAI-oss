"""Open all demo-relevant browser tabs."""
import time
import webbrowser

URLS = [
    ("Frontend", "http://localhost:3000"),
    ("EVM Dashboard", "http://localhost:3000/controls"),
    ("Safety Dashboard", "http://localhost:3000/safety"),
    ("Quality Dashboard", "http://localhost:3000/quality"),
    ("Portfolio", "http://localhost:3000/portfolio"),
    ("API Docs", "http://localhost:8000/docs"),
    ("pgAdmin", "http://localhost:5050"),
    ("Kafka UI", "http://localhost:8080"),
    ("MinIO Console", "http://localhost:9001"),
]

if __name__ == "__main__":
    for name, url in URLS:
        print(f"Opening {name}: {url}")
        webbrowser.open(url)
        time.sleep(0.5)
