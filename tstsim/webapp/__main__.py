"""python -m tstsim.webapp [--host 0.0.0.0] [--port 8000]"""
import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print(f"Open http://localhost:{a.port} in your browser")
    uvicorn.run("tstsim.webapp.server:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
