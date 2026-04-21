-- 创建 user_settings 表
-- 在 Supabase SQL Editor 中执行此脚本

CREATE TABLE IF NOT EXISTS user_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 通知配置
    feishu_webhook TEXT,
    wecom_webhook TEXT,
    dingtalk_webhook TEXT,

    -- 读盘室供应商
    chat_provider TEXT DEFAULT 'gemini',

    -- 大模型配置
    gemini_api_key TEXT,
    gemini_model TEXT DEFAULT 'gemini-2.0-flash',
    gemini_base_url TEXT,

    openai_api_key TEXT,
    openai_model TEXT DEFAULT 'gpt-4.1-mini',
    openai_base_url TEXT DEFAULT 'https://api.openai.com/v1',

    deepseek_api_key TEXT,
    deepseek_model TEXT DEFAULT 'deepseek-chat',
    deepseek_base_url TEXT DEFAULT 'https://api.deepseek.com/v1',

    -- 自定义供应商配置（JSON）
    custom_providers JSONB DEFAULT '{}',

    -- 数据源
    tushare_token TEXT,

    -- Telegram 私密推送
    tg_bot_token TEXT,
    tg_chat_id TEXT,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建更新触发器
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_user_settings_updated_at
    BEFORE UPDATE ON user_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 启用 RLS（行级安全）
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;

-- 创建策略：用户只能读写自己的设置
CREATE POLICY "Users can view own settings"
    ON user_settings FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can update own settings"
    ON user_settings FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own settings"
    ON user_settings FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- 创建服务角色完全访问策略（用于服务端操作）
CREATE POLICY "Service role full access"
    ON user_settings FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

-- 创建索引
CREATE INDEX idx_user_settings_user_id ON user_settings(user_id);

COMMENT ON TABLE user_settings IS '用户配置表：存储 API Key、通知 Webhook、模型偏好等';
