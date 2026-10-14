import argparse
import uvicorn


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    print("==================================================================")
    print("🌌 SkyMosaic Multi-Camera AllSky System")
    print(f"📡 Web Dashboard & Live Sky Dome: http://localhost:{port}")
    print(f"📹 MJPEG Live Stream: http://localhost:{port}/api/sky/stream")
    print(f"🎯 Celestial Calibration Studio: http://localhost:{port}/#tab-calibration")
    print("==================================================================")
    uvicorn.run("backend.api.app:app", host=host, port=port, reload=reload)


def main():
    parser = argparse.ArgumentParser(description="SkyMosaic Multi-Camera AllSky System")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind (default: 8080)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
