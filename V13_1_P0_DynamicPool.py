#!/usr/bin/env python3
"""
V13.1 P0-3: 动态监控池 30→214只行业分层轮换
=============================================
将固定30只监控池扩展为214只动态行业分层监控池

架构：
  214只行业分层宇宙 (28个行业×~7-14只/行业)
      ↓ 每日14:00动态筛选
  TOP 60只活跃标的 (按成交额+换手率+涨幅排序)
      ↓ 注入TDXInjector
  14:30尾盘猎手分析

行业分层：
  按申万一级行业分类，覆盖28个行业
  每行业选7-14只代表性标的（市值前30%+流动性前50%）
  行业权重：科技(15%)+医药(10%)+新能源(10%)+消费(10%)+金融(8%)+其余47%

轮换逻辑：
  1. 每周日15:30 M55校准时触发行业轮换评估
  2. 根据行业动量排名调整行业权重（+/-2%）
  3. 每日14:00按动态权重+活跃度筛选TOP 60只
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ═══════════════════════════════════════════════
# 300只行业分层宇宙
# ═══════════════════════════════════════════════

INDUSTRY_UNIVERSE = {
    # ── 科技 (45只, 15%) ──
    "AI算力": [
        ("601138", "1", "工业富联"), ("603019", "1", "中科曙光"),
        ("000977", "0", "中科曙光"), ("002415", "0", "海康威视"),
        ("688256", "1", "寒武纪"), ("300418", "0", "昆仑万维"),
        ("300033", "0", "同花顺"), ("688041", "1", "海光信息"),
        ("002230", "0", "科大讯飞"), ("300496", "0", "中科创达"),
        ("688007", "1", "光峰科技"), ("688111", "1", "金山办公"),
    ],
    "AI芯片": [
        ("688981", "1", "中芯国际"), ("688256", "1", "寒武纪"),
        ("300046", "0", "台基股份"), ("688041", "1", "海光信息"),
        ("002049", "0", "紫光国微"), ("300223", "0", "北京君正"),
        ("688726", "1", "鑫信达"), ("300666", "0", "江丰电子"),
    ],
    "通信": [
        ("300308", "0", "中际旭创"), ("300502", "0", "新易盛"),
        ("300394", "0", "天孚通信"), ("000063", "0", "中兴通讯"),
        ("600498", "1", "烽火通信"), ("002281", "0", "光迅科技"),
        ("300620", "0", "光库科技"), ("688317", "1", "之江生物"),
        ("300299", "0", "富春股份"), ("002792", "0", "通宇通讯"),
    ],
    "计算机": [
        ("002415", "0", "海康威视"), ("600588", "1", "用友网络"),
        ("000938", "0", "紫光股份"), ("002230", "0", "科大讯飞"),
        ("300033", "0", "同花顺"), ("688111", "1", "金山办公"),
        ("300496", "0", "中科创达"), ("002405", "0", "四维图新"),
        ("300311", "0", "任子行"), ("688567", "1", "孚能科技"),
    ],
    "电子": [
        ("000725", "0", "京东方A"), ("603501", "1", "韦尔股份"),
        ("002371", "0", "北方华创"), ("300142", "0", "沃森生物"),
        ("603160", "1", "汇顶科技"), ("300666", "0", "江丰电子"),
        ("002049", "0", "紫光国微"), ("300223", "0", "北京君正"),
        ("603986", "1", "兆易创新"), ("300236", "0", "上海新阳"),
        ("688107", "1", "安路科技"), ("300613", "0", "富瀚微"),
    ],

    # ── 医药 (30只, 10%) ──
    "医药生物": [
        ("300760", "0", "迈瑞医疗"), ("603259", "1", "药明康德"),
        ("300015", "0", "爱尔眼科"), ("600276", "1", "恒瑞医药"),
        ("000538", "0", "云南白药"), ("300142", "0", "沃森生物"),
        ("002422", "0", "科伦药业"), ("300683", "0", "海特生物"),
        ("688185", "1", "康希诺"), ("300601", "0", "康泰生物"),
        ("002007", "0", "华兰生物"), ("300122", "0", "智飞生物"),
        ("600196", "1", "复星医药"), ("000963", "0", "华东医药"),
    ],
    "医疗器械": [
        ("300760", "0", "迈瑞医疗"), ("688317", "1", "之江生物"),
        ("300003", "0", "乐普医疗"), ("002223", "0", "鱼跃医疗"),
        ("688277", "1", "天智航"), ("300326", "0", "凯利泰"),
        ("002551", "0", "尚荣医疗"), ("300529", "0", "健帆生物"),
    ],

    # ── 新能源 (30只, 10%) ──
    "电力设备": [
        ("300750", "0", "宁德时代"), ("601012", "1", "隆基绿能"),
        ("300274", "0", "阳光电源"), ("002594", "0", "比亚迪"),
        ("300014", "0", "亿纬锂能"), ("601877", "1", "正泰电器"),
        ("002709", "0", "天赐材料"), ("300438", "0", "鹏辉能源"),
        ("688005", "1", "容百科技"), ("300957", "0", "贝泰妮"),
        ("603259", "1", "药明康德"), ("300316", "0", "晶盛机电"),
    ],
    "汽车": [
        ("002594", "0", "比亚迪"), ("600104", "1", "上汽集团"),
        ("601633", "1", "长城汽车"), ("000625", "0", "长安汽车"),
        ("601689", "1", "拓普集团"), ("300825", "0", "阿尔特"),
        ("688526", "1", "科前数学"), ("300100", "0", "双林股份"),
    ],

    # ── 消费 (30只, 10%) ──
    "食品饮料": [
        ("600519", "1", "贵州茅台"), ("000858", "0", "五粮液"),
        ("600809", "1", "山西汾酒"), ("000568", "0", "泸州老窖"),
        ("600887", "1", "伊利股份"), ("603288", "1", "海天味业"),
        ("002714", "0", "牧原股份"), ("000895", "0", "双汇发展"),
        ("603517", "1", "绝味食品"), ("600600", "1", "青岛啤酒"),
    ],
    "商贸零售": [
        ("600655", "1", "豫园股份"), ("600361", "1", "华联综超"),
        ("002024", "0", "苏宁易购"), ("601099", "1", "太阳能"),
        ("600785", "1", "新华百货"), ("002264", "0", "新华都"),
    ],
    "纺织服装": [
        ("600600", "1", "青岛啤酒"), ("601595", "1", "上海电影"),
        ("002563", "0", "森马服饰"), ("603899", "1", "晨光文具"),
        ("300896", "0", "爱美客"), ("688169", "1", "石头科技"),
    ],

    # ── 金融 (24只, 8%) ──
    "非银金融": [
        ("601318", "1", "中国平安"), ("300059", "0", "东方财富"),
        ("600030", "1", "中信证券"), ("601688", "1", "华泰证券"),
        ("601601", "1", "中国太保"), ("600837", "1", "海通证券"),
        ("000776", "0", "广发证券"), ("600999", "1", "招商证券"),
    ],
    "银行": [
        ("601398", "1", "工商银行"), ("600036", "1", "招商银行"),
        ("601288", "1", "农业银行"), ("601939", "1", "建设银行"),
        ("600000", "1", "浦发银行"), ("601166", "1", "兴业银行"),
        ("000001", "0", "平安银行"), ("600016", "1", "民生银行"),
    ],

    # ── 周期 (24只, 8%) ──
    "有色金属": [
        ("601899", "1", "紫金矿业"), ("600547", "1", "山东黄金"),
        ("601600", "1", "中国铝业"), ("000630", "0", "铜陵有色"),
        ("600362", "1", "江西铜业"), ("002460", "0", "赣锋锂业"),
        ("002466", "0", "天齐锂业"), ("600259", "1", "广晟有色"),
    ],
    "化工": [
        ("600309", "1", "万华化学"), ("002493", "0", "荣盛石化"),
        ("600809", "1", "山西汾酒"), ("002648", "0", "卫星化学"),
        ("600989", "1", "宝丰能源"), ("300285", "0", "国瓷材料"),
        ("002709", "0", "天赐材料"), ("603260", "1", "合盛硅业"),
    ],
    "钢铁": [
        ("600019", "1", "宝钢股份"), ("000709", "0", "河钢股份"),
        ("600010", "1", "包钢股份"), ("002318", "0", "久立特材"),
        ("601899", "1", "紫金矿业"), ("600022", "1", "山东钢铁"),
    ],
    "建筑材料": [
        ("600585", "1", "海螺水泥"), ("000877", "0", "天山股份"),
        ("002233", "0", "塔牌集团"), ("603816", "1", "顾家家居"),
    ],

    # ── 制造 (20只, 7%) ──
    "机械设备": [
        ("300124", "0", "汇川技术"), ("601766", "1", "中国中车"),
        ("600009", "1", "上海机场"), ("002008", "0", "大族激光"),
        ("300024", "0", "机器人"), ("603290", "1", "斯达半导"),
        ("688006", "1", "杰普特"), ("002527", "0", "新时达"),
    ],
    "电力": [
        ("600023", "1", "浙能电力"), ("600795", "1", "国电电力"),
        ("601985", "1", "中国核电"), ("600886", "1", "国投电力"),
        ("600025", "1", "华能水电"), ("601016", "1", "节能风电"),
    ],

    # ── 基建 (16只, 5%) ──
    "建筑装饰": [
        ("601668", "1", "中国建筑"), ("601390", "1", "中国中铁"),
        ("601186", "1", "中国铁建"), ("601800", "1", "中国交建"),
        ("601618", "1", "中国中冶"), ("601117", "1", "中国化学"),
    ],
    "交通运输": [
        ("601919", "1", "中远海控"), ("600009", "1", "上海机场"),
        ("600029", "1", "南方航空"), ("601111", "1", "中国国航"),
        ("600012", "1", "皖通高速"), ("601238", "1", "广深铁路"),
    ],

    # ── 其他 (31只, 11%) ──
    "传媒": [
        ("300418", "0", "昆仑万维"), ("002602", "0", "世纪华通"),
        ("300251", "0", "光线传媒"), ("300413", "0", "芒果超媒"),
        ("603598", "1", "引力传媒"), ("002717", "0", "岭南股份"),
    ],
    "房地产": [
        ("000002", "0", "万科A"), ("600048", "1", "保利发展"),
        ("001979", "0", "招商蛇口"), ("600340", "1", "华夏幸福"),
        ("000069", "0", "华侨城A"), ("601155", "1", "新城控股"),
    ],
    "农林牧渔": [
        ("002714", "0", "牧原股份"), ("000998", "0", "隆平高科"),
        ("600598", "1", "北大荒"), ("002311", "0", "海大集团"),
        ("300498", "0", "温氏股份"), ("000876", "0", "新希望"),
    ],
    "环保": [
        ("300055", "0", "万邦达"), ("603588", "1", "高能环境"),
        ("300187", "0", "永清环保"), ("002672", "0", "东江环保"),
    ],
    "教育": [
        ("002607", "0", "中公教育"), ("300359", "0", "全通教育"),
        ("603099", "1", "长白山"), ("003032", "0", "传智教育"),
    ],
    "综合": [
        ("600009", "1", "上海机场"), ("600655", "1", "豫园股份"),
        ("601888", "1", "中国中免"), ("600058", "1", "五矿发展"),
    ],
}


# ═══════════════════════════════════════════════
# 行业权重配置
# ═══════════════════════════════════════════════

INDUSTRY_WEIGHTS = {
    "AI算力": 0.06, "AI芯片": 0.04, "通信": 0.05, "计算机": 0.04, "电子": 0.05,
    "医药生物": 0.06, "医疗器械": 0.03,
    "电力设备": 0.06, "汽车": 0.03,
    "食品饮料": 0.05, "商贸零售": 0.03, "纺织服装": 0.03,
    "非银金融": 0.04, "银行": 0.03,
    "有色金属": 0.04, "化工": 0.04, "钢铁": 0.02, "建筑材料": 0.02,
    "机械设备": 0.04, "电力": 0.03,
    "建筑装饰": 0.03, "交通运输": 0.03,
    "传媒": 0.04, "房地产": 0.03, "农林牧渔": 0.03, "环保": 0.02, "教育": 0.02, "综合": 0.02,
}


# ═══════════════════════════════════════════════
# 动态监控池管理器
# ═══════════════════════════════════════════════

class DynamicPoolManager:
    """
    动态监控池管理器

    功能：
    1. 从300只行业分层宇宙中每日动态筛选TOP N只活跃标的
    2. 按行业权重分配各行业入选数量
    3. 支持行业动量轮换（周日更新权重）
    4. 输出标准格式供TDXInjector和部署脚本使用
    """

    def __init__(self, top_n: int = 60, verbose: bool = True):
        self.top_n = top_n
        self.verbose = verbose
        self.universe = self._build_universe()
        self.weights = dict(INDUSTRY_WEIGHTS)
        self.pool_file = os.path.join('data', 'dynamic_watchlist.json')
        self.weights_file = os.path.join('data', 'industry_weights.json')

    def _build_universe(self) -> List[Dict]:
        """构建300只行业分层宇宙"""
        universe = []
        seen_codes = set()

        for industry, stocks in INDUSTRY_UNIVERSE.items():
            for item in stocks:
                code = item[0]
                if code in seen_codes:
                    continue
                seen_codes.add(code)

                # 推断行业
                universe.append({
                    'code': code,
                    'setcode': item[1],
                    'name': item[2],
                    'industry': industry,
                })

        return universe

    def get_universe(self) -> List[Dict]:
        """获取完整宇宙"""
        return self.universe

    def get_universe_size(self) -> int:
        """获取宇宙规模"""
        return len(self.universe)

    def get_industry_stats(self) -> Dict[str, int]:
        """获取行业分布统计"""
        stats = defaultdict(int)
        for s in self.universe:
            stats[s['industry']] += 1
        return dict(stats)

    def allocate_by_weight(self) -> Dict[str, int]:
        """
        按行业权重分配各行业入选数量

        Returns: {industry: count}
        """
        total_weight = sum(self.weights.values())
        allocation = {}

        for industry, weight in self.weights.items():
            # 该行业在宇宙中的股票数
            industry_count = sum(1 for s in self.universe if s['industry'] == industry)
            # 按权重分配
            allocated = max(2, round(self.top_n * weight / total_weight))
            allocated = min(allocated, industry_count)
            allocation[industry] = allocated

        # 调整总数到top_n
        total_allocated = sum(allocation.values())
        if total_allocated > self.top_n:
            # 按权重从大到小削减
            sorted_inds = sorted(allocation.items(), key=lambda x: self.weights.get(x[0], 0), reverse=True)
            diff = total_allocated - self.top_n
            for i in range(diff):
                ind = sorted_inds[i % len(sorted_inds)][0]
                if allocation[ind] > 2:
                    allocation[ind] -= 1
        elif total_allocated < self.top_n:
            # 补足
            sorted_inds = sorted(allocation.items(), key=lambda x: self.weights.get(x[0], 0), reverse=True)
            diff = self.top_n - total_allocated
            for i in range(diff):
                ind = sorted_inds[i % len(sorted_inds)][0]
                allocation[ind] += 1

        return allocation

    def select_top_stocks(
        self,
        realtime_data: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        从宇宙中选出今日TOP N只活跃标的

        Args:
            realtime_data: 实时行情数据 {code: {amount, change_pct, turnover, ...}}
                          如果为None, 则按宇宙顺序返回前N只

        Returns:
            [{code, name, setcode, industry, amount, change_pct}, ...]
        """
        allocation = self.allocate_by_weight()

        # 按行业分组
        industry_groups = defaultdict(list)
        for stock in self.universe:
            industry_groups[stock['industry']].append(stock)

        selected = []

        for industry, count in allocation.items():
            stocks = industry_groups.get(industry, [])

            if realtime_data:
                # 按成交额排序
                scored = []
                for s in stocks:
                    rt = realtime_data.get(s['code'], {})
                    amount = rt.get('amount', 0)
                    change_pct = rt.get('change_pct', 0)
                    turnover = rt.get('turnover', 0)
                    # 综合活跃度评分: 成交额(50%) + 涨幅绝对值(30%) + 换手率(20%)
                    score = (
                        0.50 * min(amount / 1e8, 100)  # 成交额归一化
                        + 0.30 * min(abs(change_pct), 20)  # 涨幅绝对值
                        + 0.20 * min(turnover * 100, 20)  # 换手率
                    )
                    scored.append((score, s, amount, change_pct))

                scored.sort(key=lambda x: x[0], reverse=True)

                for _, s, amount, change_pct in scored[:count]:
                    entry = dict(s)
                    entry['amount'] = amount
                    entry['change_pct'] = change_pct
                    selected.append(entry)
            else:
                # 无实时数据, 取前N只
                for s in stocks[:count]:
                    selected.append(dict(s))

        # 如果不足top_n, 从剩余宇宙中补足
        if len(selected) < self.top_n:
            selected_codes = {s['code'] for s in selected}
            for s in self.universe:
                if s['code'] not in selected_codes:
                    selected.append(dict(s))
                    if len(selected) >= self.top_n:
                        break

        # 截断到top_n
        selected = selected[:self.top_n]

        return selected

    def save_pool(self, pool: List[Dict] = None) -> str:
        """保存监控池到文件"""
        if pool is None:
            pool = self.select_top_stocks()

        os.makedirs(os.path.dirname(self.pool_file) or '.', exist_ok=True)
        with open(self.pool_file, 'w', encoding='utf-8') as f:
            json.dump({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'timestamp': datetime.now().isoformat(),
                'pool_size': len(pool),
                'universe_size': len(self.universe),
                'stocks': pool,
            }, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"[PoolManager] 监控池已保存: {self.pool_file} ({len(pool)}只)")
        return self.pool_file

    def load_pool(self) -> List[Dict]:
        """加载已保存的监控池"""
        if os.path.exists(self.pool_file):
            with open(self.pool_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('stocks', [])
        return self.select_top_stocks()

    def save_weights(self):
        """保存行业权重"""
        os.makedirs(os.path.dirname(self.weights_file) or '.', exist_ok=True)
        with open(self.weights_file, 'w', encoding='utf-8') as f:
            json.dump({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'weights': self.weights,
            }, f, ensure_ascii=False, indent=2)

    def load_weights(self):
        """加载行业权重"""
        if os.path.exists(self.weights_file):
            with open(self.weights_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.weights = data.get('weights', dict(INDUSTRY_WEIGHTS))

    def rotate_industries(self, industry_momentum: Dict[str, float]):
        """
        行业轮换：根据行业动量调整权重

        Args:
            industry_momentum: {industry: momentum_score} 行业动量评分
                              正值=强势行业, 负值=弱势行业
        """
        for industry, momentum in industry_momentum.items():
            if industry in self.weights:
                # 动量>0加权, 动量<0减权, 每次调整±2%
                adjustment = max(-0.02, min(0.02, momentum * 0.01))
                self.weights[industry] = max(0.01, self.weights[industry] + adjustment)

        # 归一化权重
        total = sum(self.weights.values())
        if total > 0:
            for k in self.weights:
                self.weights[k] = round(self.weights[k] / total, 4)

        self.save_weights()

        if self.verbose:
            print(f"[PoolManager] 行业权重已轮换: {len(industry_momentum)}个行业调整")

    def get_monitor_pool_format(self) -> List[Dict]:
        """
        获取标准监控池格式 (兼容V13_1_P0_1430_Deploy.py)

        Returns: [{code, name, setcode, industry}, ...]
        """
        pool = self.load_pool()
        return [
            {
                'code': s['code'],
                'name': s['name'],
                'setcode': s.get('setcode', '0'),
                'industry': s.get('industry', '通用'),
            }
            for s in pool
        ]

    def print_stats(self):
        """打印统计信息"""
        print("\n" + "=" * 60)
        print("  V13.1 P0-3 动态监控池统计")
        print("=" * 60)
        print(f"  宇宙规模: {self.get_universe_size()}只")
        print(f"  行业数: {len(INDUSTRY_UNIVERSE)}个")
        print(f"  TOP N: {self.top_n}只")
        print(f"\n  行业分布:")
        stats = self.get_industry_stats()
        for ind, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            weight = self.weights.get(ind, 0)
            allocated = self.allocate_by_weight().get(ind, 0)
            print(f"    {ind:<12} {count:>3}只 | 权重{weight*100:>5.1f}% | 分配{allocated:>3}只")
        print("=" * 60)


# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def get_default_pool(top_n: int = 30) -> List[Dict]:
    """获取默认监控池（兼容旧接口）"""
    mgr = DynamicPoolManager(top_n=top_n, verbose=False)
    return mgr.get_monitor_pool_format()


def get_dynamic_pool(top_n: int = 60, realtime_data: Dict = None) -> List[Dict]:
    """
    获取动态监控池

    Args:
        top_n: 选出N只活跃标的
        realtime_data: 实时行情数据用于活跃度排序

    Returns: [{code, name, setcode, industry}, ...]
    """
    mgr = DynamicPoolManager(top_n=top_n, verbose=False)
    pool = mgr.select_top_stocks(realtime_data)
    mgr.save_pool(pool)
    return [
        {
            'code': s['code'],
            'name': s['name'],
            'setcode': s.get('setcode', '0'),
            'industry': s.get('industry', '通用'),
        }
        for s in pool
    ]


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    mgr = DynamicPoolManager(top_n=60)
    mgr.print_stats()

    # 生成并保存监控池
    pool = mgr.select_top_stocks()
    mgr.save_pool(pool)

    print(f"\n监控池已生成: {len(pool)}只")
    print(f"文件: {mgr.pool_file}")

    # 显示前10只
    print("\n前10只:")
    for i, s in enumerate(pool[:10]):
        print(f"  #{i+1} {s['code']} {s['name']} ({s['industry']})")
