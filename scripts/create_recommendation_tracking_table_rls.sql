-- ============================================================
-- 增强版 recommendation_tracking 表创建脚本
-- 包含 RLS 全面保护和删除审计
-- 在 Supabase SQL Editor 中执行此脚本
-- ============================================================

-- ─────────────────────────────────────────
-- 1. 创建审计日志表（记录所有数据变更）
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendation_tracking_audit (
    id BIGserial PRIMARY KEY,
    op_type TEXT NOT NULL CHECK (op_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')),
    record_id BIGINT,
    record_code BIGINT,
    record_recommend_date BIGINT,
    old_data JSONB,
    new_data JSONB,
    performed_by TEXT,
    performed_at TIMESTAMPTZ DEFAULT NOW(),
    source_ip TEXT,
    client_info TEXT
);

COMMENT ON TABLE recommendation_tracking_audit IS 'recommendation_tracking 表的变更审计日志';

-- 审计表也启用 RLS（只有 service_role 可读写）
ALTER TABLE recommendation_tracking_audit ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on audit"
    ON recommendation_tracking_audit FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role')
    WITH CHECK (auth.jwt() ->> 'role' = 'service_role');

CREATE INDEX IF NOT EXISTS idx_audit_record ON recommendation_tracking_audit(record_id, record_recommend_date);
CREATE INDEX IF NOT EXISTS idx_audit_op_type ON recommendation_tracking_audit(op_type);
CREATE INDEX IF NOT EXISTS idx_audit_performed_at ON recommendation_tracking_audit(performed_at);

-- ─────────────────────────────────────────
-- 2. 创建推荐跟踪表（如已存在则跳过）
-- ─────────────────────────────────────────
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

DROP TRIGGER IF EXISTS update_recommendation_tracking_updated_at ON recommendation_tracking;
CREATE TRIGGER update_recommendation_tracking_updated_at
    BEFORE UPDATE ON recommendation_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_recommendation_tracking_updated_at();

-- ─────────────────────────────────────────
-- 3. 审计触发器函数（记录 INSERT/UPDATE/DELETE）
-- ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION log_recommendation_tracking_changes()
RETURNS TRIGGER AS $$
DECLARE
    audit_row recommendation_tracking_audit%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO recommendation_tracking_audit (
            op_type, record_id, record_code, record_recommend_date,
            old_data, performed_by, source_ip, client_info
        ) VALUES (
            'DELETE',
            OLD.id, OLD.code, OLD.recommend_date,
            to_jsonb(OLD)::jsonb,
            current_setting('request.jwt->>role', true),
            NULL,
            NULL
        );
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO recommendation_tracking_audit (
            op_type, record_id, record_code, record_recommend_date,
            old_data, new_data, performed_by, source_ip, client_info
        ) VALUES (
            'UPDATE',
            NEW.id, NEW.code, NEW.recommend_date,
            to_jsonb(OLD)::jsonb,
            to_jsonb(NEW)::jsonb,
            current_setting('request.jwt->>role', true),
            NULL,
            NULL
        );
        RETURN NEW;
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO recommendation_tracking_audit (
            op_type, record_id, record_code, record_recommend_date,
            new_data, performed_by, source_ip, client_info
        ) VALUES (
            'INSERT',
            NEW.id, NEW.code, NEW.recommend_date,
            to_jsonb(NEW)::jsonb,
            current_setting('request.jwt->>role', true),
            NULL,
            NULL
        );
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_recommendation_tracking ON recommendation_tracking;
CREATE TRIGGER audit_recommendation_tracking
    AFTER INSERT OR UPDATE OR DELETE ON recommendation_tracking
    FOR EACH ROW
    EXECUTE FUNCTION log_recommendation_tracking_changes();

-- ─────────────────────────────────────────
-- 4. 启用 RLS（行级安全）
-- ─────────────────────────────────────────
ALTER TABLE recommendation_tracking ENABLE ROW LEVEL SECURITY;

-- 清除旧策略（重新运行脚本时避免冲突）
DROP POLICY IF EXISTS "Anyone can view recommendations" ON recommendation_tracking;
DROP POLICY IF EXISTS "Service role can insert" ON recommendation_tracking;
DROP POLICY IF EXISTS "Service role can update" ON recommendation_tracking;
DROP POLICY IF EXISTS "Service role can delete" ON recommendation_tracking;

-- 策略 1：公开读取（任何人都可查看推荐数据）
CREATE POLICY "Anyone can view recommendations"
    ON recommendation_tracking FOR SELECT
    USING (true);

-- 策略 2：仅 service_role 可 INSERT
CREATE POLICY "Service role can insert"
    ON recommendation_tracking FOR INSERT
    WITH CHECK (auth.jwt() ->> 'role' = 'service_role');

-- 策略 3：仅 service_role 可 UPDATE（非 DELETE）
CREATE POLICY "Service role can update"
    ON recommendation_tracking FOR UPDATE
    USING (auth.jwt() ->> 'role' = 'service_role')
    WITH CHECK (auth.jwt() ->> 'role' = 'service_role');

-- 策略 4：【关键】禁止通过 API 执行 DELETE
-- 即使是 service_role，也无法通过 REST API 删除记录
-- 外部数据库连接（如 Supabase SQL Editor 手动操作）仍可删除
-- 这保护数据不被有 RLS 权限的应用误删
CREATE POLICY "Deny delete from API"
    ON recommendation_tracking FOR DELETE
    USING (false)
    WITH CHECK (false);

-- ─────────────────────────────────────────
-- 5. 创建索引
-- ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_recommendation_tracking_code ON recommendation_tracking(code);
CREATE INDEX IF NOT EXISTS idx_recommendation_tracking_recommend_date ON recommendation_tracking(recommend_date);
CREATE INDEX IF NOT EXISTS idx_recommendation_tracking_ai ON recommendation_tracking(is_ai_recommended);

-- ─────────────────────────────────────────
-- 6. 注释
-- ─────────────────────────────────────────
COMMENT ON TABLE recommendation_tracking IS '推荐跟踪表：记录每日定时任务推荐的股票及其后续表现';
COMMENT ON COLUMN recommendation_tracking.code IS '股票代码（纯数字，如 1000001）';
COMMENT ON COLUMN recommendation_tracking.recommend_date IS '推荐日期（YYYYMMDD 格式，如 20260420）';
COMMENT ON COLUMN recommendation_tracking.recommend_count IS '该股票被推荐的累计次数';
COMMENT ON COLUMN recommendation_tracking.is_ai_recommended IS '是否被 AI 批量研报推荐为起跳板';

-- ============================================================
-- 验证查询（执行后查看结果）
-- ============================================================
-- 查看当前 RLS 策略：
-- SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual FROM pg_policies WHERE tablename = 'recommendation_tracking';

-- 查看审计日志最近记录：
-- SELECT * FROM recommendation_tracking_audit ORDER BY performed_at DESC LIMIT 10;