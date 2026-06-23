import os
import subprocess
import threading
import shutil
from datetime import datetime
from dotenv import load_dotenv

from flask import (Flask, render_template, redirect, url_for, request,
                   jsonify, abort, send_from_directory, flash)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename

# --- App Initialization & Configuration ---
load_dotenv()
app = Flask(__name__)

# Flask secret key for system-level operations (e.g., flash messages)
app.secret_key = os.getenv('SECRET_KEY', 'default-fallback-secret-key-for-dev')

# Base directory for storing application files and log outputs
DATA_BASE_DIR = os.path.join(app.root_path, 'user_data')
FILES_DIR = os.path.join(DATA_BASE_DIR, 'files')
LOGS_DIR = os.path.join(DATA_BASE_DIR, 'logs')

os.makedirs(FILES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Single log path for the bot instance
LOG_PATH = os.path.join(LOGS_DIR, 'bot.log')

# SQLite Local Storage
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(DATA_BASE_DIR, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Track the running bot process
running_process = None
process_lock = threading.Lock()


# --- Database Models ---
class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    main_file = db.Column(db.String(255), default='app.py')

    # Dummy attributes to prevent template errors if your frontend accesses 'user' fields
    @property
    def name(self):
        return "Administrator"

    @property
    def email(self):
        return "admin@local"

    @property
    def picture(self):
        return ""


# --- Helper Functions ---
def get_settings():
    """Retrieves or creates the single global settings record."""
    settings = Settings.query.get(1)
    if not settings:
        settings = Settings(id=1, main_file='app.py')
        db.session.add(settings)
        db.session.commit()
    return settings


# --- Core Application Routes ---
@app.route('/')
def dashboard():
    settings = get_settings()
    # Passing settings as 'user' to maintain compatibility with existing template logic
    return render_template('dashboard.html', user=settings)


@app.route('/files')
def files():
    settings = get_settings()
    file_list = []
    try:
        if os.path.exists(FILES_DIR):
            for item in sorted(os.listdir(FILES_DIR)):
                is_dir = os.path.isdir(os.path.join(FILES_DIR, item))
                file_list.append({'name': item, 'is_dir': is_dir})
    except OSError:
        flash("Could not read files directory.", "error")

    return render_template('files.html', user=settings, files=file_list)


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    settings = get_settings()
    if request.method == 'POST':
        main_file = request.form.get('main_file')
        if main_file:
            settings.main_file = secure_filename(main_file)
            db.session.commit()
            flash("Settings saved successfully!", "success")
        return redirect(url_for('profile'))
    return render_template('profile.html', user=settings)


# --- API Routes for Bot Control ---
def run_bot_process(command):
    """Thread target to execute the bot command in a subprocess."""
    global running_process
    try:
        with open(LOG_PATH, 'a') as log_file:
            # Use os.setsid to support killing subprocesses and child processes clean
            proc = subprocess.Popen(
                ['/bin/sh', '-c', command],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            with process_lock:
                running_process = proc
            proc.wait()
    except Exception as e:
        with open(LOG_PATH, 'a') as log_file:
            log_file.write(f"\n--- CRITICAL ERROR: Failed to start process ---\n{e}\n")
    finally:
        with process_lock:
            running_process = None


@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    global running_process
    with process_lock:
        if running_process and running_process.poll() is None:
            return jsonify({'status': 'error', 'message': 'Bot is already running.'}), 400

    settings = get_settings()
    req_file_path = os.path.join(FILES_DIR, 'requirements.txt')

    command = f"""
    cd "{FILES_DIR}"
    echo "--- System is starting up at $(date) ---" > "{LOG_PATH}"
    if [ -f "{req_file_path}" ]; then
        echo "--- Installing requirements from requirements.txt ---" >> "{LOG_PATH}"
        pip install -r "{req_file_path}" >> "{LOG_PATH}" 2>&1
    fi
    echo "--- Starting bot: python3 {settings.main_file} ---" >> "{LOG_PATH}"
    exec python3 -u "{settings.main_file}"
    """

    thread = threading.Thread(target=run_bot_process, args=(command,))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'success', 'message': 'Bot start sequence initiated.'})


@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    global running_process
    with process_lock:
        proc = running_process

    if proc and proc.poll() is None:
        try:
            # Send SIGTERM to the entire process group
            os.killpg(os.getpgid(proc.pid), 15)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                # Force kill if SIGTERM timeouts
                os.killpg(os.getpgid(proc.pid), 9)
            except ProcessLookupError:
                pass
        with process_lock:
            running_process = None
        return jsonify({'status': 'success', 'message': 'Bot stopped.'})
    return jsonify({'status': 'info', 'message': 'Bot was not running.'})


@app.route('/api/bot/restart', methods=['POST'])
def bot_restart():
    bot_stop()
    import time
    time.sleep(1)  # Brief pause to allow system resources to release
    return bot_start()


@app.route('/api/bot/logs')
def bot_logs():
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, 'r') as f:
                return f.read()
        return "No logs found. Start your bot to generate logs."
    except Exception as e:
        return f"Error reading logs: {e}"


@app.route('/api/bot/command', methods=['POST'])
def bot_command():
    return jsonify({'status': 'info', 'message': 'Direct command input is not yet implemented.'}), 501


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)