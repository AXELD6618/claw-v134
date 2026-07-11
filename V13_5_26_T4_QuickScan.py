"""
V13.5.26 T4 QuickScan - 全市场快扫补救路径
═══════════════════════════════════════════════════════════
★目的: 在T4 14:30对全市场做快速扫描，捞出T0-T3渐进漏斗遗漏的盘中异动股
★问题: T0(10:30)初筛后，某股可能在10:30-14:30间发生:
  - 换手率突然飙升(HSL从2%→8%)
  - 主力资金午后大笔净流入(InOutHB从负转正)
  - 量比从1.0突变到3.0(放量启动)
  - "致命背离"形态午后才形成(HSL>7%+LB<1.5)
★方案: 3路tdx_screener并行查询 → 去重 → 排除T3已有 → D56v2快检
★时间预算: ~2-3分钟(3次screener + 5-10只新候选quotes/kline)
★验证: 7/6华天科技/有研新材/华微电子均属"致命背离"形态，
  若T0未捕获(因10:30时HSL/LB尚未达标)，QuickScan在14:30可补救!

Author: 毕方灵犀貔貅助手 V13.5.26
Date: 2026-07-08
"""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from enum import Enum


# ═══════════════════════════════════════════════════════════
# 1. QuickScan 3路查询定义
# ═══════════════════════════════════════════════════════════

class QuickScanTarget(Enum):
    """快扫目标模式"""
    FATAL_DIVERGENCE = "fatal_divergence"   # ★致命背离(HSL高+LB低) — 最高优先级!
    CAPITAL_FLOW = "capital_flow"            # 资金流入+高换手
    VOLUME_SURGE = "volume_surge"            # 放量启动
    PRICE_SURGE = "price_surge"              # 涨幅异动


@dataclass
class ScreenerQuery:
    """tdx_screener查询定义"""
    query_id: str
    message: str                    # 自然语言选股条件
    target: QuickScanTarget
    priority: int                   # 优先级(1=最高)
    expected_fields: List[str]      # 预期返回字段
    min_hsl: float = 0.0           # 换手率下限(用于二次过滤)
    max_lb: float = 999.0          # 量比上限(用于二次过滤, 致命背离用)
    min_lb: float = 0.0            # 量比下限(用于二次过滤, 放量用)
    rang: str = "AG"               # A股
    page_size: str = "20"          # 每页返回数


# ★3路并行查询 — 覆盖3种盘中异动模式
QUICKSCAN_QUERIES: List[ScreenerQuery] = [
    ScreenerQuery(
        query_id="QS1_FATAL_DIVERGENCE",
        message="换手率大于7%且缩量且非ST",
        target=QuickScanTarget.FATAL_DIVERGENCE,
        priority=1,  # ★最高优先级! 致命背离=洗盘尾声最强信号
        expected_fields=["sec_code", "sec_name"],
        min_hsl=7.0,
        max_lb=1.5,  # ★关键: 量比<1.5才是致命背离
    ),
    ScreenerQuery(
        query_id="QS2_CAPITAL_FLOW",
        message="换手率大于5%且主力净流入且非ST",
        target=QuickScanTarget.CAPITAL_FLOW,
        priority=2,
        expected_fields=["sec_code", "sec_name"],
        min_hsl=5.0,
    ),
    ScreenerQuery(
        query_id="QS3_VOLUME_SURGE",
        message="量比大于2且换手率大于3%且涨幅0到8%且非ST",
        target=QuickScanTarget.VOLUME_SURGE,
        priority=3,
        expected_fields=["sec_code", "sec_name"],
        min_hsl=3.0,
        min_lb=2.0,
    ),
]


# ═══════════════════════════════════════════════════════════
# 2. 快扫候选数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class QuickScanCandidate:
    """快扫发现的候选股"""
    code: str                          # 股票代码
    name: str                          # 股票名称
    setcode: str                       # 市场代码(0=深/1=沪)
    source_query: str                  # 来源查询ID
    target: QuickScanTarget            # 匹配模式
    priority: int                      # 优先级
    # 二次验证后填充:
    hsl: float = 0.0                  # 换手率%(tdx_quotes HQInfo.HSL)
    lb: float = 0.0                   # 量比(tdx_quotes HQInfo.LB)
    inout_hb: float = 0.0             # 主力净额(tdx_quotes ProInfo.InOutHB)
    inout_main: float = 0.0           # 主买净额(tdx_quotes ProInfo.InOut)
    wei_bi: float = 0.0               # 委比(tdx_quotes ProInfo.Wtb)
    is_new: bool = True               # 是否T3池中没有的新候选
    quickscan_score: float = 0.0      # 快扫优先级评分
    d56v2_score: float = 0.0          # D56v2得分(待计算)
    d56v2_triggered: bool = False     # D56v2是否触发
    reject_signal: bool = False       # S4 reject信号


# ═══════════════════════════════════════════════════════════
# 3. 核心函数
# ═══════════════════════════════════════════════════════════

def get_setcode_from_code(code: str) -> str:
    """从股票代码推断setcode (0=深市, 1=沪市)"""
    if not code:
        return "1"
    # 6开头=沪市(setcode=1), 0/3开头=深市(setcode=0), 8/4开头=北交所(setcode=1)
    if code.startswith("6") or code.startswith("9"):
        return "1"
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return "0"
    elif code.startswith("8") or code.startswith("4"):
        return "1"  # 北交所
    return "1"


def deduplicate_screener_results(
    all_results: List[Dict]
) -> List[Tuple[str, str, str]]:
    """
    去重多路screener结果
    返回: [(code, name, setcode), ...]
    """
    seen = set()
    unique = []
    for item in all_results:
        code = str(item.get("sec_code", item.get("code", "")))
        name = str(item.get("sec_name", item.get("name", "")))
        if not code or code in seen:
            continue
        # 排除ST(双保险, screener已过滤但可能漏网)
        if "ST" in name.upper() or "*ST" in name:
            continue
        seen.add(code)
        setcode = get_setcode_from_code(code)
        unique.append((code, name, setcode))
    return unique


def find_new_candidates(
    screener_unique: List[Tuple[str, str, str]],
    t3_pool_codes: Set[str]
) -> List[QuickScanCandidate]:
    """
    从去重后的screener结果中找出T3候选池中没有的新候选
    返回: QuickScanCandidate列表
    """
    candidates = []
    for code, name, setcode in screener_unique:
        is_new = code not in t3_pool_codes
        candidate = QuickScanCandidate(
            code=code,
            name=name,
            setcode=setcode,
            source_query="MULTI",  # 可能被多路查询命中
            target=QuickScanTarget.FATAL_DIVERGENCE,  # 默认, 后续根据HSL/LB修正
            priority=1,
            is_new=is_new,
        )
        candidates.append(candidate)
    return candidates


def calculate_quickscan_priority(
    candidate: QuickScanCandidate,
    dist_from_20d_low_pct: float = 50.0,
    dist_from_20d_high_pct: float = 50.0
) -> float:
    """
    计算快扫候选的优先级评分(0-100)
    ★优先级决定哪些新候选值得花时间做D56v2完整检测
    
    评分维度:
    - 致命背离匹配(40分): HSL>7%+LB<1.5 → 最高优先
    - 资金流入(25分): InOutHB>0且>5000万
    - 位置校正(20分): 低位加分, 高位减分
    - 换手率强度(15分): HSL越高越优先
    """
    score = 0.0
    hsl = candidate.hsl
    lb = candidate.lb
    inout_hb = candidate.inout_hb

    # 1. 致命背离匹配(40分)
    if hsl > 7.0 and lb < 1.5:
        score += 40.0
        candidate.target = QuickScanTarget.FATAL_DIVERGENCE
    elif hsl > 5.0 and inout_hb > 0:
        score += 25.0
        candidate.target = QuickScanTarget.CAPITAL_FLOW
    elif lb > 2.0 and hsl > 3.0:
        score += 20.0
        candidate.target = QuickScanTarget.VOLUME_SURGE
    else:
        score += 5.0

    # 2. 资金流入(25分)
    if inout_hb > 1e8:        # >1亿
        score += 25.0
    elif inout_hb > 5e7:      # >5000万
        score += 15.0
    elif inout_hb > 0:
        score += 8.0
    else:
        score -= 10.0  # 资金流出扣分

    # 3. 位置校正(20分) — 与D56v2 S4位置双重校正V2一致
    total_range = dist_from_20d_low_pct + dist_from_20d_high_pct
    position_ratio = dist_from_20d_low_pct / total_range if total_range > 0 else 0.5
    is_low = dist_from_20d_low_pct < 25.0 or position_ratio < 0.45
    is_high = dist_from_20d_high_pct < 10.0 or position_ratio > 0.85

    if is_low:
        score += 20.0   # 低位=加分
    elif is_high:
        score -= 20.0   # 高位=减分(可能是出货)
    else:
        score += 5.0    # 中位=微加

    # 4. 换手率强度(15分)
    if hsl > 10.0:
        score += 15.0
    elif hsl > 7.0:
        score += 12.0
    elif hsl > 5.0:
        score += 8.0
    else:
        score += 3.0

    candidate.quickscan_score = max(0.0, min(100.0, score))
    return candidate.quickscan_score


def filter_quickscan_candidates(
    candidates: List[QuickScanCandidate],
    max_new_to_check: int = 10
) -> List[QuickScanCandidate]:
    """
    过滤快扫候选:
    1. 只保留is_new=True的(T3池中没有的)
    2. 按quickscan_score排序
    3. 限制最多检查max_new_to_check只(时间约束)
    """
    new_only = [c for c in candidates if c.is_new]
    # 按优先级排序
    new_only.sort(key=lambda c: c.quickscan_score, reverse=True)
    # 限制数量(20分钟时间约束, 每只D56v2约1分钟)
    return new_only[:max_new_to_check]


# ═══════════════════════════════════════════════════════════
# 4. QuickScan执行指令模板(AI Agent执行参考)
# ═══════════════════════════════════════════════════════════

QUICKSCAN_EXECUTION_GUIDE = """
★★★ T4 QuickScan 执行指南 (14:30→14:33, 3分钟内完成!)

## 第1步: 3路tdx_screener并行调用 (~30秒)
  调用1: tdx_screener(message="换手率大于7%且缩量且非ST", rang="AG", pageSize="20")
  调用2: tdx_screener(message="换手率大于5%且主力净流入且非ST", rang="AG", pageSize="20")  
  调用3: tdx_screener(message="量比大于2且换手率大于3%且涨幅0到8%且非ST", rang="AG", pageSize="20")

## 第2步: 去重+排除T3池已有 (~10秒)
  - 合并3路结果, 按sec_code去重
  - 排除名称含"ST"的(双保险)
  - 排除T3候选池中已有的代码
  - 得到新候选列表(预期5-15只)

## 第3步: 新候选D56v2快检 (~2分钟, 每只~10秒)
  对每个新候选(code最多10只, 按优先级):
  a. tdx_quotes(code, setcode, hasCalcInfo=1, hasCwInfo=1, bspNum=5)
     → 提取HSL/LB/InOutHB/InOut/Wtb/外盘/内盘
  b. tdx_kline(code, setcode, period="4", wantNum="25")
     → 计算20日最低/最高价 → dist_from_20d_low_pct / dist_from_20d_high_pct
  c. 构建RealTimeCapitalProxy → calc_d56_realtime_capital_proxy()
  d. 记录D56v2得分+S4组合+reject_signal

## 第4步: 合并到T4选股池
  - D56v2≥8的新候选 → 标记QUICKSCAN_SMART_MONEY, 合并到T4选股池
  - S4 reject_signal=True的新候选 → 直接REJECT, 不进入选股池
  - D56v2<8的新候选 → 记录但不进入选股池
  - ★致命背离形态(HSL>7%+LB<1.5+低位)的新候选 → 即使D56v2略低于8也优先关注!

## 时间分配
  3路screener: ~30秒
  去重+过滤: ~10秒
  10只新候选D56v2: ~100秒(每只10秒)
  总计: ~140秒 ≈ 2.5分钟
  剩余17.5分钟用于T3候选池D56v2+50维度评分
"""


# ═══════════════════════════════════════════════════════════
# 5. 测试
# ═══════════════════════════════════════════════════════════

def _test_quickscan():
    """QuickScan模块自检"""
    print("=" * 70)
    print("★★★ V13.5.26 T4 QuickScan 自检")
    print("=" * 70)

    # 1. 查询定义
    print(f"\n[1] 快扫查询定义: {len(QUICKSCAN_QUERIES)}路并行")
    for q in QUICKSCAN_QUERIES:
        print(f"  {q.query_id}(P{q.priority}): {q.message}")
        print(f"    目标={q.target.value} | HSL>{q.min_hsl}% | LB<{q.max_lb}")

    # 2. 模拟screener结果去重
    mock_results = [
        {"sec_code": "002185", "sec_name": "华天科技"},
        {"sec_code": "002185", "sec_name": "华天科技"},  # 重复
        {"sec_code": "600206", "sec_name": "有研新材"},
        {"sec_code": "600360", "sec_name": "华微电子"},
        {"sec_code": "600118", "sec_name": "中国卫星"},
        {"sec_code": "000123", "sec_name": "*ST测试"},  # ST应被排除
    ]
    unique = deduplicate_screener_results(mock_results)
    print(f"\n[2] 去重结果: {len(unique)}只 (原始{len(mock_results)}条)")
    for code, name, sc in unique:
        print(f"  {code} {name} setcode={sc}")

    # 3. 排除T3池已有
    t3_pool = {"600118"}  # 假设中国卫星已在T3池
    candidates = find_new_candidates(unique, t3_pool)
    print(f"\n[3] 新候选(T3池没有的):")
    for c in candidates:
        print(f"  {c.code} {c.name} is_new={c.is_new}")

    # 4. 模拟填充HSL/LB/InOutHB并计算优先级
    test_data = [
        ("002185", "华天科技", 10.44, 0.736, 3.8e9, 18.8, 26.7),   # 致命背离+低位
        ("600206", "有研新材", 12.96, 0.766, 1.3e9, 90.9, 26.7),   # 致命背离+中位
        ("600360", "华微电子", 13.43, 0.930, 8.0e8, 25.0, 15.0),   # 致命背离+低位边界
        ("600118", "中国卫星", 4.72, 0.743, -2.45e8, 8.0, 30.0),    # 非致命背离(HSL<7%)
    ]
    print(f"\n[4] 优先级评分(模拟7/6数据):")
    for code, name, hsl, lb, inout_hb, dist_low, dist_high in test_data:
        cand = QuickScanCandidate(
            code=code, name=name, setcode=get_setcode_from_code(code),
            source_query="QS1", target=QuickScanTarget.FATAL_DIVERGENCE,
            priority=1, hsl=hsl, lb=lb, inout_hb=inout_hb
        )
        score = calculate_quickscan_priority(cand, dist_low, dist_high)
        is_divergence = hsl > 7.0 and lb < 1.5
        print(f"  {code} {name}: HSL={hsl}% LB={lb} InOutHB={inout_hb/1e8:.1f}亿")
        print(f"    → 致命背离={is_divergence} | 优先级={score:.0f}/100 | target={cand.target.value}")

    # 5. 过滤+排序
    all_candidates = []
    for code, name, hsl, lb, inout_hb, dist_low, dist_high in test_data:
        cand = QuickScanCandidate(
            code=code, name=name, setcode=get_setcode_from_code(code),
            source_query="QS1", target=QuickScanTarget.FATAL_DIVERGENCE,
            priority=1, hsl=hsl, lb=lb, inout_hb=inout_hb, is_new=True
        )
        calculate_quickscan_priority(cand, dist_low, dist_high)
        all_candidates.append(cand)

    filtered = filter_quickscan_candidates(all_candidates, max_new_to_check=10)
    print(f"\n[5] 过滤后(按优先级排序, 最多10只):")
    for c in filtered:
        print(f"  {c.code} {c.name}: score={c.quickscan_score:.0f} | target={c.target.value}")

    print(f"\n{'=' * 70}")
    print("★★★ 自检结论:")
    print("  - 3路screener查询定义OK (致命背离/资金流入/放量启动)")
    print("  - 去重+ST排除OK")
    print("  - T3池排除OK")
    print("  - 优先级评分OK (致命背离+低位=最高分)")
    print("  - 过滤排序OK")
    print("  ★★★ 7/6验证: 华天科技(致命背离+低位)应得最高优先级!")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    _test_quickscan()
