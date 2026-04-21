-- 创建 recommendation_tracking 表
-- 在 Supabase SQL Editor 中执行此脚本

CREATE TABLE IF NOT EXISTS recommendation_tracking (
    id BIGSERIAL PRIMARY KEY,
    code BIGINT NOT NULL,
    name TEXT,
    recommend_reason TEXT,
    recommend_date BIGINT NOT NULL,
    initial_price DECIMAL(10, 4),
    current_price DECIMAL(10, 4),
    change_pct DECIMAL(8, 4),
    recommend_count BIGINT DEFAULT 1,
    funnel_score DECIMAL(8, 4),
    is_ai_recommended BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- 唯一约束：同一只股票在同一推荐日只能有一条记录
    UNIQUE (code, recommend_date)
);

-- 创建更新触发器
CREATE OR REPLACE FUNCTION update_recommendation_tracking_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_recommendation_tracking_updated_at
    BEFORE UPDATE ON recommendation_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_recommendation_tracking_updated_at();

-- 启用 RLS（行级安全）
ALTER TABLE recommendation_tracking ENABLE ROW LEVEL SECURITY;

-- 创建策略：所有用户都可以读取（公开推荐数据）
CREATE POLICY "Anyone can view recommendations"
    ON recommendation_tracking FOR SELECT
    USING (true);

-- 创建策略：只有服务端可以写入（通过 service_role）
CREATE POLICY "Service role can insert"
    ON recommendation_tracking FOR INSERT
    WITH CHECK (auth.jwt() ->> 'role' = 'service_role');

CREATE POLICY "Service role can update"
    ON recommendation_tracking FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

-- 创建索引
CREATE INDEX idx_recommendation_tracking_code ON recommendation_tracking(code);
CREATE INDEX idx_recommendation_tracking_recommend_date ON recommendation_tracking(recommend_date);
CREATE INDEX idx_recommendation_tracking_ai ON recommendation_tracking(is_ai_recommended);

COMMENT ON TABLE recommendation_tracking IS '推荐跟踪表：记录每日定时任务推荐的股票及其后续表现';
COMMENT ON COLUMN recommendation_tracking.code IS '股票代码（纯数字，如 1000001）';
COMMENT ON COLUMN recommendation_tracking.recommend_date IS '推荐日期（YYYYMMDD 格式，如 20260420）';
COMMENT ON COLUMN recommendation_tracking.recommend_count IS '该股票被推荐的累计次数';
COMMENT ON COLUMN recommendation_tracking.is_ai_recommended IS '是否被 AI 批量研报推荐为起跳板';
