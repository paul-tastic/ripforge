#!/usr/bin/env python3
"""
RipForge - Disc Ripping Solution
"""

from flask import Flask
from app.routes import main
from app import config
from app import ripper
from app import activity

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    app.secret_key = 'ripforge-secret-change-me'

    # Register blueprints
    app.register_blueprint(main)

    return app


if __name__ == '__main__':
    cfg = config.load_config()

    # Initialize the rip engine
    ripper.init_engine(cfg)
    print("  Rip engine initialized")
    activity.service_started()
    app = create_app()

    host = cfg.get('ripforge', {}).get('host', '0.0.0.0')
    port = cfg.get('ripforge', {}).get('port', 8081)

    print(f"""
    ╔═══════════════════════════════════════════════════╗
    ║                                                   ║
    ║   ██████╗ ██╗██████╗ ███████╗ ██████╗ ██████╗ ██╗███████╗   ║
    ║   ██╔══██╗██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██║██╔════╝   ║
    ║   ██████╔╝██║██████╔╝█████╗  ██║   ██║██████╔╝██║█████╗     ║
    ║   ██╔══██╗██║██╔═══╝ ██╔══╝  ██║   ██║██╔══██╗██║██╔══╝     ║
    ║   ██║  ██║██║██║     ██║     ╚██████╔╝██║  ██║██║███████╗   ║
    ║   ╚═╝  ╚═╝╚═╝╚═╝     ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝   ║
    ║                                                   ║
    ║   Disc Ripping Solution                           ║
    ║   v0.1.0                                          ║
    ║                                                   ║
    ╚═══════════════════════════════════════════════════╝

    Starting server on http://{host}:{port}
    """)

    app.run(host=host, port=port, debug=True)
