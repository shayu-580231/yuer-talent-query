"""
数据查询网站 - 云端版后端
部署到 Render，使用 Playwright headless 登录
认证状态存储在内存中（适配 Docker 环境）
"""
import json, os, threading, time, shutil, math, tempfile
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright

app = Flask(__name__, static_folder="static", template_folder="templates")

# ============ CORS ============
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Accesstoken, X-User-Agent, X-Page-Path, X-Csrf-Token"
    if request.method == "OPTIONS":
        return resp
    return resp

# ============ 配置 ============
TARGET_SITE = "https://cv.bxyuer.com"
API_BASE = "https://pre-cv.bxyuer.com"
DATA_API_BASE = "https://webapi.bxyuer.com"

# ============ 内存存储（适配 Docker 无持久化文件系统） ============
AUTH_STATE = {"state": None}  # 存储 Playwright storage_state
BROWSER_LOCK = threading.Lock()
login_result = {"success": False, "message": "", "done": False}
login_done_event = threading.Event()


# ============ 达标标准 ============
STANDARDS = {
    "effective": {
        "name": "有效达人",
        "order_amount_diamond": 6000,
        "repeat_rate": 0.01,
        "mic_days": 1,
        "net_order_users": 6,
        "mic_count": 60,
        "refund_rate": 1.0,
    },
    "quality": {
        "name": "优质达人",
        "order_amount_rmb": 2000,
        "repeat_rate": 0.15,
        "mic_days": 15,
        "net_order_users": 10,
        "mic_count": 150,
        "refund_rate": 0.05,
    }
}


# ============ 认证状态管理 ============
def load_auth_state():
    return AUTH_STATE["state"]

def save_auth_state(state):
    AUTH_STATE["state"] = state

def clear_auth():
    AUTH_STATE["state"] = None


# ============ 获取 Access Token ============
def get_access_token():
    auth_state = load_auth_state()
    if not auth_state:
        return None
    for origin in auth_state.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") == "yuer-cv-union__accessToken":
                return item.get("value")
            if item.get("name") == "yuer-cv-union__userinfo":
                try:
                    userinfo = json.loads(item.get("value", "{}"))
                    token = userinfo.get("user", {}).get("accessToken")
                    if token:
                        return token
                except:
                    pass
    return None


def get_page_path(path):
    if "/talent" in path or "queryUnionTalent" in path or "searchUser" in path:
        return "/talent"
    if "queryUnionTalentData" in path or "DataAnalysis" in path:
        return "/talent/detail"
    return "/talent"


# ============ API 请求（使用 requests 库） ============
def playwright_request(path, method="GET", params=None, data=None, base_url=None):
    auth_state = load_auth_state()
    if not auth_state:
        return None, "need_login"

    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        base = base_url or API_BASE
        url = base.rstrip("/") + "/" + path.lstrip("/")

    access_token = get_access_token()
    if not access_token:
        return None, "need_login"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://cv.bxyuer.com/",
        "X-Accesstoken": access_token,
        "X-User-Agent": "mapi/1.0 (Windows 10.0;yitan.com.out.code.app.yuer 0.0.1; ; ) kernel/1.0(webkit;537.36)",
        "X-Page-Path": get_page_path(path),
        "X-Csrf-Token": "",
        "Origin": "https://cv.bxyuer.com",
    }

    cookies = {}
    for c in auth_state.get("cookies", []):
        name = c.get("name")
        value = c.get("value")
        if name and value:
            cookies[name] = value

    session = requests.Session()
    session.headers.update(headers)
    session.cookies.update(cookies)

    try:
        if method == "GET":
            resp = session.get(url, params=params or {}, timeout=30)
        else:
            resp = session.post(url, json=data or {}, timeout=30)

        if resp.status_code == 401:
            return None, "need_login"

        try:
            body = resp.json()
        except:
            body = {"raw": resp.text[:5000], "status_code": resp.status_code}

        return body, None

    except Exception as e:
        print(f"[API] exception: {e}")
        return {"error": str(e)}, None
    finally:
        session.close()


def check_login_status():
    auth_state = load_auth_state()
    if not auth_state:
        return False, "未登录"

    data, error = playwright_request("/api/user/info")
    if error == "need_login":
        return False, "登录已过期"

    if data and isinstance(data, dict) and data.get("success") is False:
        code = data.get("code")
        if code in ("9002", "8010"):
            return False, "登录已过期，请重新登录"

    return True, "已登录"


# ============ 日期范围 ============
def get_date_range(period, ref_date=None):
    today = ref_date or datetime.now().date()
    if isinstance(today, str):
        today = datetime.strptime(today, "%Y-%m-%d").date()

    if period == "daily":
        start = end = today
    elif period == "weekly":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "monthly":
        start = today.replace(day=1)
        next_month = start + timedelta(days=32)
        end = next_month.replace(day=1) - timedelta(days=1)
    else:
        start = end = today

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ============ 数据接口 ============
def get_union_id():
    auth_state = load_auth_state()
    if auth_state:
        for origin in auth_state.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") == "yuer-cv-union__userinfo":
                    try:
                        userinfo = json.loads(item.get("value", "{}"))
                        union_id = userinfo.get("union", {}).get("unionId")
                        if union_id:
                            return union_id, None
                    except:
                        pass

    data, error = playwright_request(
        "/api/cv/unionOperationService/getUnionDetailInfo",
        method="POST",
        data={}
    )
    if error:
        return None, error
    if not data or not data.get("success"):
        return None, "获取公会信息失败"
    result = data.get("result", {})
    union_id = result.get("unionTalentLimitDTO", {}).get("unionId")
    if not union_id:
        union_id = result.get("unionId")
    return union_id, None


def search_talent_uid(show_no, union_id):
    try:
        uid_num = int(union_id)
    except:
        uid_num = union_id
    data, error = playwright_request(
        "/api/cv/unionCommonOperationService/searchUser",
        method="POST",
        data={"nickNameOrShowNo": str(show_no), "needFilter": True, "unionId": uid_num}
    )
    if error:
        return None, error
    if not data or not data.get("success"):
        msg = data.get("msg", "搜索达人失败") if data else "搜索达人失败"
        return None, msg
    result = data.get("result", [])
    if not result:
        return None, "未找到该达人"
    return result[0], None


def get_talent_info(talent_uid, union_id):
    try:
        uid_num = int(union_id)
    except:
        uid_num = union_id
    data, error = playwright_request(
        "https://pre-cv.bxyuer.com/api/cv/unionDataOperationService/queryUnionTalentInfo",
        method="POST",
        data={"talentUid": str(talent_uid), "unionId": uid_num},
        base_url=None
    )
    if error:
        return None, error
    if not data or not data.get("success"):
        msg = data.get("msg", "获取达人详情失败") if data else "获取达人详情失败"
        return None, msg
    return data.get("result", {}), None


def get_talent_data_analysis(talent_uid, union_id, start_date, end_date, biz_type=0):
    data, error = playwright_request(
        "https://webapi.bxyuer.com/UnionDataOperationService/queryUnionTalentDataAnalysis",
        method="POST",
        data={
            "talentUid": str(talent_uid),
            "unionId": str(union_id),
            "startDate": start_date,
            "endDate": end_date,
            "ascending": False,
            "bizType": biz_type,
            "column": "start_date"
        }
    )
    if error:
        return None, error
    if not data or not data.get("success"):
        msg = data.get("msg", "获取数据分析失败") if data else "获取数据分析失败"
        return None, msg
    return data.get("result", {}), None


# ============ 数值转换 ============
def diamond_to_rmb(diamond_str):
    try:
        diamond = float(diamond_str or 0)
        return round(diamond / 100, 2)
    except:
        return 0.0

def safe_float(val, default=0.0):
    try:
        return float(val or default)
    except:
        return default

def safe_int(val, default=0):
    try:
        return int(float(val or default))
    except:
        return default


# ============ 达标对比 ============
def calc_repeat_rate(repeat_users, total_users):
    if not total_users:
        return 0.0
    return round(safe_int(repeat_users) / safe_int(total_users) * 100, 2)

def calc_refund_rate(refund_count, order_count):
    if not order_count:
        return 0.0
    return round(safe_int(refund_count) / safe_int(order_count) * 100, 2)

def required_repeat_users(total_users, rate):
    if not total_users:
        return 0
    return max(1, math.ceil(safe_int(total_users) * rate))


def build_comparison(data):
    actual = data.get("data", {})
    total_users = safe_int(actual.get("payUserCount"))
    repeat_users = safe_int(actual.get("repeatUserCount"))
    order_count = safe_int(actual.get("orderCount"))
    refund_count = safe_int(actual.get("refundOrderCount"))

    repeat_rate = calc_repeat_rate(repeat_users, total_users)
    refund_rate = calc_refund_rate(refund_count, order_count)

    total_amount_rmb = safe_float(actual.get("totalOrderAmount"))
    total_amount_diamond = safe_float(actual.get("totalOrderAmountDiamond"))
    mic_count = safe_int(actual.get("micAuditionCount"))
    mic_days = safe_int(actual.get("micDays"))
    net_order_users = safe_int(actual.get("netOrderUserCount"))

    eff = STANDARDS["effective"]
    qty = STANDARDS["quality"]

    eff_required_repeat = required_repeat_users(total_users, eff["repeat_rate"])
    qty_required_repeat = required_repeat_users(total_users, qty["repeat_rate"])

    rows = [
        {"key": "nickname", "label": "达人昵称", "value": actual.get("nickname") or "--", "effective": None, "quality": None},
        {"key": "talentId", "label": "达人ID", "value": actual.get("talentId") or "--", "effective": None, "quality": None},
        {"key": "totalOrderAmount", "label": "成单金额（元）", "value": f"\u00a5{total_amount_rmb:,.2f}",
         "effective": {"threshold": f"\u2265{diamond_to_rmb(eff['order_amount_diamond']):.0f}\u5143", "pass": total_amount_diamond >= eff["order_amount_diamond"], "short": None},
         "quality": {"threshold": f"\u2265{qty['order_amount_rmb']}\u5143", "pass": total_amount_rmb >= qty["order_amount_rmb"], "short": None if total_amount_rmb >= qty["order_amount_rmb"] else f"\u5dee{qty['order_amount_rmb'] - total_amount_rmb:.0f}\u5143"}},
        {"key": "orderCount", "label": "成单数", "value": f"{order_count}\u5355", "effective": None, "quality": None},
        {"key": "payUserCount", "label": "成单人数", "value": f"{total_users}\u4eba", "effective": None, "quality": None},
        {"key": "netOrderUserCount", "label": "净下单用户数", "value": f"{net_order_users}\u4eba",
         "effective": {"threshold": f"\u2265{eff['net_order_users']}\u4eba", "pass": net_order_users >= eff["net_order_users"], "short": None if net_order_users >= eff["net_order_users"] else f"\u5dee{eff['net_order_users'] - net_order_users}\u4eba"},
         "quality": {"threshold": f"\u2265{qty['net_order_users']}\u4eba", "pass": net_order_users >= qty["net_order_users"], "short": None if net_order_users >= qty["net_order_users"] else f"\u5dee{qty['net_order_users'] - net_order_users}\u4eba"}},
        {"key": "refundDiamond", "label": "退款钻石", "value": f"{safe_float(actual.get('refundDiamond')):,.0f}\u94bb\u77f3", "effective": None, "quality": None},
        {"key": "repeatUserCount", "label": "复购用户数", "value": f"{repeat_users}\u4eba",
         "effective": {"threshold": f"\u2265{eff_required_repeat}\u4eba", "pass": repeat_users >= eff_required_repeat, "short": None if repeat_users >= eff_required_repeat else f"\u5dee{eff_required_repeat - repeat_users}\u4eba"},
         "quality": {"threshold": f"\u2265{qty_required_repeat}\u4eba", "pass": repeat_users >= qty_required_repeat, "short": None if repeat_users >= qty_required_repeat else f"\u5dee{qty_required_repeat - repeat_users}\u4eba"}},
        {"key": "micAuditionCount", "label": "上麦试音次数", "value": f"{mic_count}\u6b21",
         "effective": {"threshold": f"\u2265{eff['mic_count']}\u6b21", "pass": mic_count >= eff["mic_count"], "short": None if mic_count >= eff["mic_count"] else f"\u5dee{eff['mic_count'] - mic_count}\u6b21"},
         "quality": {"threshold": f"\u2265{qty['mic_count']}\u6b21", "pass": mic_count >= qty["mic_count"], "short": None if mic_count >= qty["mic_count"] else f"\u5dee{qty['mic_count'] - mic_count}\u6b21"}},
        {"key": "refundUserCount", "label": "退单用户数", "value": f"{refund_count}\u4eba", "effective": None, "quality": None},
        {"key": "micDays", "label": "上麦天数", "value": f"{mic_days}\u5929",
         "effective": {"threshold": f"\u2265{eff['mic_days']}\u5929", "pass": mic_days >= eff["mic_days"], "short": None if mic_days >= eff["mic_days"] else f"\u5dee{eff['mic_days'] - mic_days}\u5929"},
         "quality": {"threshold": f"\u2265{qty['mic_days']}\u5929", "pass": mic_days >= qty["mic_days"], "short": None if mic_days >= qty["mic_days"] else f"\u5dee{qty['mic_days'] - mic_days}\u5929"}},
        {"key": "repeatRate", "label": "复购率", "value": f"{repeat_rate}%",
         "effective": {"threshold": f"\u2265{eff['repeat_rate'] * 100:.0f}%", "pass": repeat_users >= eff_required_repeat, "short": None if repeat_users >= eff_required_repeat else f"\u5dee{eff_required_repeat - repeat_users}\u4eba"},
         "quality": {"threshold": f"\u2265{qty['repeat_rate'] * 100:.0f}%", "pass": repeat_users >= qty_required_repeat, "short": None if repeat_users >= qty_required_repeat else f"\u5dee{qty_required_repeat - repeat_users}\u4eba"}},
        {"key": "refundRate", "label": "退单率", "value": f"{refund_rate}%",
         "effective": {"threshold": f"<{eff['refund_rate'] * 100:.0f}%", "pass": refund_rate < eff["refund_rate"] * 100, "short": None},
         "quality": {"threshold": f"<{qty['refund_rate'] * 100:.0f}%", "pass": refund_rate < qty["refund_rate"] * 100, "short": None}},
    ]

    return {
        "effective_pass": all(r["effective"]["pass"] for r in rows if r.get("effective")),
        "quality_pass": all(r["quality"]["pass"] for r in rows if r.get("quality")),
        "rows": rows,
    }


# ============ Playwright 登录 ============
def do_login_auto(phone, password):
    global login_result
    login_result = {"success": False, "message": "", "done": False}
    login_done_event.clear()

    with BROWSER_LOCK:
        # 使用临时目录作为浏览器 profile
        browser_profile = tempfile.mkdtemp(prefix="pw_profile_")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            # 反检测
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { runtime: {} };
            """)

            page = context.new_page()

            try:
                page.goto(f"{TARGET_SITE}/#/login", wait_until="networkidle")
                page.wait_for_timeout(1500)

                page.click('.login-tabs__tab:has-text("密码登录")')
                page.wait_for_timeout(500)

                page.fill('input[name="phone"]', phone)
                page.fill('input[name="password"]', password)
                page.wait_for_timeout(300)

                page.click('.btn-submit')
                print("[登录] 已提交登录信息，等待跳转...", flush=True)

                page.wait_for_function(
                    """() => !window.location.hash.includes('login')""",
                    timeout=30000
                )
                page.wait_for_timeout(2000)
                print("[登录] 登录成功，正在保存认证状态...", flush=True)

                auth_state = context.storage_state()
                save_auth_state(auth_state)
                print(f"[登录] 已保存认证状态（{len(auth_state.get('cookies', []))} cookies）", flush=True)

                login_result = {"success": True, "message": "登录成功", "done": True}
                login_done_event.set()

                context.close()
                browser.close()

                # 清理临时目录
                try:
                    shutil.rmtree(browser_profile)
                except:
                    pass

                return True, "登录成功"

            except Exception as e:
                print(f"[登录] 自动登录失败或超时: {e}", flush=True)
                login_result = {"success": False, "message": f"登录失败: {str(e)}", "done": True}
                login_done_event.set()

                context.close()
                browser.close()

                try:
                    shutil.rmtree(browser_profile)
                except:
                    pass

                return False, f"登录失败: {str(e)}"


# ============ API 路由 ============
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/status")
def api_status():
    ok, msg = check_login_status()
    return jsonify({"logged_in": ok, "message": msg})


@app.route("/api/login", methods=["POST"])
def api_login():
    auth = load_auth_state()
    if auth:
        ok, _ = check_login_status()
        if ok:
            return jsonify({"success": True, "message": "已经登录"})

    body = request.get_json() or {}
    phone = body.get("phone", "").strip()
    password = body.get("password", "").strip()

    if not phone or not password:
        return jsonify({"success": False, "message": "请输入手机号和密码"}), 400

    t = threading.Thread(target=do_login_auto, args=(phone, password), daemon=True)
    t.start()

    return jsonify({"success": True, "message": "正在自动登录，请稍候..."})


@app.route("/api/login/status")
def api_login_status():
    auth = load_auth_state()
    if not auth:
        return jsonify({"done": login_result.get("done", False), "message": login_result.get("message", "等待登录中...")})

    ok, msg = check_login_status()
    if ok:
        return jsonify({"done": True, "success": True, "message": "登录成功！"})
    return jsonify({"done": False, "message": "等待登录中..."})


@app.route("/api/talent/<talent_id>", methods=["GET"])
def api_talent_query(talent_id):
    auth = load_auth_state()
    if not auth:
        return jsonify({"error": "need_login", "message": "请先登录"}), 401

    period = request.args.get("period", "daily")
    if period not in ("daily", "weekly", "monthly"):
        period = "daily"

    union_id, error = get_union_id()
    if error:
        return jsonify({"error": error, "message": "获取公会信息失败"}), 401 if error == "need_login" else 500

    user_info, error = search_talent_uid(talent_id, union_id)
    if error:
        return jsonify({"error": "query_failed", "message": error}), 404

    talent_uid = user_info.get("uid")
    if not talent_uid:
        return jsonify({"error": "query_failed", "message": "未获取到达人UID"}), 500

    talent_info, error = get_talent_info(talent_uid, union_id)
    if error:
        return jsonify({"error": error, "message": f"获取达人详情失败: {error}"}), 401 if error == "need_login" else 500

    start_date, end_date = get_date_range(period)
    analysis, error = get_talent_data_analysis(talent_uid, union_id, start_date, end_date)
    if error:
        return jsonify({"error": error, "message": "获取业务数据失败"}), 401 if error == "need_login" else 500

    total_diamond = safe_float(analysis.get("totalDiamond"))
    refund_diamond = safe_float(analysis.get("totalRefundDiamond"))
    actual_diamond = total_diamond - refund_diamond

    result = {
        "talent": {
            "nickname": talent_info.get("nickname") or user_info.get("nickname"),
            "id": talent_id,
            "showNo": talent_info.get("showNo") or user_info.get("showNo"),
            "uid": talent_uid,
            "avatar": talent_info.get("avatar") or user_info.get("avatar"),
            "unionName": talent_info.get("unionName"),
            "signTime": talent_info.get("signTime"),
        },
        "period": period,
        "dateRange": {"start": start_date, "end": end_date},
        "data": {
            "nickname": talent_info.get("nickname") or user_info.get("nickname"),
            "talentId": talent_id,
            "totalOrderAmount": diamond_to_rmb(total_diamond),
            "totalOrderAmountDiamond": total_diamond,
            "orderCount": safe_int(analysis.get("totalOrderCount")),
            "payUserCount": safe_int(analysis.get("totalPayCount")),
            "netOrderUserCount": safe_int(analysis.get("netOrderCnt")),
            "refundDiamond": refund_diamond,
            "actualAmount": diamond_to_rmb(actual_diamond),
            "refundOrderCount": safe_int(analysis.get("totalRefundCount")),
            "repeatUserCount": safe_int(analysis.get("repeatOrderUserCnt")),
            "micAuditionCount": safe_int(analysis.get("mcAuditionNum")),
            "micDays": safe_int(analysis.get("auditionMicDaysNum")),
            "dispatchRewardAmount": diamond_to_rmb(safe_float(analysis.get("dispatchMicRewardAmt"))),
            "dispatchRewardUserCount": safe_int(analysis.get("dispatchMicRewardUserCnt")),
        }
    }

    return jsonify({"success": True, "data": result})


@app.route("/api/talent/<talent_id>/compare", methods=["GET"])
def api_talent_compare(talent_id):
    auth = load_auth_state()
    if not auth:
        return jsonify({"error": "need_login", "message": "请先登录"}), 401

    union_id, error = get_union_id()
    if error:
        return jsonify({"error": error, "message": "获取公会信息失败"}), 401 if error == "need_login" else 500

    user_info, error = search_talent_uid(talent_id, union_id)
    if error:
        return jsonify({"error": "query_failed", "message": error}), 404

    talent_uid = user_info.get("uid")
    if not talent_uid:
        return jsonify({"error": "query_failed", "message": "未获取到达人UID"}), 500

    talent_info, error = get_talent_info(talent_uid, union_id)
    if error:
        return jsonify({"error": error, "message": f"获取达人详情失败: {error}"}), 401 if error == "need_login" else 500

    start_date, end_date = get_date_range("monthly")
    analysis, error = get_talent_data_analysis(talent_uid, union_id, start_date, end_date)
    if error:
        return jsonify({"error": error, "message": "获取业务数据失败"}), 401 if error == "need_login" else 500

    total_diamond = safe_float(analysis.get("totalDiamond"))
    refund_diamond = safe_float(analysis.get("totalRefundDiamond"))
    actual_diamond = total_diamond - refund_diamond

    monthly_data = {
        "data": {
            "nickname": talent_info.get("nickname") or user_info.get("nickname"),
            "talentId": talent_id,
            "totalOrderAmount": diamond_to_rmb(total_diamond),
            "totalOrderAmountDiamond": total_diamond,
            "orderCount": safe_int(analysis.get("totalOrderCount")),
            "payUserCount": safe_int(analysis.get("totalPayCount")),
            "netOrderUserCount": safe_int(analysis.get("netOrderCnt")),
            "refundDiamond": refund_diamond,
            "actualAmount": diamond_to_rmb(actual_diamond),
            "refundOrderCount": safe_int(analysis.get("totalRefundCount")),
            "repeatUserCount": safe_int(analysis.get("repeatOrderUserCnt")),
            "micAuditionCount": safe_int(analysis.get("mcAuditionNum")),
            "micDays": safe_int(analysis.get("auditionMicDaysNum")),
            "dispatchRewardAmount": diamond_to_rmb(safe_float(analysis.get("dispatchMicRewardAmt"))),
            "dispatchRewardUserCount": safe_int(analysis.get("dispatchMicRewardUserCnt")),
        }
    }

    comparison = build_comparison(monthly_data)

    return jsonify({
        "success": True,
        "data": {
            "talent": {
                "nickname": talent_info.get("nickname") or user_info.get("nickname"),
                "id": talent_id,
                "showNo": talent_info.get("showNo") or user_info.get("showNo"),
                "uid": talent_uid,
                "avatar": talent_info.get("avatar") or user_info.get("avatar"),
            },
            "dateRange": {"start": start_date, "end": end_date},
            "comparison": comparison,
        }
    })


@app.route("/api/logout", methods=["POST"])
def api_logout():
    clear_auth()
    return jsonify({"success": True, "message": "已退出登录"})
