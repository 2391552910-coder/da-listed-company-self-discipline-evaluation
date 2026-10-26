"""Opt-in loopback deployment fixture. Never imported by production evaluation."""
import argparse
from http.server import ThreadingHTTPServer


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic deployment acceptance only; not a real model")
    parser.add_argument("--acknowledge-synthetic-fixture", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=23991)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("fixture port must be between 1024 and 65535")
    from scripts.seed_browser_acceptance import FixtureProvider
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FixtureProvider)
    server.daemon_threads = True
    print("SYNTHETIC DEPLOYMENT FIXTURE: not a research model; loopback only", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
