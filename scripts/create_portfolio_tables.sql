-- 创建持仓管理相关表
-- 在 Supabase SQL Editor 中执行此脚本

-- 1. 投资组合表
CREATE TABLE IF NOT EXISTS portfolios (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT 'Portfolio',
    free_cash NUMERIC(20,2) DEFAULT 0.0,
    total_equity NUMERIC(20,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 持仓表
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT,
    shares NUMERIC(20,0) NOT NULL DEFAULT 0,
    cost_price NUMERIC(12,4),
    buy_date TIMESTAMPTZ,
    strategy TEXT,
    stop_loss NUMERIC(12,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT portfolio_positions_unique UNIQUE (portfolio_id, code)
);

-- 3. 交易订单表
CREATE TABLE IF NOT EXISTS trade_orders (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    run_id TEXT,
    trade_date TEXT NOT NULL,
    model TEXT,
    market_view TEXT,
    code TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    shares NUMERIC(20,0),
    price_hint NUMERIC(12,4),
    amount NUMERIC(20,2),
    stop_loss NUMERIC(12,4),
    reason TEXT,
    tape_condition TEXT,
    invalidate_condition TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX idx_portfolio_positions_portfolio_id ON portfolio_positions(portfolio_id);
CREATE INDEX idx_portfolio_positions_code ON portfolio_positions(code);
CREATE INDEX idx_trade_orders_portfolio_id ON trade_orders(portfolio_id);
CREATE INDEX idx_trade_orders_trade_date ON trade_orders(trade_date);
CREATE INDEX idx_trade_orders_run_id ON trade_orders(run_id);

-- 创建更新触发器
CREATE TRIGGER update_portfolios_updated_at
    BEFORE UPDATE ON portfolios
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_portfolio_positions_updated_at
    BEFORE UPDATE ON portfolio_positions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_trade_orders_updated_at
    BEFORE UPDATE ON trade_orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 启用 RLS
ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_orders ENABLE ROW LEVEL SECURITY;

-- 策略：认证用户可以管理自己的投资组合
-- 假设 portfolio_id = user_id，通过 auth.uid() 验证
CREATE POLICY "Users can manage own portfolio"
    ON portfolios FOR ALL
    USING (auth.uid()::TEXT = portfolio_id)
    WITH CHECK (auth.uid()::TEXT = portfolio_id);

CREATE POLICY "Users can manage own positions"
    ON portfolio_positions FOR ALL
    USING (
        portfolio_id IN (
            SELECT portfolio_id FROM portfolios WHERE auth.uid()::TEXT = portfolio_id
        )
    )
    WITH CHECK (
        portfolio_id IN (
            SELECT portfolio_id FROM portfolios WHERE auth.uid()::TEXT = portfolio_id
        )
    );

CREATE POLICY "Users can manage own orders"
    ON trade_orders FOR ALL
    USING (
        portfolio_id IN (
            SELECT portfolio_id FROM portfolios WHERE auth.uid()::TEXT = portfolio_id
        )
    )
    WITH CHECK (
        portfolio_id IN (
            SELECT portfolio_id FROM portfolios WHERE auth.uid()::TEXT = portfolio_id
        )
    );

-- 服务角色完全访问
CREATE POLICY "Service role full access"
    ON portfolios FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

CREATE POLICY "Service role full access"
    ON portfolio_positions FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

CREATE POLICY "Service role full access"
    ON trade_orders FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

-- 添加注释
COMMENT ON TABLE portfolios IS '投资组合表 - 用户持仓组合信息';
COMMENT ON TABLE portfolio_positions IS '持仓明细表 - 记录每只股票的持仓';
COMMENT ON TABLE trade_orders IS '交易订单表 - 记录交易指令和执行情况';
