-- 创建 stock_hist_cache 表
-- 在 Supabase SQL Editor 中执行此脚本

CREATE TABLE IF NOT EXISTS stock_hist_cache (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    adjust TEXT NOT NULL DEFAULT 'qfq',
    date DATE NOT NULL,

    -- 行情数据
    open NUMERIC(12,4),
    high NUMERIC(12,4),
    low NUMERIC(12,4),
    close NUMERIC(12,4),
    volume NUMERIC(20,2),
    amount NUMERIC(20,2),
    pct_chg NUMERIC(10,4),

    -- 元数据
    source TEXT DEFAULT 'cache',
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- 唯一约束：同一股票、复权方式、日期的记录唯一
    CONSTRAINT stock_hist_cache_unique UNIQUE (symbol, adjust, date)
);

-- 创建索引
CREATE INDEX idx_stock_hist_cache_symbol_adjust ON stock_hist_cache(symbol, adjust);
CREATE INDEX idx_stock_hist_cache_date ON stock_hist_cache(date);
CREATE INDEX idx_stock_hist_cache_symbol_date ON stock_hist_cache(symbol, date);

-- 创建更新触发器
CREATE TRIGGER update_stock_hist_cache_updated_at
    BEFORE UPDATE ON stock_hist_cache
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 启用 RLS
ALTER TABLE stock_hist_cache ENABLE ROW LEVEL SECURITY;

-- 允许所有认证用户读取（行情数据是公开的）
CREATE POLICY "Authenticated users can read"
    ON stock_hist_cache FOR SELECT
    TO authenticated
    USING (true);

-- 允许认证用户插入（用于缓存写入）
CREATE POLICY "Authenticated users can insert"
    ON stock_hist_cache FOR INSERT
    TO authenticated
    WITH CHECK (true);

-- 允许认证用户更新自己的缓存
CREATE POLICY "Authenticated users can update"
    ON stock_hist_cache FOR UPDATE
    TO authenticated
    USING (true);

-- 服务角色完全访问
CREATE POLICY "Service role full access"
    ON stock_hist_cache FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

COMMENT ON TABLE stock_hist_cache IS '股票历史行情缓存表 - 存储 A 股股票日线行情数据';
