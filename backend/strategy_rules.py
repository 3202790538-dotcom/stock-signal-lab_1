"""Versioned research rules, shared by the simulator and its explanation UI."""
from copy import deepcopy

VERSION = 'complete-rules-v1.0'
SYMBOLS = ['NVDA', 'MRVL', 'AVGO', 'MU', 'GOOG']
SEMICONDUCTORS = frozenset(['NVDA', 'MRVL', 'AVGO', 'MU'])
PROFILES = {
    'conservative': dict(id='conservative', label='保守', drawdownPct=10, tradeRiskPct=.25,
                         grossCapPct=50, singleCapPct=15, sectorCapPct=30, maxPositions=4, openRiskCapPct=1),
    'balanced': dict(id='balanced', label='均衡', drawdownPct=20, tradeRiskPct=.5,
                     grossCapPct=75, singleCapPct=20, sectorCapPct=45, maxPositions=5, openRiskCapPct=2),
    'aggressive': dict(id='aggressive', label='进取', drawdownPct=30, tradeRiskPct=.75,
                       grossCapPct=100, singleCapPct=25, sectorCapPct=60, maxPositions=5, openRiskCapPct=3),
}
TEMPLATES = {
    'breakout': dict(id='breakout', label='趋势突破', description='放量突破区间，顺势持有数周',
                     defaultParameters={'lookback': 20, 'holdingDays': 20}),
    'pullback': dict(id='pullback', label='趋势回调', description='上涨趋势中等待回调后的重新走强',
                    defaultParameters={'pullbackDays': 5, 'holdingDays': 20}),
    'rotation': dict(id='rotation', label='动量轮动', description='每周比较强弱，以强势股票替换落后股票',
                    defaultParameters={'momentumDays': 63, 'topK': 3, 'holdingDays': 60}),
}


def get_profile(profile_id):
    if not isinstance(profile_id, str) or profile_id not in PROFILES:
        raise ValueError('未知风险档位')
    return deepcopy(PROFILES[profile_id])


def candidate_parameters(template_id):
    if template_id == 'breakout':
        return [{'lookback': n, 'holdingDays': h} for n in (20, 40, 60) for h in (20, 40)]
    if template_id == 'pullback':
        return [{'pullbackDays': n, 'holdingDays': h} for n in (3, 5, 10) for h in (10, 20)]
    if template_id == 'rotation':
        return [{'momentumDays': n, 'topK': k, 'holdingDays': 60} for n in (63, 126) for k in (2, 3, 4)]
    raise ValueError('未知策略模板')


def describe_rules(template_id, profile_id, parameters=None):
    candidate_parameters(template_id)
    p = get_profile(profile_id)
    t = TEMPLATES[template_id]
    params = deepcopy(parameters if parameters is not None else t['defaultParameters'])
    if params not in candidate_parameters(template_id):
        raise ValueError('策略参数不在预定义候选中')
    if template_id == 'breakout':
        entry = [f"收盘 > SMA50 > SMA200，且严格突破此前 {params['lookback']} 日最高价（不含当日）。",
                 '成交量至少为此前 20 日均量的 1.2 倍，63 日涨幅高于 SPY。',
                 '同时出现多个信号，按相对 SPY 的 63 日涨幅排序，同分按代码排序。']
        exit_rule = f"收盘跌破 SMA20，或持有满 {params['holdingDays']} 个交易日，下一交易日开盘退出。"
    elif template_id == 'pullback':
        entry = [f"收盘 > SMA50 > SMA200；最近 {params['pullbackDays']} 日曾触及 SMA20，但每天收盘仍高于各自 SMA50。",
                 '当日收盘重新高于 SMA20 且突破前一日最高价；成交量至少达到此前 20 日均量。',
                 '63 日涨幅高于 SPY；多个信号按相对 SPY 的 63 日涨幅排序，同分按代码排序。']
        exit_rule = f"收盘跌破 SMA50，或持有满 {params['holdingDays']} 个交易日，下一交易日开盘退出。"
    else:
        entry = [f"每周首个交易日，以上一交易日收盘数据排名：收盘 > SMA50 > SMA200，{params['momentumDays']} 日涨幅为正。",
                 f"按 {params['momentumDays']} 日涨幅取前 {params['topK']} 名，同分按代码排序；先退出落榜股票，再补空位。",
                 '市场走弱不会清空排名；只禁止新买入。保留仍在名单的头寸，不追加、不强制等权。']
        exit_rule = '每周退出不在入选名单的股票；日常收盘跌破 SMA50 或持有满 60 日，次日开盘退出。'
    sections = [
        {'title': '选股与买入信号', 'items': [
            '股票池：NVDA、MRVL、AVGO、MU、GOOG；SPCX、SKHY 历史不足，本版不纳入主研究。',
            '只使用已完成日线。新买入须 SPY 收盘高于 SMA200；股价至少 10 美元、前 20 日平均成交额至少 2,000 万美元。', *entry]},
        {'title': '成交与仓位计算', 'items': [
            '收盘确认，次日开盘附近模拟买入。开盘与信号收盘相差超过 0.5 × ATR14 则跳过。',
            '初始止损 = 买入价 − 2 × 信号日 ATR14。ATR14 为近 14 日真实波幅的简单平均，SMA 为简单移动平均。',
            f"每笔初始止损预算为净值的 {p['tradeRiskPct']:g}%；最多 {p['maxPositions']} 只股票。",
            f"总仓位 ≤ {p['grossCapPct']}%，单股 ≤ {p['singleCapPct']}%，半导体合计 ≤ {p['sectorCapPct']}%，未平仓初始风险合计 ≤ {p['openRiskCapPct']}%。",
            '股数 = 各约束可买股数的最小值，再向下取整：每笔风险/止损距离、单股/行业/总仓位余量、未平仓风险余量、含手续费现金、均量的 0.1%。',
            f"例如净值 $10,000、本档每笔风险 {p['tradeRiskPct']:g}%、买价 $100、ATR=$3：风险预算 ${100*p['tradeRiskPct']:g}，止损距离 $6，风险约束最多买 {int(100*p['tradeRiskPct']/6)} 股；其他限额可能进一步减少。",
            '同一股票不加仓、不借款、不做空。完全退出后等 5 个完整交易日；每个开盘按单股、行业、总仓位、初始风险合计顺序削减超限持仓。']},
        {'title': '止损与卖出', 'items': [
            exit_rule,
            '初始止损在买入当日有效；每日收盘把止损上调至 max(旧止损，入场以来最高收盘 − 3 × 当日 ATR14)，下一交易日起生效。',
            '跳空跌破止损按开盘价退出；盘中触发按止损价退出，两者都扣卖出滑点。止损不能保证最大亏损。',
            '没有固定百分比止盈，靠移动止损与趋势退出保护利润。计划开盘卖出及开盘止损先于买入，盘中卖出现金不能回头用于当日开盘。']},
        {'title': '组合暂停与恢复', 'items': [
            '组合回撤以每日收盘净值判断，次日开盘执行减仓或清仓；不代表盘中实时监控或保证成交价。',
            f"本轮回撤达到 {p['drawdownPct']/3:g}%：次日风险及市值上限减半，已有超额仓位也减下来。至少保持 5 日；回撤低于 {p['drawdownPct']/6:g}% 且 SPY 连续 5 日高于 SMA200 才恢复上限。",
            f"本轮回撤达到 {2*p['drawdownPct']/3:g}%：次日全部退出，暂停至少 10 个完整交易日。之后还须 SPY 连续 5 日高于 SMA200，才以半档风险恢复 5 日。",
            f"全窗口回撤达到 {p['drawdownPct']}%：次日退出，并在该窗口内停止新买入。重启只重置本轮风控高点，不抹掉总历史回撤。",
            'SPY 不高于 SMA200 时不买入，现有头寸仍执行退出规则。数据缺失时不生成最新信号。']},
        {'title': '收益目标和验证方法', 'items': [
            '每个模板只有 6 个预先定义候选；风险档位决定限额，收益目标只改变训练排序，不加杠杆凑收益。',
            '200 日预热后，前 70% 选参数，后 30% 验证；两个窗口分别从 $10,000 现金开始。完整模拟包含训练段，不是独立验证结果。',
            '未达标就显示未达标；历史达标也不代表未来可盈利。反复查看后段结果会污染验证，短期年化不可当作长期承诺。']},
        {'title': '费用与操作明细', 'items': [
            '每边手续费和滑点各 5 bps（0.05%），另显示翻倍成本压力。现金不计息，税、真实最低佣金和市场冲击未建模。',
            '策略与两个基准统一按期末收盘估值，不假装清仓；未平仓不计入胜率。同一头寸部分减仓仍属于一笔交易，完全退出后汇总净盈亏。',
            '展开每笔明细可核对信号条件、数量约束、成交费用、每次止损上调及退出原因；跳过的信号、组合风控事件也可查看。',
            '这是模拟操作记录，不是 IBKR 真实成交记录。行情打开时自动同步，IBKR 账户仍手动同步。']},
    ]
    return dict(version=VERSION, templateId=template_id, templateLabel=t['label'], profile=p,
                parameters=params, sections=sections, warnings=[
                    '原始价格探索回测，未处理拆股和分红；日线开盘附近成交为近似，未经分钟级撮合验证。',
                    '收益目标不是承诺，回撤阈值也不能保证实际损失上限；本功能不连接实盘下单。'])


def rules_catalog():
    return {'version': VERSION, 'templates': deepcopy(list(TEMPLATES.values())),
            'profiles': deepcopy(list(PROFILES.values())),
            'defaults': {'templateId': 'breakout', 'riskProfile': 'aggressive', 'targetReturnPct': 50},
            'ruleSets': {f'{t}:{p}': describe_rules(t, p) for t in TEMPLATES for p in PROFILES}}
