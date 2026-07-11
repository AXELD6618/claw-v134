"""
V13_5_TdxClaw_TQ_Connector.py
TdxClaw TQ Python API 的 MCP 封装器
让本系统能够直接调用通达信TQ的45个金融专业Skills

架构:
TdxClaw桌面应用 → TQ Python API (tqcenter.py) → 本封装器 → MCP工具 → M71预测器

作者: 毕方灵犀·貔貅助手
版本: V13.5.18
日期: 2026-07-03
"""

import sys
import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

class TdxClawTQConnector:
    """
    TdxClaw TQ Python API 连接器
    
    通过包装 tqcenter.py 模块，提供对通达信45个金融专业Skills的调用能力
    """
    
    def __init__(self, tdx_root: Optional[str] = None):
        """
        初始化TQ连接器
        
        Args:
            tdx_root: 通达信安装目录，如果为None则自动检测
        """
        self.tdx_root = tdx_root or self._detect_tdx_root()
        self.tq = None
        
        if self.tdx_root:
            self._init_tq()
    
    def _detect_tdx_root(self) -> Optional[str]:
        """
        自动检测通达信安装目录
        
        通过检查注册表和常见安装路径
        """
        # 方法1: 从注册表读取
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信金融终端64"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                tdx_root, _ = winreg.QueryValueEx(key, "InstallLocation")
                tq_path = os.path.join(tdx_root, 'PYPlugins', 'user', 'tqcenter.py')
                if os.path.exists(tq_path):
                    print(f"✅ 检测到通达信安装目录: {tdx_root}")
                    return tdx_root
        except Exception as e:
            print(f"⚠️ 注册表检测失败: {e}")
        
        # 方法2: 检查常见安装路径
        common_paths = [
            r"C:\Program Files\通达信金融终端",
            r"D:\Program Files\通达信金融终端",
            r"E:\Program Files\通达信金融终端",
        ]
        
        for path in common_paths:
            tq_path = os.path.join(path, 'PYPlugins', 'user', 'tqcenter.py')
            if os.path.exists(tq_path):
                print(f"✅ 检测到通达信安装目录: {path}")
                return path
        
        print("⚠️ 未检测到通达信安装目录，请手动指定")
        return None
    
    def _init_tq(self):
        """初始化TQ模块"""
        try:
            # 将TQ模块路径添加到sys.path
            tq_module_path = os.path.join(self.tdx_root, 'PYPlugins', 'user')
            sys.path.insert(0, tq_module_path)
            
            # 导入TQ模块
            from tqcenter import tq
            
            # 初始化TQ
            tq.initialize(__file__)
            
            self.tq = tq
            print("✅ TQ模块初始化成功")
            
        except Exception as e:
            print(f"❌ TQ模块初始化失败: {e}")
            self.tq = None
    
    def is_ready(self) -> bool:
        """检查TQ是否就绪"""
        return self.tq is not None
    
    # ==================== 行情数据接口 ====================
    
    def get_market_data(self, field_list: List[str], stock_list: List[str], 
                       period: str = '1d', start_time: Optional[str] = None,
                       end_time: Optional[str] = None, count: int = -1) -> Dict:
        """
        获取历史K线数据
        
        Args:
            field_list: 字段列表 ['Open', 'High', 'Low', 'Close', 'Volume', 'Amount']
            stock_list: 股票代码列表 ['600519', '000001']
            period: 周期 '1m'/'5m'/'1d'/'5d'/'1w'/'1M'
            start_time: 开始时间 '2026-01-01'
            end_time: 结束时间 '2026-07-03'
            count: 数据条数，-1表示全部
        
        Returns:
            Dict of DataFrame
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            result = self.tq.get_market_data(
                field_list, stock_list, period,
                start_time or '', end_time or '',
                count, 0, 1, 0
            )
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def get_market_snapshot(self, stock_code: str, field_list: List[str] = []) -> Dict:
        """
        获取实时快照（单股）
        
        Args:
            stock_code: 股票代码 '600519'
            field_list: 字段列表，空列表返回全部
        
        Returns:
            快照数据Dict
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            result = self.tq.get_market_snapshot(stock_code, field_list)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def get_more_info(self, stock_code: str, field_list: List[str] = []) -> Dict:
        """
        获取扩展信息（涨幅/封单/PE/市值等100+字段）
        
        Args:
            stock_code: 股票代码 '600519'
            field_list: 字段列表，空列表返回全部
                       关键字段: FCAmo(封单额), ZTPrice(涨停价), DTPrice(跌停价),
                                ZAF(涨幅), Zsz(总市值), EverZTCount(连板天数)
        
        Returns:
            扩展信息Dict
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            result = self.tq.get_more_info(stock_code, field_list)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    # ==================== 专业数据接口 ====================
    
    def get_financial_data(self, stock_list: List[str], field_list: List[str],
                          year: int = 0, mmdd: int = 0) -> Dict:
        """
        获取财务报表数据（FN1~FN584）
        
        Args:
            stock_list: 股票代码列表
            field_list: 字段列表（FN1~FN584）
            year: 年份，0表示最新
            mmdd: 月份日期，0表示最新
        
        Returns:
            财务数据Dict
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            result = self.tq.get_financial_data_by_date(stock_list, field_list, year, mmdd)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def get_stock_trading_data(self, stock_list: List[str], field_list: List[str],
                              year: int = 0, mmdd: int = 0) -> Dict:
        """
        获取股票交易数据（GP01~GP46）
        
        Args:
            stock_list: 股票代码列表
            field_list: 字段列表（GP01~GP46）
            year: 年份，0表示最新
            mmdd: 月份日期，0表示最新
        
        Returns:
            交易数据Dict
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            result = self.tq.get_gpjy_value_by_date(stock_list, field_list, year, mmdd)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    # ==================== 股票/板块列表接口 ====================
    
    def get_stock_list(self, market: str = '5', list_type: int = 0) -> List[Dict]:
        """
        获取市场股票列表
        
        Args:
            market: 市场代码 '5'=全部A股, '0'=深市, '1'=沪市, '2'=北交所
            list_type: 列表类型 0=全部
        
        Returns:
            股票列表 [{'code': '600519', 'name': '贵州茅台'}, ...]
        """
        if not self.is_ready():
            return [{"error": "TQ模块未初始化"}]
        
        try:
            result = self.tq.get_stock_list(market, list_type)
            return result
        except Exception as e:
            return [{"error": str(e)}]
    
    def get_sector_list(self, list_type: int = 0) -> List[Dict]:
        """
        获取全部板块列表
        
        Args:
            list_type: 板块类型 0=全部, 1=行业, 2=概念, 3=地域
        
        Returns:
            板块列表 [{'code': '880564', 'name': '贵州板块'}, ...]
        """
        if not self.is_ready():
            return [{"error": "TQ模块未初始化"}]
        
        try:
            result = self.tq.get_sector_list(list_type)
            return result
        except Exception as e:
            return [{"error": str(e)}]
    
    # ==================== 通达信公式接口 ====================
    
    def run_indicator_formula(self, formula: str, stock_list: List[str],
                             period: str = '1d', count: int = 250) -> Dict:
        """
        运行指标公式（单次调用）
        
        Args:
            formula: 通达信公式字符串
            stock_list: 股票代码列表
            period: 周期
            count: 数据条数
        
        Returns:
            指标计算结果Dict
        """
        if not self.is_ready():
            return {"error": "TQ模块未初始化"}
        
        try:
            # 预设K线数据
            self.tq.formula_set_data(stock_list, period, count)
            
            # 调用指标公式
            result = self.tq.formula_zb(formula, stock_list, period, count)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def run_screener_formula(self, formula: str, stock_list: List[str],
                             period: str = '1d', count: int = 250) -> List[str]:
        """
        运行选股公式（单次调用）
        
        Args:
            formula: 通达信选股公式字符串
            stock_list: 股票代码列表
            period: 周期
            count: 数据条数
        
        Returns:
            符合条件的股票代码列表
        """
        if not self.is_ready():
            return [{"error": "TQ模块未初始化"}]
        
        try:
            # 预设K线数据
            self.tq.formula_set_data(stock_list, period, count)
            
            # 调用选股公式
            result = self.tq.formula_xg(formula, stock_list, period, count)
            return result
        except Exception as e:
            return [{"error": str(e)}]
    
    # ==================== 与客户端交互接口 ====================
    
    def send_alert(self, stock_list: List[str], price_list: List[str],
                   close_list: List[str], volum_list: List[str], count: int) -> bool:
        """
        发送预警信号到TQ信号界面
        
        Args:
            stock_list: 股票代码列表
            price_list: 价格列表（纯数字字符串）
            close_list: 收盘价列表（纯数字字符串）
            volum_list: 成交量列表（纯数字字符串）
            count: 信号数量（必须>0）
        
        Returns:
            是否发送成功
        """
        if not self.is_ready():
            return False
        
        try:
            self.tq.send_warn(stock_list, price_list, close_list, volum_list, count)
            return True
        except Exception as e:
            print(f"❌ 发送预警失败: {e}")
            return False
    
    def send_to_client(self, msg: str):
        """
        发送文本到TQ策略管理界面
        
        Args:
            msg: 消息文本（用 \\| 分行）
        """
        if not self.is_ready():
            return
        
        try:
            self.tq.send_message(msg)
        except Exception as e:
            print(f"❌ 发送消息失败: {e}")


# ==================== MCP工具封装 ====================

def create_mcp_tools(connector: TdxClawTQConnector) -> Dict[str, Any]:
    """
    为TdxClaw TQ连接器创建MCP工具字典
    
    这些工具可以被MCP服务器暴露给AI助手使用
    """
    
    tools = {}
    
    # 工具1: 获取历史K线
    def mcp_get_kline(stock_code: str, period: str = '1d', count: int = 250) -> Dict:
        """
        获取股票历史K线数据
        
        Args:
            stock_code: 股票代码
            period: 周期 '1d'/'1w'/'1M'/'5m'/'1m'
            count: 数据条数
        
        Returns:
            K线数据Dict
        """
        field_list = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount']
        result = connector.get_market_data(field_list, [stock_code], period, count=count)
        return result
    
    tools['tq_get_kline'] = mcp_get_kline
    
    # 工具2: 获取实时快照
    def mcp_get_snapshot(stock_code: str) -> Dict:
        """
        获取股票实时快照
        
        Args:
            stock_code: 股票代码
        
        Returns:
            实时快照Dict（包含现价/涨跌/成交量/换手率等）
        """
        result = connector.get_market_snapshot(stock_code)
        return result
    
    tools['tq_get_snapshot'] = mcp_get_snapshot
    
    # 工具3: 获取扩展信息（涨停/跌停判断）
    def mcp_get_extended_info(stock_code: str, fields: str = "") -> Dict:
        """
        获取股票扩展信息（涨停/跌停/市值/PE等）
        
        Args:
            stock_code: 股票代码
            fields: 字段列表（逗号分隔），空字符串返回全部
                    FCAmo=封单额, ZTPrice=涨停价, DTPrice=跌停价,
                    ZAF=涨幅, Zsz=总市值, EverZTCount=连板天数
        
        Returns:
            扩展信息Dict
        """
        field_list = fields.split(',') if fields else []
        result = connector.get_more_info(stock_code, field_list)
        return result
    
    tools['tq_get_extended_info'] = mcp_get_extended_info
    
    # 工具4: 运行通达信选股公式
    def mcp_run_screener(formula: str, market: str = '5') -> List[str]:
        """
        运行通达信选股公式
        
        Args:
            formula: 通达信选股公式字符串
            market: 市场范围 '5'=全部A股
        
        Returns:
            符合条件的股票代码列表
        """
        stock_list = connector.get_stock_list(market)
        stock_codes = [s['code'] for s in stock_list if 'code' in s]
        
        result = connector.run_screener_formula(formula, stock_codes)
        return result
    
    tools['tq_run_screener'] = mcp_run_screener
    
    # 工具5: 获取财务报表
    def mcp_get_financial(stock_code: str, fields: str = "FN1,FN2,FN3") -> Dict:
        """
        获取股票财务报表数据
        
        Args:
            stock_code: 股票代码
            fields: 字段列表（逗号分隔，FN1~FN584）
        
        Returns:
            财务数据Dict
        """
        field_list = fields.split(',')
        result = connector.get_financial_data([stock_code], field_list)
        return result
    
    tools['tq_get_financial'] = mcp_get_financial
    
    return tools


# ==================== 测试代码 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("TdxClaw TQ Connector 测试")
    print("=" * 60)
    
    # 创建连接器
    connector = TdxClawTQConnector()
    
    if not connector.is_ready():
        print("\n❌ TQ模块未就绪，请检查:")
        print("  1. 通达信客户端是否已安装")
        print("  2. TdxW.exe 是否正在运行")
        print("  3. PYPlugins/user/tqcenter.py 是否存在")
        sys.exit(1)
    
    print("\n✅ TQ模块已就绪，开始测试...\n")
    
    # 测试1: 获取股票列表
    print("-" * 60)
    print("测试1: 获取A股股票列表（前10只）")
    print("-" * 60)
    stock_list = connector.get_stock_list('5')[:10]
    for stock in stock_list:
        print(f"  {stock.get('code', 'N/A')} - {stock.get('name', 'N/A')}")
    
    # 测试2: 获取实时快照
    print("\n" + "-" * 60)
    print("测试2: 获取贵州茅台(600519)实时快照")
    print("-" * 60)
    snapshot = connector.get_market_snapshot('600519')
    if 'error' not in snapshot:
        print(f"  现价: {snapshot.get('Now', 'N/A')}")
        print(f"  涨幅: {snapshot.get('ZAF', 'N/A')}%")
        print(f"  成交量: {snapshot.get('Volume', 'N/A')}")
    else:
        print(f"  ❌ 错误: {snapshot['error']}")
    
    # 测试3: 获取扩展信息（涨停判断）
    print("\n" + "-" * 60)
    print("测试3: 获取贵州茅台(600519)扩展信息（涨停判断）")
    print("-" * 60)
    extended = connector.get_more_info('600519', ['FCAmo', 'ZTPrice', 'ZAF'])
    if 'error' not in extended:
        fc_amo = extended.get('FCAmo', 0)
        if fc_amo > 0:
            print(f"  ✅ 涨停状态: 封单额={fc_amo}万元")
        elif fc_amo < 0:
            print(f"  ❌ 跌停状态: 封单额={fc_amo}万元")
        else:
            print(f"  状态: 未封板")
        print(f"  涨幅: {extended.get('ZAF', 'N/A')}%")
    else:
        print(f"  ❌ 错误: {extended['error']}")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
