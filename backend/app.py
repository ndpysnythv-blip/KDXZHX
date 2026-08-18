import os
import sqlite3
import hashlib
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, send_from_directory, session, redirect, url_for, render_template, render_template_string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BASE_DIR)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'), static_url_path='/static')
app.secret_key = 'warranty_admin_secret_key_2024'

DATABASE = os.path.join(BASE_DIR, 'warranty.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS warranty_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT,
            tracking_number TEXT,
            screenshot_path TEXT,
            ip_address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            user_id TEXT,
            user_phone TEXT,
            user_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            review_note TEXT
        )
    ''')
    for col_def in [
        ('tracking_number', 'TEXT'),
        ('user_id', 'TEXT'),
        ('user_phone', 'TEXT'),
        ('user_name', 'TEXT'),
    ]:
        try:
            c.execute(f"ALTER TABLE warranty_applications ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS warranty_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE,
            tracking_number TEXT,
            platform TEXT DEFAULT '淘宝/天猫',
            purchase_date TEXT,
            activate_date TEXT,
            expire_date TEXT,
            status TEXT DEFAULT 'active',
            ip_address TEXT,
            user_id TEXT,
            user_phone TEXT,
            user_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    for col_def in [
        ('tracking_number', 'TEXT'),
        ('user_id', 'TEXT'),
        ('user_phone', 'TEXT'),
        ('user_name', 'TEXT'),
    ]:
        try:
            c.execute(f"ALTER TABLE warranty_orders ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS ip_order_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            order_number TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ip_address, order_number)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_phone_order_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_phone TEXT,
            order_number TEXT,
            tracking_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, order_number, tracking_number)
        )
    ''')
    for col_def in [
        ('tracking_number', 'TEXT'),
    ]:
        try:
            c.execute(f"ALTER TABLE user_phone_order_mapping ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS device_visitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_code TEXT NOT NULL UNIQUE,
            ip_address TEXT,
            user_agent TEXT,
            visit_count INTEGER DEFAULT 1,
            first_visit TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_visit TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS device_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_code TEXT NOT NULL UNIQUE,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def get_client_ip():
    for header in ['CF-Connecting-IP', 'X-Real-IP', 'X-Forwarded-For', 'X-Original-Forwarded-For', 'True-Client-IP']:
        ip = request.headers.get(header)
        if ip:
            ip = ip.split(',')[0].strip()
            if ip and ip != 'unknown' and not ip.startswith('127.') and ip != '::1':
                return ip
    return request.remote_addr or '127.0.0.1'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/location', methods=['GET'])
def get_location():
    try:
        ip = get_client_ip()
        if ip in ('127.0.0.1', 'localhost', '::1'):
            return jsonify({'success': True, 'ip': ip, 'province': '广东', 'city': '深圳', 'is_local': True})
        url = f'http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,message,country,regionName,city,query'
        req = urllib.request.Request(url, headers={'User-Agent': 'KDXZHX-Site/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('status') == 'success' and data.get('country') == '中国':
            province = (data.get('regionName') or '').replace('省', '').replace('市', '').replace('自治区', '').replace('壮族', '').replace('回族', '').replace('维吾尔', '').replace('特别行政区', '').strip()
            city = (data.get('city') or '').replace('市', '').replace('地区', '').replace('自治州', '').strip()
            return jsonify({'success': True, 'ip': ip, 'province': province, 'city': city, 'is_local': False})
        return jsonify({'success': True, 'ip': ip, 'province': '未知', 'city': '未知', 'is_local': False})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'province': '未知', 'city': '未知'}), 200


@app.route('/api/device/check', methods=['POST'])
def device_check():
    try:
        data = request.get_json() or {}
        device_code = data.get('device_code', '').strip()
        ip_address = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        if not device_code:
            return jsonify({'success': False, 'blocked': False})
        db = get_db()
        blocked = db.execute('SELECT * FROM device_blacklist WHERE device_code = ?', (device_code,)).fetchone()
        if blocked:
            return jsonify({'success': True, 'blocked': True, 'reason': blocked['reason'] or '您的设备已被限制访问'})
        existing = db.execute('SELECT * FROM device_visitors WHERE device_code = ?', (device_code,)).fetchone()
        if existing:
            db.execute('UPDATE device_visitors SET visit_count = visit_count + 1, last_visit = CURRENT_TIMESTAMP, ip_address = ?, user_agent = ? WHERE device_code = ?', (ip_address, user_agent, device_code))
        else:
            db.execute('INSERT INTO device_visitors (device_code, ip_address, user_agent) VALUES (?, ?, ?)', (device_code, ip_address, user_agent))
        db.commit()
        return jsonify({'success': True, 'blocked': False})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'blocked': False}), 500


def _header_str(key):
    v = request.headers.get(key)
    return (v or '').strip() if v else ''


@app.route('/api/warranty/apply', methods=['POST'])
def apply_warranty():
    try:
        order_number = (request.form.get('order_number') or '').strip()
        tracking_number = (request.form.get('tracking_number') or '').strip()
        screenshot = request.files.get('screenshot')
        ip_address = get_client_ip()
        if not order_number and not tracking_number:
            return jsonify({'success': False, 'message': '订单编号和运单号至少填写一个'}), 400
        user_id = (request.form.get('user_id') or _header_str('X-User-Id') or '').strip()
        user_phone = (request.form.get('user_phone') or _header_str('X-User-Phone') or '').strip()
        user_name = (request.form.get('user_name') or _header_str('X-User-Name') or '').strip()
        screenshot_path = None
        if screenshot:
            seed = (order_number or tracking_number or 'no-seed').encode()
            filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(seed).hexdigest()[:8]}_{screenshot.filename}"
            screenshot_path = os.path.join(UPLOAD_FOLDER, filename)
            screenshot.save(screenshot_path)
            screenshot_path = filename
        db = get_db()
        db.execute('''
            INSERT INTO warranty_applications
            (order_number, tracking_number, screenshot_path, ip_address, status, user_id, user_phone, user_name)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        ''', (order_number or None, tracking_number or None, screenshot_path, ip_address,
              user_id or None, user_phone or None, user_name or None))
        db.commit()
        return jsonify({'success': True, 'message': '提交成功，请等待审核'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


def _serial_order(o):
    return {
        'order_number': o['order_number'] or '',
        'tracking_number': o['tracking_number'] or '',
        'platform': o['platform'],
        'purchase_date': o['purchase_date'],
        'activate_date': o['activate_date'],
        'expire_date': o['expire_date'],
        'status': o['status'],
        'remaining_days': calculate_remaining_days(o['expire_date']),
    }


@app.route('/api/warranty/query', methods=['GET'])
def query_warranty():
    try:
        order_number = (request.args.get('order_number') or '').strip()
        tracking_number = (request.args.get('tracking_number') or '').strip()
        ip_address = get_client_ip()
        user_id = (request.args.get('user_id') or _header_str('X-User-Id') or '').strip()
        user_phone = (request.args.get('user_phone') or _header_str('X-User-Phone') or '').strip()
        db = get_db()
        user_sync = False
        order = None
        if user_id or user_phone:
            wheres = []
            vals = []
            if user_id:
                wheres.append('m.user_id = ?')
                vals.append(user_id)
            if user_phone:
                wheres.append('m.user_phone = ?')
                vals.append(user_phone)
            sql = f'''
                SELECT w.* FROM warranty_orders w
                JOIN user_phone_order_mapping m ON
                    (m.order_number IS NOT NULL AND w.order_number = m.order_number)
                    OR (m.tracking_number IS NOT NULL AND w.tracking_number = m.tracking_number)
                WHERE {' AND '.join(wheres)} AND w.status = 'active'
                ORDER BY w.created_at DESC LIMIT 1
            '''
            order = db.execute(sql, vals).fetchone()
            if order:
                user_sync = True
        if not order and (order_number or tracking_number):
            where_clause = []
            where_vals = []
            if order_number:
                where_clause.append('order_number = ?')
                where_vals.append(order_number)
            if tracking_number:
                where_clause.append('tracking_number = ?')
                where_vals.append(tracking_number)
            order = db.execute(
                f"SELECT * FROM warranty_orders WHERE ({' OR '.join(where_clause)}) AND status = 'active' LIMIT 1",
                where_vals,
            ).fetchone()
        ip_sync = False
        if not order and not order_number and not tracking_number and not user_sync:
            ip_order = db.execute('''
                SELECT w.* FROM warranty_orders w
                JOIN ip_order_mapping m ON w.order_number = m.order_number
                WHERE m.ip_address = ? AND w.status = 'active'
                ORDER BY w.created_at DESC LIMIT 1
            ''', (ip_address,)).fetchone()
            if ip_order:
                order = ip_order
                ip_sync = True
        if order:
            if ip_address and (order['order_number'] or order['tracking_number']):
                try:
                    db.execute('INSERT OR IGNORE INTO ip_order_mapping (ip_address, order_number) VALUES (?, ?)', (ip_address, order['order_number'] or ''))
                except Exception:
                    pass
            if (user_id or user_phone) and (order['order_number'] or order['tracking_number']):
                try:
                    db.execute('''
                        INSERT OR IGNORE INTO user_phone_order_mapping
                        (user_id, user_phone, order_number, tracking_number)
                        VALUES (?, ?, ?, ?)
                    ''', (user_id or None, user_phone or None,
                          order['order_number'] or None, order['tracking_number'] or None))
                except Exception:
                    pass
            db.commit()
            return jsonify({'success': True, 'found': True, 'ip_sync': ip_sync, 'user_sync': user_sync, 'data': _serial_order(order)})
        if order_number or tracking_number:
            where_app = []
            where_app_vals = []
            if order_number:
                where_app.append('order_number = ?')
                where_app_vals.append(order_number)
            if tracking_number:
                where_app.append('tracking_number = ?')
                where_app_vals.append(tracking_number)
            application = db.execute(
                f"SELECT * FROM warranty_applications WHERE ({' OR '.join(where_app)}) AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                where_app_vals,
            ).fetchone()
            if application:
                return jsonify({'success': True, 'found': False, 'ip_sync': False, 'user_sync': False, 'pending': True, 'message': '该订单正在审核中，请耐心等待'})
        return jsonify({'success': True, 'found': False, 'ip_sync': ip_sync, 'user_sync': user_sync, 'message': '未查询到相关质保信息'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


def calculate_remaining_days(expire_date_str):
    try:
        if not expire_date_str:
            return '-'
        expire_date = datetime.strptime(expire_date_str, '%Y-%m-%d')
        now = datetime.now()
        remaining = (expire_date - now).days
        return f'{max(0, remaining)}天'
    except:
        return '-'


@app.route('/admin')
def admin_index():
    return redirect(url_for('admin_list'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_list'))
        return render_template_string(LOGIN_HTML, error='用户名或密码错误')
    return render_template_string(LOGIN_HTML, error=None)


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/list')
def admin_list():
    status = request.args.get('status', 'pending')
    db = get_db()
    applications = db.execute('SELECT * FROM warranty_applications WHERE status = ? ORDER BY created_at DESC', (status,)).fetchall()
    counts = {
        'pending': db.execute('SELECT COUNT(*) as c FROM warranty_applications WHERE status = "pending"').fetchone()['c'],
        'approved': db.execute('SELECT COUNT(*) as c FROM warranty_applications WHERE status = "approved"').fetchone()['c'],
        'rejected': db.execute('SELECT COUNT(*) as c FROM warranty_applications WHERE status = "rejected"').fetchone()['c'],
    }
    return render_template_string(ADMIN_LIST_HTML, applications=applications, status=status, counts=counts)


@app.route('/admin/review/<int:app_id>', methods=['POST'])
def admin_review(app_id):
    action = request.form.get('action', '')
    note = request.form.get('note', '')
    platform = request.form.get('platform', '淘宝/天猫')
    purchase_date = request.form.get('purchase_date', '')
    warranty_days = int(request.form.get('warranty_days', '30'))
    if action not in ['approve', 'reject']:
        return jsonify({'success': False, 'message': '无效操作'}), 400
    db = get_db()
    application = db.execute('SELECT * FROM warranty_applications WHERE id = ?', (app_id,)).fetchone()
    if not application:
        return jsonify({'success': False, 'message': '申请不存在'}), 404
    if application['status'] != 'pending':
        return jsonify({'success': False, 'message': '该申请已处理'}), 400
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if action == 'approve':
        db.execute('UPDATE warranty_applications SET status = 'approved', reviewed_at = ?, review_note = ? WHERE id = ?', (now, note, app_id))
        activate_date = datetime.now().strftime('%Y-%m-%d')
        expire_date = (datetime.now() + timedelta(days=warranty_days)).strftime('%Y-%m-%d')
        db.execute('''
            INSERT INTO warranty_orders
            (order_number, tracking_number, platform, purchase_date, activate_date, expire_date, status, ip_address, user_id, user_phone, user_name)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(order_number) DO UPDATE SET
                tracking_number=excluded.tracking_number,
                platform=excluded.platform,
                purchase_date=excluded.purchase_date,
                activate_date=excluded.activate_date,
                expire_date=excluded.expire_date,
                status='active',
                ip_address=excluded.ip_address,
                user_id=COALESCE(excluded.user_id, warranty_orders.user_id),
                user_phone=COALESCE(excluded.user_phone, warranty_orders.user_phone),
                user_name=COALESCE(excluded.user_name, warranty_orders.user_name)
        ''', (application['order_number'], application['tracking_number'],
              platform, purchase_date or activate_date,
              activate_date, expire_date,
              application['ip_address'],
              application['user_id'], application['user_phone'], application['user_name']))
        if application['ip_address'] and application['order_number']:
            try:
                db.execute('INSERT OR IGNORE INTO ip_order_mapping (ip_address, order_number) VALUES (?, ?)', (application['ip_address'], application['order_number']))
            except:
                pass
        if application['user_id'] or application['user_phone']:
            try:
                db.execute('''
                    INSERT OR IGNORE INTO user_phone_order_mapping
                    (user_id, user_phone, order_number, tracking_number)
                    VALUES (?, ?, ?, ?)
                ''', (application['user_id'], application['user_phone'],
                      application['order_number'], application['tracking_number']))
            except:
                pass
    else:
        db.execute('UPDATE warranty_applications SET status = 'rejected', reviewed_at = ?, review_note = ? WHERE id = ?', (now, note, app_id))
    db.commit()
    return jsonify({'success': True, 'message': '操作成功'})


@app.route('/admin/orders')
def admin_orders():
    db = get_db()
    orders = db.execute('SELECT * FROM warranty_orders ORDER BY created_at DESC').fetchall()
    return render_template_string(ADMIN_ORDERS_HTML, orders=orders)


def admin_required():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    return None


@app.route('/admin/blacklist')
def admin_blacklist():
    resp = admin_required()
    if resp: return resp
    db = get_db()
    visitors = db.execute('SELECT * FROM device_visitors ORDER BY last_visit DESC LIMIT 200').fetchall()
    blacklist = db.execute('SELECT * FROM device_blacklist ORDER BY created_at DESC').fetchall()
    bl_set = {b['device_code'] for b in blacklist}
    return render_template_string(ADMIN_BLACKLIST_HTML, visitors=visitors, blacklist=blacklist, bl_set=bl_set)


@app.route('/api/admin/blacklist/add', methods=['POST'])
def blacklist_add():
    resp = admin_required()
    if resp: return jsonify({'success': False, 'message': '未登录'}), 401
    try:
        data = request.get_json() or request.form.to_dict()
        device_code = (data.get('device_code') or '').strip()
        reason = (data.get('reason') or '').strip()
        if not device_code:
            return jsonify({'success': False, 'message': '设备码不能为空'}), 400
        db = get_db()
        db.execute('INSERT OR REPLACE INTO device_blacklist (device_code, reason, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)', (device_code, reason))
        db.commit()
        return jsonify({'success': True, 'message': '已加入黑名单'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/blacklist/remove', methods=['POST'])
def blacklist_remove():
    resp = admin_required()
    if resp: return jsonify({'success': False, 'message': '未登录'}), 401
    try:
        data = request.get_json() or request.form.to_dict()
        device_code = (data.get('device_code') or '').strip()
        if not device_code:
            return jsonify({'success': False, 'message': '设备码不能为空'}), 400
        db = get_db()
        db.execute('DELETE FROM device_blacklist WHERE device_code = ?', (device_code,))
        db.commit()
        return jsonify({'success': True, 'message': '已从黑名单移除'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台登录</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
    <div class="bg-white p-8 rounded-xl shadow-lg w-full max-w-md">
        <h2 class="text-2xl font-bold text-center mb-6">质保管理后台</h2>
        {% if error %}
        <div class="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg mb-4">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="mb-4">
                <label class="block text-gray-700 mb-2">用户名</label>
                <input type="text" name="username" class="w-full p-3 border border-gray-300 rounded-lg" required>
            </div>
            <div class="mb-6">
                <label class="block text-gray-700 mb-2">密码</label>
                <input type="password" name="password" class="w-full p-3 border border-gray-300 rounded-lg" required>
            </div>
            <button type="submit" class="w-full py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">登录</button>
        </form>
    </div>
</body>
</html>
'''


ADMIN_LIST_HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>质保审核管理</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="max-w-6xl mx-auto p-6">
        <div class="flex justify-between items-center mb-6">
            <h1 class="text-2xl font-bold">质保审核管理</h1>
            <div class="flex gap-4">
                <a href="{{ url_for('admin_orders') }}" class="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors">
                    <i class="fa-solid fa-list mr-2"></i>已激活订单
                </a>
                <a href="{{ url_for('index') }}" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                    <i class="fa-solid fa-house mr-2"></i>返回首页
                </a>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <div class="flex gap-4 mb-4">
                <a href="{{ url_for('admin_list', status='pending') }}"
                   class="px-4 py-2 rounded-lg transition-colors {{ 'bg-yellow-500 text-white' if status == 'pending' else 'bg-gray-200 text-gray-700 hover:bg-gray-300' }}">
                    待审核 ({{ counts.pending }})
                </a>
                <a href="{{ url_for('admin_list', status='approved') }}"
                   class="px-4 py-2 rounded-lg transition-colors {{ 'bg-green-500 text-white' if status == 'approved' else 'bg-gray-200 text-gray-700 hover:bg-gray-300' }}">
                    已通过 ({{ counts.approved }})
                </a>
                <a href="{{ url_for('admin_list', status='rejected') }}"
                   class="px-4 py-2 rounded-lg transition-colors {{ 'bg-red-500 text-white' if status == 'rejected' else 'bg-gray-200 text-gray-700 hover:bg-gray-300' }}">
                    已拒绝 ({{ counts.rejected }})
                </a>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-lg overflow-hidden">
            {% if applications %}
            <table class="w-full">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">ID</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">订单编号</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">运单号</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">截图</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">IP地址</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">用户</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">申请时间</th>
                        {% if status == 'pending' %}
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">操作</th>
                        {% else %}
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">审核时间</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">备注</th>
                        {% endif %}
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                    {% for app in applications %}
                    <tr class="hover:bg-gray-50">
                        <td class="px-4 py-3 text-sm text-gray-600">{{ app.id }}</td>
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ app.order_number or '—' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ app.tracking_number or '—' }}</td>
                        <td class="px-4 py-3 text-sm">
                            {% if app.screenshot_path %}
                            <a href="{{ url_for('uploaded_file', filename=app.screenshot_path) }}" target="_blank" class="text-blue-600 hover:text-blue-800">
                                <i class="fa-solid fa-image mr-1"></i>查看
                            </a>
                            {% else %}
                            <span class="text-gray-400">无</span>
                            {% endif %}
                        </td>
                        <td class="px-4 py-3 text-sm text-gray-600 font-mono">{{ app.ip_address }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">
                            {% if app.user_phone %}{{ app.user_phone }}{% endif %}
                            {% if app.user_name %}<div class="text-xs text-gray-400">{{ app.user_name }}</div>{% endif %}
                        </td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ app.created_at }}</td>
                        {% if status == 'pending' %}
                        <td class="px-4 py-3 text-sm">
                            <button onclick="showReviewModal({{ app.id }}, '{{ app.order_number or '无单号' }}')" class="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors">审核</button>
                        </td>
                        {% else %}
                        <td class="px-4 py-3 text-sm text-gray-600">{{ app.reviewed_at or '-' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ app.review_note or '-' }}</td>
                        {% endif %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="p-8 text-center text-gray-500">
                <i class="fa-solid fa-inbox text-4xl mb-3"></i>
                <p>暂无数据</p>
            </div>
            {% endif %}
        </div>
    </div>
    <div id="review-modal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
        <div class="bg-white rounded-xl p-6 w-full max-w-md mx-4">
            <h3 class="text-lg font-bold mb-4">审核订单 <span id="modal-order-num" class="text-blue-600"></span></h3>
            <div class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">购买平台</label>
                    <select id="modal-platform" class="w-full p-2 border border-gray-300 rounded-lg">
                        <option value="淘宝/天猫">淘宝/天猫</option>
                        <option value="京东">京东</option>
                        <option value="拼多多">拼多多</option>
                        <option value="闲鱼">闲鱼</option>
                        <option value="其他">其他</option>
                    </select>
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">购买日期</label>
                    <input type="date" id="modal-purchase-date" class="w-full p-2 border border-gray-300 rounded-lg">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">质保天数</label>
                    <input type="number" id="modal-warranty-days" value="30" class="w-full p-2 border border-gray-300 rounded-lg">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">备注</label>
                    <textarea id="modal-note" rows="2" class="w-full p-2 border border-gray-300 rounded-lg" placeholder="选填"></textarea>
                </div>
            </div>
            <div class="flex gap-3 mt-6">
                <button onclick="submitReview('reject')" class="flex-1 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors">拒绝</button>
                <button onclick="submitReview('approve')" class="flex-1 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600 transition-colors">通过</button>
            </div>
            <button onclick="closeReviewModal()" class="w-full mt-3 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors">取消</button>
        </div>
    </div>
    <script>
        let currentAppId = null;
        function showReviewModal(appId, orderNum) {
            currentAppId = appId;
            document.getElementById('modal-order-num').textContent = orderNum;
            document.getElementById('modal-platform').value = '淘宝/天猫';
            document.getElementById('modal-purchase-date').value = new Date().toISOString().split('T')[0];
            document.getElementById('modal-warranty-days').value = 30;
            document.getElementById('modal-note').value = '';
            document.getElementById('review-modal').classList.remove('hidden');
            document.getElementById('review-modal').classList.add('flex');
        }
        function closeReviewModal() {
            document.getElementById('review-modal').classList.add('hidden');
            document.getElementById('review-modal').classList.remove('flex');
            currentAppId = null;
        }
        function submitReview(action) {
            if (!currentAppId) return;
            const formData = new FormData();
            formData.append('action', action);
            formData.append('note', document.getElementById('modal-note').value);
            formData.append('platform', document.getElementById('modal-platform').value);
            formData.append('purchase_date', document.getElementById('modal-purchase-date').value);
            formData.append('warranty_days', document.getElementById('modal-warranty-days').value);
            fetch('/admin/review/' + currentAppId, { method: 'POST', body: formData }).then(r => r.json()).then(data => {
                if (data.success) { location.reload(); } else { alert(data.message); }
            });
        }
    </script>
</body>
</html>
'''


ADMIN_ORDERS_HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>已激活订单列表</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="max-w-6xl mx-auto p-6">
        <div class="flex justify-between items-center mb-6">
            <h1 class="text-2xl font-bold">已激活订单</h1>
            <div class="flex gap-4">
                <a href="{{ url_for('admin_list') }}" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                    <i class="fa-solid fa-clipboard-check mr-2"></i>审核管理
                </a>
                <a href="{{ url_for('index') }}" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                    <i class="fa-solid fa-house mr-2"></i>返回首页
                </a>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-lg overflow-hidden">
            {% if orders %}
            <table class="w-full">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">订单编号</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">运单号</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">平台</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">购买日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">激活日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">到期日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">状态</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">用户</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">IP地址</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                    {% for order in orders %}
                    <tr class="hover:bg-gray-50">
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ order.order_number or '—' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ order.tracking_number or '—' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ order.platform }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ order.purchase_date or '-' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ order.activate_date or '-' }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ order.expire_date or '-' }}</td>
                        <td class="px-4 py-3 text-sm">
                            {% if order.status == 'active' %}
                            <span class="px-2 py-1 bg-green-100 text-green-700 rounded-full text-xs">有效</span>
                            {% else %}
                            <span class="px-2 py-1 bg-gray-100 text-gray-700 rounded-full text-xs">{{ order.status }}</span>
                            {% endif %}
                        </td>
                        <td class="px-4 py-3 text-sm text-gray-600">
                            {% if order.user_phone %}{{ order.user_phone }}{% endif %}
                            {% if order.user_name %}<div class="text-xs text-gray-400">{{ order.user_name }}</div>{% endif %}
                        </td>
                        <td class="px-4 py-3 text-sm text-gray-600 font-mono">{{ order.ip_address or '-' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="p-8 text-center text-gray-500">
                <i class="fa-solid fa-inbox text-4xl mb-3"></i>
                <p>暂无订单</p>
            </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
'''


ADMIN_BLACKLIST_HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>设备黑名单管理 - 防伪溯源</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="max-w-7xl mx-auto p-6">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
            <h1 class="text-2xl font-bold">
                <i class="fa-solid fa-shield-halved mr-2 text-blue-600"></i>设备黑名单 & 防伪溯源
            </h1>
            <div class="flex flex-wrap gap-3">
                <a href="{{ url_for('admin_list') }}" class="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors text-sm">
                    <i class="fa-solid fa-clipboard-check mr-2"></i>审核管理
                </a>
                <a href="{{ url_for('admin_orders') }}" class="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors text-sm">
                    <i class="fa-solid fa-list mr-2"></i>已激活订单
                </a>
                <a href="{{ url_for('index') }}" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm">
                    <i class="fa-solid fa-house mr-2"></i>返回首页
                </a>
                <a href="{{ url_for('admin_logout') }}" class="px-4 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors text-sm">
                    <i class="fa-solid fa-right-from-bracket mr-2"></i>退出登录
                </a>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-bold mb-4 text-red-700">
                <i class="fa-solid fa-ban mr-2"></i>通过设备码手动封禁
            </h2>
            <form id="add-blacklist-form" class="flex flex-col sm:flex-row gap-3">
                <div class="flex-1">
                    <label class="block text-sm font-medium text-gray-700 mb-1">设备码（从水印或下方记录表中复制）</label>
                    <input id="blk-device-code" type="text" placeholder="如：DEV-XXXXXXXX-XXXXX" class="w-full p-3 border border-gray-300 rounded-lg font-mono focus:ring-2 focus:ring-red-500 focus:border-transparent" required>
                </div>
                <div class="flex-1">
                    <label class="block text-sm font-medium text-gray-700 mb-1">封禁原因（选填，用户可见）</label>
                    <input id="blk-reason" type="text" placeholder="如：资料恶意外泄" class="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 focus:border-transparent">
                </div>
                <div class="sm:min-w-[140px]">
                    <label class="block text-sm font-medium text-gray-700 mb-1">&nbsp;</label>
                    <button type="submit" class="w-full py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors font-medium">
                        <i class="fa-solid fa-user-lock mr-2"></i>加入黑名单
                    </button>
                </div>
            </form>
        </div>
        <div class="bg-white rounded-xl shadow-lg overflow-hidden mb-6">
            <div class="px-6 py-4 border-b border-gray-200 bg-red-50">
                <h2 class="text-lg font-bold text-red-800">
                    <i class="fa-solid fa-circle-xmark mr-2"></i>当前黑名单（{{ blacklist|length }}）
                </h2>
            </div>
            <div class="overflow-x-auto">
                {% if blacklist %}
                <table class="w-full">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">设备码</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">封禁原因</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">封禁时间</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">操作</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">
                        {% for b in blacklist %}
                        <tr class="hover:bg-gray-50">
                            <td class="px-4 py-3 text-sm font-mono font-bold text-red-700 select-all">{{ b.device_code }}</td>
                            <td class="px-4 py-3 text-sm text-gray-700">{{ b.reason or '未填写' }}</td>
                            <td class="px-4 py-3 text-sm text-gray-600">{{ b.created_at }}</td>
                            <td class="px-4 py-3 text-sm">
                                <button onclick="removeBlacklist('{{ b.device_code }}')" class="px-3 py-1 bg-green-600 text-white rounded hover:bg-green-700 transition-colors text-xs">
                                    <i class="fa-solid fa-check mr-1"></i>解封
                                </button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                {% else %}
                <div class="p-8 text-center text-gray-500">
                    <i class="fa-solid fa-shield text-4xl mb-3 text-green-500"></i>
                    <p>当前无封禁设备，一切正常</p>
                </div>
                {% endif %}
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-lg overflow-hidden">
            <div class="px-6 py-4 border-b border-gray-200 bg-blue-50">
                <h2 class="text-lg font-bold text-blue-800">
                    <i class="fa-solid fa-users mr-2"></i>设备访问记录（最新 {{ visitors|length }} 条）
                </h2>
                <p class="text-sm text-blue-600 mt-1">从水印中读取到设备码后，可在此处查找来源并点击「封禁」按钮</p>
            </div>
            <div class="overflow-x-auto">
                {% if visitors %}
                <table class="w-full">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">设备码</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">IP地址</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">访问次数</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">首次访问</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">最近访问</th>
                            <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">操作</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">
                        {% for v in visitors %}
                        <tr class="hover:bg-gray-50">
                            <td class="px-4 py-3 text-sm font-mono font-bold text-gray-800 select-all">{{ v.device_code }}</td>
                            <td class="px-4 py-3 text-sm font-mono text-gray-600">{{ v.ip_address or '-' }}</td>
                            <td class="px-4 py-3 text-sm">
                                <span class="px-2 py-1 bg-blue-100 text-blue-700 rounded-full text-xs">{{ v.visit_count }}</span>
                            </td>
                            <td class="px-4 py-3 text-sm text-gray-600">{{ v.first_visit }}</td>
                            <td class="px-4 py-3 text-sm text-gray-600">{{ v.last_visit }}</td>
                            <td class="px-4 py-3 text-sm">
                                {% if v.device_code in bl_set %}
                                <span class="px-2 py-1 bg-red-100 text-red-700 rounded text-xs">
                                    <i class="fa-solid fa-ban mr-1"></i>已封禁
                                </span>
                                <button onclick="removeBlacklist('{{ v.device_code }}')" class="ml-1 px-2 py-1 bg-green-600 text-white rounded hover:bg-green-700 transition-colors text-xs">解封</button>
                                {% else %}
                                <button onclick="addBlacklist('{{ v.device_code }}', '资料外泄嫌疑')" class="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 transition-colors text-xs">
                                    <i class="fa-solid fa-ban mr-1"></i>封禁
                                </button>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                {% else %}
                <div class="p-8 text-center text-gray-500">
                    <i class="fa-solid fa-clock-rotate-left text-4xl mb-3"></i>
                    <p>暂无访问记录</p>
                </div>
                {% endif %}
            </div>
        </div>
    </div>
    <script>
        document.getElementById('add-blacklist-form').addEventListener('submit', function(e) {
            e.preventDefault();
            const code = document.getElementById('blk-device-code').value.trim();
            const reason = document.getElementById('blk-reason').value.trim();
            if (!code) { alert('请输入设备码'); return; }
            addBlacklist(code, reason);
        });
        function addBlacklist(deviceCode, reason) {
            fetch('/api/admin/blacklist/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_code: deviceCode, reason: reason || '' })
            }).then(r => r.json()).then(data => {
                if (data.success) { alert('已封禁：' + deviceCode); location.reload(); }
                else { alert(data.message || '操作失败'); }
            });
        }
        function removeBlacklist(deviceCode) {
            if (!confirm('确定要解除对 ' + deviceCode + ' 的封禁吗？')) return;
            fetch('/api/admin/blacklist/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_code: deviceCode })
            }).then(r => r.json()).then(data => {
                if (data.success) { alert('已解除封禁'); location.reload(); }
                else { alert(data.message || '操作失败'); }
            });
        }
    </script>
</body>
</html>
'''


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
