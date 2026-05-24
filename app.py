import os
import json
import time
import threading
import requests
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)

# =========================
# CONFIG
# =========================

UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'results'

ALLOWED_EXTENSIONS = {'txt', 'm3u'}

MAX_WORKERS = 2
TIMEOUT = 10
RATE_LIMIT_DELAY = 1

Path(UPLOAD_FOLDER).mkdir(exist_ok=True)
Path(RESULTS_FOLDER).mkdir(exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

API_URL = "https://toppos.xyz/api/check-status"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}

# =========================
# SESSION
# =========================

session = requests.Session()

# =========================
# STATE
# =========================

checking_state = {
    'is_checking': False,
    'progress': 0,
    'total': 0,
    'results': [],
    'errors': [],
    'current_url': '',
    'status_message': '',
    'result_file': None
}

state_lock = threading.Lock()

# =========================
# ALLOWED FILE
# =========================

def allowed_file(filename):

    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )

# =========================
# PARSE FILE
# =========================

def parse_m3u_file(filepath):

    urls = []

    with open(
        filepath,
        'r',
        encoding='utf-8',
        errors='ignore'
    ) as f:

        for line in f:

            line = line.strip()

            if (
                line and
                not line.startswith('#')
            ):

                if (
                    line.startswith('http://') or
                    line.startswith('https://')
                ):

                    urls.append(line)

    return urls

# =========================
# CHECK IPTV
# =========================

def check_iptv_url(url, index, total):

    result = None

    try:

        with state_lock:

            checking_state['current_url'] = url

            checking_state['status_message'] = (
                f"Checking {index}/{total}"
            )

        response = session.post(
            API_URL,
            json={"url": url},
            headers=HEADERS,
            timeout=TIMEOUT
        )

        if response.status_code != 200:

            return {
                'url': url,
                'success': False,
                'error': 'Bad status code'
            }, index

        try:

            data = response.json()

        except:

            return {
                'url': url,
                'success': False,
                'error': 'Invalid JSON'
            }, index

        user_info = data.get('user_info', {})

        if user_info.get('auth') == 1:

            exp = user_info.get('exp_date', '0')

            try:

                exp_date = datetime.fromtimestamp(
                    int(exp)
                )

            except:

                exp_date = 'Unknown'

            result = {
                'url': url,
                'username': user_info.get(
                    'username',
                    'N/A'
                ),
                'status': user_info.get(
                    'status',
                    'N/A'
                ),
                'exp_date': str(exp_date),
                'success': True
            }

        else:

            result = {
                'url': url,
                'success': False,
                'error': 'Dead account'
            }

    except requests.exceptions.Timeout:

        result = {
            'url': url,
            'success': False,
            'error': 'Timeout'
        }

    except requests.exceptions.ConnectionError:

        result = {
            'url': url,
            'success': False,
            'error': 'Connection error'
        }

    except Exception as e:

        result = {
            'url': url,
            'success': False,
            'error': str(e)
        }

    time.sleep(RATE_LIMIT_DELAY)

    return result, index

# =========================
# PROCESS URLS
# =========================

def process_urls(urls, session_id):

    results = []
    errors = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for index, url in enumerate(urls, 1):

            future = executor.submit(
                check_iptv_url,
                url,
                index,
                len(urls)
            )

            futures[future] = index

        for future in as_completed(futures):

            try:

                result, index = future.result()

                with state_lock:

                    checking_state['progress'] += 1

                if result.get('success'):

                    results.append(result)

                else:

                    errors.append(result)

            except Exception as e:

                errors.append({
                    'url': 'Unknown',
                    'success': False,
                    'error': str(e)
                })

    return results, errors

# =========================
# SAVE RESULTS
# =========================

def save_results(results, session_id):

    filename = (
        f"results_{session_id}.txt"
    )

    filepath = os.path.join(
        RESULTS_FOLDER,
        filename
    )

    with open(
        filepath,
        'w',
        encoding='utf-8'
    ) as f:

        f.write("=" * 70 + "\n")
        f.write("IPTV CHECKER RESULTS\n")
        f.write("=" * 70 + "\n\n")

        for result in results:

            f.write(
                f"URL: {result['url']}\n"
            )

            f.write(
                f"USER: {result['username']}\n"
            )

            f.write(
                f"STATUS: {result['status']}\n"
            )

            f.write(
                f"EXP: {result['exp_date']}\n"
            )

            f.write(
                "-" * 70 + "\n"
            )

    return filename

# =========================
# HOME
# =========================

@app.route('/')

def home():

    return render_template(
        'index.html'
    )

# =========================
# CHECK API
# =========================

@app.route(
    '/api/check',
    methods=['POST']
)

def check_iptv():

    if (
        'file'
        not in request.files
    ):

        return jsonify({
            'error': 'No file'
        }), 400

    file = request.files['file']

    if file.filename == '':

        return jsonify({
            'error': 'No selected file'
        }), 400

    if not allowed_file(file.filename):

        return jsonify({
            'error': 'Invalid file type'
        }), 400

    try:

        with state_lock:

            if checking_state['is_checking']:

                return jsonify({
                    'error': 'Already checking'
                }), 429

            checking_state['is_checking'] = True
            checking_state['progress'] = 0
            checking_state['results'] = []
            checking_state['errors'] = []
            checking_state['result_file'] = None

        filename = secure_filename(
            file.filename
        )

        session_id = int(
            time.time()
        )

        filepath = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        file.save(filepath)

        urls = parse_m3u_file(filepath)

        if not urls:

            with state_lock:

                checking_state['is_checking'] = False

            return jsonify({
                'error': 'No URLs found'
            }), 400

        checking_state['total'] = len(urls)

        # =========================
        # BACKGROUND
        # =========================

        def background_task():

            try:

                results, errors = process_urls(
                    urls,
                    session_id
                )

                checking_state['results'] = results
                checking_state['errors'] = errors

                result_file = save_results(
                    results,
                    session_id
                )

                checking_state[
                    'result_file'
                ] = result_file

            finally:

                checking_state[
                    'is_checking'
                ] = False

        threading.Thread(
            target=background_task,
            daemon=True
        ).start()

        return jsonify({
            'status': 'started',
            'total': len(urls)
        })

    except Exception as e:

        checking_state[
            'is_checking'
        ] = False

        return jsonify({
            'error': str(e)
        }), 500

# =========================
# PROGRESS
# =========================

@app.route('/api/progress')

def progress():

    return jsonify(checking_state)

# =========================
# DOWNLOAD
# =========================

@app.route('/download/<filename>')

def download(filename):

    path = os.path.join(
        RESULTS_FOLDER,
        filename
    )

    return send_file(
        path,
        as_attachment=True
    )

# =========================
# RUN
# =========================

if __name__ == '__main__':

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False
    )