from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import csv
import io
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'trades.db'
UPLOAD_DIR = BASE_DIR / 'static' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-in-production')
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    initial_balance REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('Long','Short')),
    setup TEXT NOT NULL,
    criteria_met TEXT,
    market_condition TEXT NOT NULL,
    planned_rr REAL NOT NULL DEFAULT 0,
    realised_rr REAL NOT NULL DEFAULT 0,
    rules_followed INTEGER NOT NULL DEFAULT 0,
    exit_reason TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    quantity REAL,
    pnl REAL NOT NULL DEFAULT 0,
    notes TEXT,
    screenshot_4h TEXT,
    screenshot_15m TEXT,
    screenshot_5m TEXT,
    created_at TEXT NOT NULL
);
'''


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(trades)').fetchall()}
        if 'user_id' not in columns:
            conn.execute('ALTER TABLE trades ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE')
        for column in ('screenshot_4h', 'screenshot_15m', 'screenshot_5m'):
            if column not in columns:
                conn.execute(f'ALTER TABLE trades ADD COLUMN {column} TEXT')
        user_columns = {row['name'] for row in conn.execute('PRAGMA table_info(users)').fetchall()}
        if 'initial_balance' not in user_columns:
            conn.execute('ALTER TABLE users ADD COLUMN initial_balance REAL NOT NULL DEFAULT 0')


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    with get_db() as conn:
        return conn.execute('SELECT id, name, email, initial_balance FROM users WHERE id = ?', (user_id,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_user_trades(user_id, order='trade_date DESC, id DESC'):
    with get_db() as conn:
        return conn.execute(f'SELECT * FROM trades WHERE user_id = ? ORDER BY {order}', (user_id,)).fetchall()


@app.context_processor
def inject_user():
    return {'logged_in_user': current_user()}


@app.template_filter('money')
def money(value):
    return f"${float(value or 0):,.2f}"


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not name or not email or len(password) < 8:
            flash('Enter your name, a valid email, and a password of at least 8 characters.', 'error')
            return render_template('register.html')
        try:
            with get_db() as conn:
                initial_balance = float(request.form.get('initial_balance') or 0)
                if initial_balance < 0:
                    raise ValueError('Initial balance cannot be negative.')
                conn.execute('INSERT INTO users (name,email,password_hash,initial_balance,created_at) VALUES (?,?,?,?,?)',
                             (name, email, generate_password_hash(password), initial_balance, datetime.now(timezone.utc).isoformat()))
                user_id = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()['id']
                # One-time migration path for rows created before authentication existed.
                conn.execute('UPDATE trades SET user_id = ? WHERE user_id IS NULL', (user_id,))
            session.clear()
            session['user_id'] = user_id
            flash('Your private journal is ready.', 'success')
            return redirect(url_for('index'))
        except ValueError:
            flash('Initial balance must be a non-negative number.', 'error')
        except sqlite3.IntegrityError:
            flash('An account with that email already exists.', 'error')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with get_db() as conn:
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            return redirect(request.args.get('next') or url_for('index'))
        flash('Email or password is incorrect.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


def save_screenshot(upload, user_id, trade_id, timeframe):
    if not upload or not upload.filename:
        return None
    filename = secure_filename(upload.filename)
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('Screenshots must be PNG, JPG, JPEG, or WEBP files.')
    stored_name = f'user_{user_id}_trade_{trade_id}_{timeframe}.{extension}'
    upload.save(UPLOAD_DIR / stored_name)
    return f'uploads/{stored_name}'


@app.route('/')
@login_required
def index():
    user = current_user()
    trades = get_user_trades(user['id'])
    return render_template('index.html', trades=trades, today=datetime.now().strftime('%Y-%m-%dT%H:%M'))


@app.route('/trade', methods=['POST'])
@login_required
def add_trade():
    form = request.form
    try:
        trade_date = form['trade_date'].replace('T', ' ')
        user_id = current_user()['id']
        with get_db() as conn:
            cursor = conn.execute('''INSERT INTO trades
                (user_id,trade_date,symbol,direction,setup,criteria_met,market_condition,planned_rr,realised_rr,
                 rules_followed,exit_reason,entry_price,exit_price,quantity,pnl,notes,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                user_id, trade_date, form['symbol'].strip().upper(), form['direction'], form['setup'],
                form.get('criteria_met',''), form['market_condition'], float(form.get('planned_rr') or 0),
                float(form.get('realised_rr') or 0), 1 if form.get('rules_followed') == 'yes' else 0,
                form['exit_reason'], float(form.get('entry_price') or 0), float(form.get('exit_price') or 0),
                float(form.get('quantity') or 0), float(form.get('pnl') or 0), form.get('notes',''),
                datetime.now(timezone.utc).isoformat(timespec='seconds')))
            trade_id = cursor.lastrowid
            screenshot_paths = [
                save_screenshot(request.files.get('screenshot_4h'), user_id, trade_id, '4h'),
                save_screenshot(request.files.get('screenshot_15m'), user_id, trade_id, '15m'),
                save_screenshot(request.files.get('screenshot_5m'), user_id, trade_id, '5m'),
            ]
            conn.execute('UPDATE trades SET screenshot_4h = ?, screenshot_15m = ?, screenshot_5m = ? WHERE id = ?',
                         (*screenshot_paths, trade_id))
        flash('Trade logged successfully.', 'success')
    except (KeyError, ValueError) as exc:
        flash(f'Please check the required fields: {exc}', 'error')
    return redirect(url_for('index'))


@app.route('/trade/<int:trade_id>/delete', methods=['POST'])
@login_required
def delete_trade(trade_id):
    with get_db() as conn:
        trade = conn.execute('SELECT screenshot_4h, screenshot_15m, screenshot_5m FROM trades WHERE id = ? AND user_id = ?', (trade_id, current_user()['id'])).fetchone()
        conn.execute('DELETE FROM trades WHERE id = ? AND user_id = ?', (trade_id, current_user()['id']))
    if trade:
        for path in (trade['screenshot_4h'], trade['screenshot_15m'], trade['screenshot_5m']):
            if path:
                (BASE_DIR / 'static' / path).unlink(missing_ok=True)
    flash('Trade deleted.', 'success')
    return redirect(url_for('index'))


@app.route('/account/balance', methods=['POST'])
@login_required
def update_balance():
    try:
        initial_balance = float(request.form.get('initial_balance') or 0)
        if initial_balance < 0:
            raise ValueError
        with get_db() as conn:
            conn.execute('UPDATE users SET initial_balance = ? WHERE id = ?', (initial_balance, current_user()['id']))
        flash('Initial account balance updated.', 'success')
    except ValueError:
        flash('Initial balance must be a non-negative number.', 'error')
    return redirect(url_for('index'))


def build_analytics(trades, initial_balance=0):
    trades = [dict(row) for row in trades]
    pnl_total = sum(t['pnl'] for t in trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] < 0]
    rules_yes = [t for t in trades if t['rules_followed']]
    rules_no = [t for t in trades if not t['rules_followed']]
    def summary(items):
        pnl = sum(t['pnl'] for t in items)
        return {'count': len(items), 'pnl': round(pnl, 2), 'win_rate': round((sum(1 for t in items if t['pnl'] > 0) / len(items) * 100) if items else 0, 1)}
    def counts(items, field):
        result = {}
        for item in items:
            value = item.get(field) or 'Not specified'
            result[value] = result.get(value, 0) + 1
        return sorted([{'label': k, 'count': v} for k, v in result.items()], key=lambda x: (-x['count'], x['label']))
    running, equity = initial_balance, []
    equity.append({'date': 'Starting balance', 'value': round(initial_balance, 2)})
    for t in sorted(trades, key=lambda x: (x['trade_date'], x['id'])):
        running += t['pnl']
        equity.append({'date': t['trade_date'][:10], 'value': round(running, 2)})
    mistakes = {
        'rule_breaks': len(rules_no),
        'rule_break_loss': round(sum(t['pnl'] for t in rules_no if t['pnl'] < 0), 2),
        'manual_close_breaks': sum(1 for t in rules_no if t['exit_reason'] == 'Manual close'),
        'early_exit_breaks': sum(1 for t in rules_no if t['exit_reason'] in ('Manual close', 'Breakeven')),
        'top_setups': counts(rules_no, 'setup')[:3],
        'top_conditions': counts(rules_no, 'market_condition')[:3],
        'top_exit_reasons': counts(rules_no, 'exit_reason')[:3],
    }
    return {'trades': trades, 'initial_balance': round(initial_balance, 2), 'current_equity': round(initial_balance + pnl_total, 2), 'total_pnl': round(pnl_total, 2), 'trade_count': len(trades),
            'win_rate': round((len(wins) / len(trades) * 100) if trades else 0, 1),
            'avg_rr': round(sum(t['realised_rr'] for t in trades) / len(trades), 2) if trades else 0,
            'avg_win': round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0,
            'avg_loss': round(sum(t['pnl'] for t in losses) / len(losses), 2) if losses else 0,
            'rules_yes': summary(rules_yes), 'rules_no': summary(rules_no), 'mistakes': mistakes, 'equity': equity}


@app.route('/api/analytics')
@login_required
def analytics():
    user = current_user()
    return jsonify(build_analytics(get_user_trades(user['id'], 'trade_date ASC, id ASC'), user['initial_balance']))


def export_rows(user_id):
    fields = ['trade_date', 'symbol', 'direction', 'setup', 'criteria_met', 'market_condition', 'planned_rr', 'realised_rr',
              'rules_followed', 'exit_reason', 'entry_price', 'exit_price', 'quantity', 'pnl', 'notes',
              'screenshot_4h', 'screenshot_15m', 'screenshot_5m']
    return fields, [dict(row) for row in get_user_trades(user_id, 'trade_date ASC, id ASC')]


@app.route('/export/csv')
@login_required
def export_csv():
    fields, rows = export_rows(current_user()['id'])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        row['rules_followed'] = 'Yes' if row['rules_followed'] else 'No'
        writer.writerow({field: row.get(field, '') for field in fields})
    response = app.response_class(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=tradelog-journal.csv'
    return response


@app.route('/export/pdf')
@login_required
def export_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    fields, rows = export_rows(current_user()['id'])
    analytics_data = build_analytics(get_user_trades(current_user()['id'], 'trade_date ASC, id ASC'), current_user()['initial_balance'])
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=0.35*inch, leftMargin=0.35*inch, topMargin=0.35*inch, bottomMargin=0.35*inch)
    styles = getSampleStyleSheet()
    story = [Paragraph('TradeLog Pro — Trade Journal', styles['Title']), Paragraph(
        f"{current_user()['name']} · {analytics_data['trade_count']} trades · Total P&L: ${analytics_data['total_pnl']:,.2f} · Win rate: {analytics_data['win_rate']:.1f}%", styles['Normal']), Spacer(1, 0.18*inch)]
    headers = ['Date', 'Symbol', 'Dir.', 'Setup', 'Condition', 'Planned R:R', 'Realised R:R', 'Rules', 'Exit', 'P&L']
    data = [headers] + [[r['trade_date'], r['symbol'], r['direction'], r['setup'], r['market_condition'], f"{r['planned_rr']:.2f}R", f"{r['realised_rr']:.2f}R", 'Yes' if r['rules_followed'] else 'No', r['exit_reason'], f"${r['pnl']:,.2f}"] for r in rows]
    table = Table(data, repeatRows=1, colWidths=[1.05*inch, .55*inch, .4*inch, 1.0*inch, 1.0*inch, .7*inch, .75*inch, .45*inch, .75*inch, .7*inch])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1c2a3d')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#cbd5e1')),('FONTSIZE',(0,0),(-1,-1),7),('ALIGN',(5,1),(-1,-1),'RIGHT'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, colors.HexColor('#f1f5f9')])]))
    story.append(table)
    if analytics_data['mistakes']['rule_breaks']:
        m = analytics_data['mistakes']
        story += [Spacer(1, .18*inch), Paragraph(f"Rule-break summary: {m['rule_breaks']} trades broke rules and produced ${m['rule_break_loss']:,.2f} in losses. Manual/breakeven exits among rule breaks: {m['early_exit_breaks']}.", styles['Normal'])]
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='tradelog-journal.pdf', mimetype='application/pdf')


init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
