import os
import sqlite3
import hashlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, send_from_directory, session, redirect, url_for, render_template_string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BASE_DIR)

app = Flask(__name__)
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
            order_number TEXT NOT NULL,
            screenshot_path TEXT,
            ip_address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            review_note TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS warranty_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL UNIQUE,
            platform TEXT DEFAULT '淘宝/天猫',
            purchase_date TEXT,
            activate_date TEXT,
            expire_date TEXT,
            status TEXT DEFAULT 'active',
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS ip_order_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            order_number TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ip_address, order_number)
        )
    ''')
    conn.commit()
    conn.close()


def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


@app.route('/')
def index():
    return send_from_directory(WORKSPACE_DIR, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(WORKSPACE_DIR, filename)


@app.route('/api/warranty/apply', methods=['POST'])
def apply_warranty():
    try:
        order_number = request.form.get('order_number', '').strip()
        screenshot = request.files.get('screenshot')
        ip_address = get_client_ip()

        if not order_number:
            return jsonify({'success': False, 'message': '请输入订单编号'}), 400

        screenshot_path = None
        if screenshot:
            filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(order_number.encode()).hexdigest()[:8]}_{screenshot.filename}"
            screenshot_path = os.path.join(UPLOAD_FOLDER, filename)
            screenshot.save(screenshot_path)
            screenshot_path = filename

        db = get_db()
        db.execute('''
            INSERT INTO warranty_applications (order_number, screenshot_path, ip_address, status)
            VALUES (?, ?, ?, 'pending')
        ''', (order_number, screenshot_path, ip_address))
        db.commit()

        return jsonify({'success': True, 'message': '提交成功，请等待审核'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/warranty/query', methods=['GET'])
def query_warranty():
    try:
        order_number = request.args.get('order_number', '').strip()
        ip_address = get_client_ip()

        db = get_db()

        # 如果没有输入订单号，尝试通过IP查找已激活的订单
        if not order_number:
            ip_order = db.execute('''
                SELECT w.* FROM warranty_orders w
                JOIN ip_order_mapping m ON w.order_number = m.order_number
                WHERE m.ip_address = ? AND w.status = 'active'
                ORDER BY w.created_at DESC LIMIT 1
            ''', (ip_address,)).fetchone()

            if ip_order:
                return jsonify({
                    'success': True,
                    'found': True,
                    'ip_sync': True,
                    'data': {
                        'order_number': ip_order['order_number'],
                        'platform': ip_order['platform'],
                        'purchase_date': ip_order['purchase_date'],
                        'activate_date': ip_order['activate_date'],
                        'expire_date': ip_order['expire_date'],
                        'status': ip_order['status'],
                        'remaining_days': calculate_remaining_days(ip_order['expire_date'])
                    }
                })
            else:
                return jsonify({'success': True, 'found': False, 'ip_sync': False, 'message': '未查询到相关质保信息'})

        # 根据订单号查询
        order = db.execute('''
            SELECT * FROM warranty_orders WHERE order_number = ? AND status = 'active'
        ''', (order_number,)).fetchone()

        if order:
            # 如果有IP，记录IP与订单的映射关系，用于后续IP同步
            if ip_address:
                try:
                    db.execute('''
                        INSERT OR IGNORE INTO ip_order_mapping (ip_address, order_number)
                        VALUES (?, ?)
                    ''', (ip_address, order_number))
                    db.commit()
                except:
                    pass

            return jsonify({
                'success': True,
                'found': True,
                'ip_sync': False,
                'data': {
                    'order_number': order['order_number'],
                    'platform': order['platform'],
                    'purchase_date': order['purchase_date'],
                    'activate_date': order['activate_date'],
                    'expire_date': order['expire_date'],
                    'status': order['status'],
                    'remaining_days': calculate_remaining_days(order['expire_date'])
                }
            })
        else:
            # 检查是否在审核中
            application = db.execute('''
                SELECT * FROM warranty_applications
                WHERE order_number = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
            ''', (order_number,)).fetchone()

            if application:
                return jsonify({
                    'success': True,
                    'found': False,
                    'ip_sync': False,
                    'pending': True,
                    'message': '该订单正在审核中，请耐心等待'
                })

            return jsonify({'success': True, 'found': False, 'ip_sync': False, 'message': '未查询到相关质保信息'})

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


# ==================== 后台管理 ====================

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

    applications = db.execute('''
        SELECT * FROM warranty_applications
        WHERE status = ?
        ORDER BY created_at DESC
    ''', (status,)).fetchall()

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
        # 更新申请状态
        db.execute('''
            UPDATE warranty_applications SET status = 'approved', reviewed_at = ?, review_note = ?
            WHERE id = ?
        ''', (now, note, app_id))

        # 计算激活和到期日期
        activate_date = datetime.now().strftime('%Y-%m-%d')
        expire_date = (datetime.now() + timedelta(days=warranty_days)).strftime('%Y-%m-%d')

        # 插入或更新订单质保信息
        db.execute('''
            INSERT OR REPLACE INTO warranty_orders
            (order_number, platform, purchase_date, activate_date, expire_date, status, ip_address)
            VALUES (?, ?, ?, ?, ?, 'active', ?)
        ''', (application['order_number'], platform, purchase_date or activate_date,
              activate_date, expire_date, application['ip_address']))

        # 记录IP映射
        if application['ip_address']:
            try:
                db.execute('''
                    INSERT OR IGNORE INTO ip_order_mapping (ip_address, order_number)
                    VALUES (?, ?)
                ''', (application['ip_address'], application['order_number']))
            except:
                pass

    else:  # reject
        db.execute('''
            UPDATE warranty_applications SET status = 'rejected', reviewed_at = ?, review_note = ?
            WHERE id = ?
        ''', (now, note, app_id))

    db.commit()
    return jsonify({'success': True, 'message': '操作成功'})


@app.route('/admin/orders')
def admin_orders():
    db = get_db()
    orders = db.execute('''
        SELECT * FROM warranty_orders ORDER BY created_at DESC
    ''').fetchall()

    return render_template_string(ADMIN_ORDERS_HTML, orders=orders)


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
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">截图</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">IP地址</th>
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
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ app.order_number }}</td>
                        <td class="px-4 py-3 text-sm">
                            {% if app.screenshot_path %}
                            <a href="{{ url_for('uploaded_file', filename=app.screenshot_path) }}" target="_blank"
                               class="text-blue-600 hover:text-blue-800">
                                <i class="fa-solid fa-image mr-1"></i>查看
                            </a>
                            {% else %}
                            <span class="text-gray-400">无</span>
                            {% endif %}
                        </td>
                        <td class="px-4 py-3 text-sm text-gray-600 font-mono">{{ app.ip_address }}</td>
                        <td class="px-4 py-3 text-sm text-gray-600">{{ app.created_at }}</td>
                        {% if status == 'pending' %}
                        <td class="px-4 py-3 text-sm">
                            <button onclick="showReviewModal({{ app.id }}, '{{ app.order_number }}')"
                                    class="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors">
                                审核
                            </button>
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

    <!-- 审核弹窗 -->
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
                <button onclick="submitReview('reject')"
                        class="flex-1 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors">
                    拒绝
                </button>
                <button onclick="submitReview('approve')"
                        class="flex-1 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600 transition-colors">
                    通过
                </button>
            </div>
            <button onclick="closeReviewModal()"
                    class="w-full mt-3 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors">
                取消
            </button>
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

            fetch('/admin/review/' + currentAppId, {
                method: 'POST',
                body: formData
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    alert(data.message);
                }
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
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">平台</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">购买日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">激活日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">到期日期</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">状态</th>
                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-700">IP地址</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                    {% for order in orders %}
                    <tr class="hover:bg-gray-50">
                        <td class="px-4 py-3 text-sm text-gray-900 font-medium">{{ order.order_number }}</td>
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

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
