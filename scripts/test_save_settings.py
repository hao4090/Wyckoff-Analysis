import os
import sys
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from integrations.supabase_base import create_admin_client

# 获取用户列表
admin = create_admin_client()
users = admin.auth.admin.list_users()
if not users:
    print("No users found")
    sys.exit(1)

user = users[0]
user_id = user.id
print(f"Testing with user: {user_id} ({user.email})")

# 创建 anon client
supabase_url = os.getenv('SUPABASE_URL')
supabase_anon_key = os.getenv('SUPABASE_KEY')
supabase = create_client(supabase_url, supabase_anon_key)

# 获取当前用户的 access token - 直接用 refresh token 刷新
from integrations.supabase_client import get_supabase_client, _apply_user_session
import streamlit as st

# 模拟 Streamlit session
class FakeSessionState:
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.user = {'id': user_id}

# 用 admin 生成一个有效的 session
try:
    # 创建一个测试用的临时用户 session
    import requests

    # 直接用 email + password 登录（如果知道密码）或者用 admin 生成 link
    auth_url = f"{supabase_url}/auth/v1/admin/generate_link"
    headers = {
        'apikey': supabase_anon_key,
        'Authorization': f'Bearer {os.getenv("SUPABASE_SERVICE_ROLE_KEY")}',
        'Content-Type': 'application/json'
    }
    body = {"type": "recovery", "email": user.email}
    resp = requests.post(auth_url, headers=headers, json=body)
    print(f"Generate link response: {resp.status_code}")

    # 无法直接拿到 access token，改用 update 测试
    print("Testing with update instead of upsert...")

    # 用 anon key + RLS 测试 update
    test_data = {"feishu_webhook": "rls-test-webhook"}
    result = supabase.table("user_settings").update(test_data).eq("user_id", user_id).execute()
    print(f"Update with anon key (no auth): {result.data if result.data else 'Blocked by RLS'}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
