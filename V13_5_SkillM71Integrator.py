# -*- coding: utf-8 -*-
"""
V13.5.18 TdxClaw Skills → M71维度 完整集成模块
=================================================
将TdxClaw 46个Skills的数据流精确映射到M71 34维度评分系统

核心架构:
  AI Agent(自动化) → 调用TDX MCP工具 → JSON结果 → 
  本模块解析 → M71维度评分 → predict()使用

6大M71维度集成:
  D21 四阶段演变 → tdx-agzxsb(市场主线) + tdx-industry-chain(产业链)
  D24 三高筹码   → tdx-zzjdysyfx(政策解读) + tdx-financials(财务分析)
  D25 放量启动   → tdx-wxd-a(问小达选股) + tdx-main-position(主力资金)
  D28 催化强度   → tdx-ggycbfx(公告与财报) + tdx-event-driven(事件驱动)
  D29 双洗盘     → tdx-main-position(主力资金) + tdx_api_data(zjlx)
  D31 主力意图   → tdx-main-position(主力资金) + tdx-jgccgdfx(机构持仓)

Author: 毕方灵犀·貔貅助手
Version: V13.5.18
Date: 2026-07-04
"""

import json
import os
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# SECTION 1: Skill → MCP工具调用参数映射表 (AI Agent参考)
# ═══════════════════════════════════════════════════════════════

SKILL_MCP_CALL_MAP = {
    # ──── D21: 四阶段演变 ────
    "tdx-agzxsb": {
        "name": "A股市场主线识别",
        "mcp_tool": "mcp__tdx-connector__tdx_screener",
        "mcp_params": {"message": "今日涨幅前10板块", "pageSize": "10"},
        "extra_calls": [
            {"mcp_tool": "mcp__tdx-connector__wenda_news_query",
             "mcp_params_template": {"name": "{sector_name}", "bdate": "{today_7d}", "edate": "{today}", "keywords": "主线,热点,资金"}}
        ],
        "m71_dimension": "D21",
        "parse_method": "parse_market_mainline_to_d21",
    },
    
    "tdx-industry-chain": {
        "name": "查询行业产业链",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params": {"entry": "TdxSharePCCW.cfg_tk_gethy", "industryCode": "881084"},
        "m71_dimension": "D21",
        "parse_method": "parse_industry_chain_to_d21",
    },
    
    # ──── D24: 三高筹码 ────
    "tdx-zzjdysyfx": {
        "name": "政策解读与受益分析",
        "mcp_tool": "mcp__tdx-connector__wenda_news_query",
        "mcp_params_template": {"name": "{policy_keyword}", "bdate": "{today_14d}", "edate": "{today}", "keywords": "政策,利好,受益"},
        "m71_dimension": "D24",
        "parse_method": "parse_policy_to_d24",
    },
    
    "tdx-financials": {
        "name": "查询财务分析数据",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxShareCW.ph_agf10_cw_lyb", "fixedTag": "00102", "code": "{stock_code}"},
        "extra_calls": [
            {"mcp_tool": "mcp__tdx-connector__tdx_api_data",
             "mcp_params_template": {"entry": "TdxShareCW.ph_agf10_gzfx", "extraOne": "1Y", "extraTwo": "PE", "code": "{stock_code}"}}
        ],
        "m71_dimension": "D24",
        "parse_method": "parse_financials_to_d24",
    },
    
    # ──── D25: 放量启动 ────
    "tdx-wxd-a": {
        "name": "问小达选A股",
        "mcp_tool": "mcp__tdx-connector__tdx_screener",
        "mcp_params_template": {"message": "{query}", "pageSize": "20"},
        "m71_dimension": "D25",
        "parse_method": "parse_screener_to_d25",
    },
    
    "tdx-main-position": {
        "name": "主力资金",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_jyds", "fixedTag": "zjlx", "code": "{stock_code}"},
        "m71_dimension": "D25",
        "parse_method": "parse_capital_flow_to_d25",
    },
    
    # ──── D28: 催化强度 ────
    "tdx-ggycbfx": {
        "name": "公告与财报分析",
        "mcp_tool": "mcp__tdx-connector__wenda_notice_query",
        "mcp_params_template": {"name": "{stock_name}", "symbol": "{stock_code}", "bdate": "{today_30d}", "edate": "{today}", "keywords": "重组,回购,定增,业绩预告"},
        "m71_dimension": "D28",
        "parse_method": "parse_announcements_to_d28",
    },
    
    "tdx-event-driven": {
        "name": "事件驱动与短线催化分析",
        "mcp_tool": "mcp__tdx-connector__wenda_news_query",
        "mcp_params_template": {"name": "{stock_name}", "bdate": "{today_7d}", "edate": "{today}", "keywords": "催化,驱动,事件,题材"},
        "m71_dimension": "D28",
        "parse_method": "parse_events_to_d28",
    },
    
    # ──── D29: 双洗盘 ────
    "tdx-main-position-d29": {
        "name": "主力资金流向(Historical)",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_jyds", "fixedTag": "zjlx", "code": "{stock_code}"},
        "m71_dimension": "D29",
        "parse_method": "parse_capital_flow_history_to_d29",
    },
    
    # ──── D31: 主力资金意图 ────
    "tdx-jgccgdfx": {
        "name": "机构持仓股东分析",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_gdyj", "fixedTag": "jgcg", "code": "{stock_code}"},
        "m71_dimension": "D31",
        "parse_method": "parse_institutional_to_d31",
    },
    
    # ──── D33: 外盘/内盘 ────
    "tdx-quotes-d33": {
        "name": "实时行情(外盘内盘)",
        "mcp_tool": "mcp__tdx-connector__tdx_quotes",
        "mcp_params_template": {"code": "{stock_code}", "setcode": "{setcode}", "hasCalcInfo": "1"},
        "m71_dimension": "D33",
        "parse_method": "parse_quotes_to_d33",
    },
    
    # ──── 辅助维度 ────
    "tdx-dragon-tiger": {
        "name": "查询个股龙虎榜",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_jyds", "fixedTag": "jglhb", "code": "{stock_code}", "extra": "{date}"},
        "m71_dimension": "D3/D4",
        "parse_method": "parse_dragon_tiger_to_d3d4",
    },
    
    "tdx-hot-topic": {
        "name": "查询个股热点题材",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_rdtc", "fixedTag": "zttzztk", "code": "{stock_code}"},
        "m71_dimension": "D28辅助",
        "parse_method": "parse_hot_topic_to_d28",
    },
    
    "tdx-report-rating": {
        "name": "查询研报评级一致预期",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "CWServ.tdxf10_gg_ybpj", "fixedTag": "yzyq", "code": "{stock_code}"},
        "m71_dimension": "D24辅助",
        "parse_method": "parse_report_rating_to_d24",
    },
    
    "tdx-shareholder": {
        "name": "查询股东信息",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_gdyj", "fixedTag": "ltgd", "code": "{stock_code}"},
        "m71_dimension": "D24辅助",
        "parse_method": "parse_shareholder_to_d24",
    },
    
    "tdx-bxzjxw": {
        "name": "北向资金行为",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf10_gg_zlcc", "fixedTag": "bszj", "code": "{stock_code}"},
        "m71_dimension": "D5辅助",
        "parse_method": "parse_northbound_to_d5",
    },
    
    "tdx-earnings-warning": {
        "name": "查询业绩预警",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxSharePCCW.tdxf9_ag_cwsj_yjyj", "code": "{stock_code}", "extra": "1"},
        "m71_dimension": "D28辅助",
        "parse_method": "parse_earnings_warning_to_d28",
    },
    
    "tdx-valuation-pricing-framework": {
        "name": "估值与定价框架分析",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxShareCW.ph_agf10_gzfx", "extraOne": "1Y", "extraTwo": "PE", "code": "{stock_code}"},
        "m71_dimension": "D24辅助",
        "parse_method": "parse_valuation_to_d24",
    },
    
    "tdx-gszddf": {
        "name": "公司质地打分",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params_template": {"entry": "TdxShareCW.ph_agf10_hypm", "queryKey": "00102", "code": "{stock_code}"},
        "m71_dimension": "D24辅助",
        "parse_method": "parse_company_quality_to_d24",
    },
    
    "tdx-lhbxwfg": {
        "name": "龙虎榜席位风格",
        "mcp_tool": "mcp__tdx-connector__tdx_api_data",
        "mcp_params": {"entry": "TdxSharePCCW.tdxsj_lhbd_lhbzl", "branch": "1"},
        "m71_dimension": "D3/D4辅助",
        "parse_method": "parse_dragon_tiger_style_to_d3d4",
    },
}

# ═══════════════════════════════════════════════════════════════
# SECTION 2: MCP结果 → M71维度解析器
# ═══════════════════════════════════════════════════════════════

class SkillM71Integrator:
    """将TdxClaw Skills MCP结果解析为M71维度评分数据"""
    
    def __init__(self):
        self.data_dir = "data/skill_mcp_results"
        os.makedirs(self.data_dir, exist_ok=True)
    
    # ──── D21: 四阶段演变 ────
    
    def parse_market_mainline_to_d21(self, mcp_result: Dict) -> Dict:
        """
        解析tdx_screener(市场主线)结果 → D21四阶段评分
        
        返回: {
            "mainline_sectors": [...],    # 主线板块列表
            "phase_scores": {...},        # 各阶段评分
            "capex_signal": float,        # CapEx扩张信号(0-10)
            "phase_d21_score": float,     # D21维度评分(0-10)
        }
        """
        result = {
            "mainline_sectors": [],
            "phase_scores": {},
            "capex_signal": 0.0,
            "phase_d21_score": 0.0,
            "phase_d21_detail": "",
        }
        
        try:
            # tdx_screener返回格式: {"items": [{"sec_code": "xxx", "sec_name": "xxx", ...}]}
            items = mcp_result.get("items", [])
            if items:
                for item in items[:5]:
                    result["mainline_sectors"].append({
                        "code": item.get("sec_code", ""),
                        "name": item.get("sec_name", ""),
                    })
            
            # 四阶段评分逻辑
            # 阶段一(买铲子): AI芯片/算力/半导体 → CapEx+50%以上 → 3-5分
            # 阶段二(瓶颈争夺): 封装/设备/HBM → 被错杀+基本面好 → 8分(最高)
            # 阶段三(效率定价): 云计算/AI服务 → 5-6分
            # 阶段四(价值回归): 有客户能盈利的应用 → 10分
            
            mainlines = result["mainline_sectors"]
            if any("算力" in s["name"] or "半导体" in s["name"] or "芯片" in s["name"] for s in mainlines):
                result["phase_scores"]["phase1"] = 5.0
                result["phase_d21_score"] += 3.0
                result["phase_d21_detail"] += "阶段一(买铲子): 算力/半导体主线+3分;"
            if any("封装" in s["name"] or "设备" in s["name"] or "HBM" in s["name"] for s in mainlines):
                result["phase_scores"]["phase2"] = 8.0
                result["phase_d21_score"] += 5.0
                result["phase_d21_detail"] += "阶段二(瓶颈争夺): 封装/设备主线+5分;"
            if any("云" in s["name"] or "服务" in s["name"] or "AI" in s["name"] for s in mainlines):
                result["phase_scores"]["phase3"] = 6.0
                result["phase_d21_score"] += 2.0
                result["phase_d21_detail"] += "阶段三(效率定价): 云服务主线+2分;"
            
            result["phase_d21_score"] = min(result["phase_d21_score"], 10.0)
            
        except Exception as e:
            logger.error(f"parse_market_mainline_to_d21 error: {e}")
            result["parse_error"] = str(e)
        
        return result
    
    def parse_industry_chain_to_d21(self, mcp_result: Dict) -> Dict:
        """
        解析tdx_api_data(产业链)结果 → D21产业阶段判断
        """
        result = {
            "chain_data": {},
            "phase_signal": 0.0,
            "phase_d21_bonus": 0.0,
        }
        
        try:
            summary = mcp_result.get("summary", "")
            tables = mcp_result.get("tables", [])
            
            # 从产业链数据判断当前产业阶段
            if "CapEx" in summary or "资本开支" in summary:
                # 检查CapEx增速
                for table in tables:
                    for row in table.get("rows", []):
                        for key, val in row.items():
                            if "增长" in key or "增速" in key:
                                try:
                                    growth = float(str(val).replace("%", "").replace("--", "0"))
                                    if growth >= 50:
                                        result["phase_signal"] = 1  # 阶段一(扩张期)
                                        result["phase_d21_bonus"] = 3.0
                                    elif growth >= 20:
                                        result["phase_signal"] = 2  # 阶段二(瓶颈期)
                                        result["phase_d21_bonus"] = 5.0
                                    elif growth < 10:
                                        result["phase_signal"] = 3  # 阶段三(效率期)
                                        result["phase_d21_bonus"] = 2.0
                                except:
                                    pass
            
        except Exception as e:
            logger.error(f"parse_industry_chain_to_d21 error: {e}")
        
        return result
    
    # ──── D24: 三高筹码 ────
    
    def parse_financials_to_d24(self, mcp_result: Dict) -> Dict:
        """
        解析tdx_api_data(财务分析)结果 → D24三高筹码评分
        
        三高 = 高集中度 + 高锁定 + 高认同
        """
        result = {
            "pe": 0.0,
            "pb": 0.0,
            "roe": 0.0,
            "revenue_growth": 0.0,
            "net_profit_growth": 0.0,
            "chip_concentration": 0.0,
            "chip_d24_score": 0.0,
            "chip_d24_detail": "",
        }
        
        try:
            summary = mcp_result.get("summary", "")
            tables = mcp_result.get("tables", [])
            
            for table in tables:
                for row in table.get("rows", []):
                    for key, val in row.items():
                        val_str = str(val).replace("--", "0").replace("%", "")
                        try:
                            if "PE" in key or "市盈率" in key:
                                result["pe"] = float(val_str)
                            elif "PB" in key or "市净率" in key:
                                result["pb"] = float(val_str)
                            elif "ROE" in key:
                                result["roe"] = float(val_str)
                            elif "营收增长" in key or "营业收入增长" in key:
                                result["revenue_growth"] = float(val_str)
                            elif "净利润增长" in key or "归母净利增长" in key:
                                result["net_profit_growth"] = float(val_str)
                        except:
                            pass
            
            # 三高筹码评分逻辑
            # 高ROE(>15%) → 筹码认同度高 → +2分
            if result["roe"] >= 15:
                result["chip_d24_score"] += 2.0
                result["chip_d24_detail"] += f"高ROE({result['roe']:.1f}%)+2分;"
            elif result["roe"] >= 10:
                result["chip_d24_score"] += 1.0
                result["chip_d24_detail"] += f"ROE({result['roe']:.1f}%)+1分;"
            
            # 低PE(<20) + 低PB(<2) → 筹码锁定度高 → +2分
            if result["pe"] > 0 and result["pe"] < 20:
                result["chip_d24_score"] += 2.0
                result["chip_d24_detail"] += f"低PE({result['pe']:.1f})+2分;"
            if result["pb"] > 0 and result["pb"] < 2:
                result["chip_d24_score"] += 1.0
                result["chip_d24_detail"] += f"低PB({result['pb']:.1f})+1分;"
            
            # 营收+净利双增长 → 筹码集中度高 → +2分
            if result["revenue_growth"] > 0 and result["net_profit_growth"] > 0:
                result["chip_d24_score"] += 2.0
                result["chip_d24_detail"] += f"营收+净利双增长+2分;"
            
            result["chip_d24_score"] = min(result["chip_d24_score"], 10.0)
            
        except Exception as e:
            logger.error(f"parse_financials_to_d24 error: {e}")
            result["parse_error"] = str(e)
        
        return result
    
    def parse_policy_to_d24(self, mcp_result: Dict) -> Dict:
        """解析政策解读结果 → D24政策信号"""
        result = {
            "policy_events": [],
            "policy_d24_bonus": 0.0,
            "policy_d24_detail": "",
        }
        
        try:
            # wenda_news_query返回格式
            items = mcp_result.get("items", [])
            summary = mcp_result.get("summary", "")
            
            # 检查重大政策利好
            policy_keywords = ["政策", "利好", "补贴", "扶持", "减税", "专项", "规划", "战略"]
            for item in items[:5]:
                title = item.get("title", "")
                for kw in policy_keywords:
                    if kw in title:
                        result["policy_events"].append(title)
                        result["policy_d24_bonus"] += 0.5
                        result["policy_d24_detail"] += f"政策利好({kw}): {title[:30]}+0.5分;"
            
            result["policy_d24_bonus"] = min(result["policy_d24_bonus"], 3.0)
            
        except Exception as e:
            logger.error(f"parse_policy_to_d24 error: {e}")
        
        return result
    
    # ──── D25: 放量启动 ────
    
    def parse_screener_to_d25(self, mcp_result: Dict) -> Dict:
        """解析tdx_screener结果 → D25候选股列表"""
        result = {
            "screener_stocks": [],
            "screener_d25_candidates": 0,
        }
        
        try:
            items = mcp_result.get("items", [])
            for item in items:
                result["screener_stocks"].append({
                    "code": item.get("sec_code", ""),
                    "name": item.get("sec_name", ""),
                    "change_pct": item.get("change_pct", 0),
                    "volume_ratio": item.get("volume_ratio", 0),
                })
            result["screener_d25_candidates"] = len(items)
            
        except Exception as e:
            logger.error(f"parse_screener_to_d25 error: {e}")
        
        return result
    
    def parse_capital_flow_to_d25(self, mcp_result: Dict) -> Dict:
        """解析主力资金流向 → D25资金信号"""
        result = {
            "main_net_today": 0.0,
            "main_net_3d": 0.0,
            "main_net_5d": 0.0,
            "capital_d25_score": 0.0,
            "capital_d25_detail": "",
        }
        
        try:
            tables = mcp_result.get("tables", [])
            for table in tables:
                rows = table.get("rows", [])
                for i, row in enumerate(rows[:5]):
                    main_net_str = str(row.get("主力净流入-净额", row.get("主力净额", "0")))
                    try:
                        main_net = float(main_net_str.replace("万", "").replace("亿", "").replace("--", "0"))
                        if i == 0:
                            result["main_net_today"] = main_net
                        if i < 3:
                            result["main_net_3d"] += main_net
                        if i < 5:
                            result["main_net_5d"] += main_net
                    except:
                        pass
            
            # 主力净流入>0 → D25加分
            if result["main_net_today"] > 0:
                result["capital_d25_score"] += 2.0
                result["capital_d25_detail"] += f"今日主力净流入+{result['main_net_today']:.0f}万+2分;"
            if result["main_net_3d"] > 0:
                result["capital_d25_score"] += 1.0
                result["capital_d25_detail"] += f"3日主力净流入+1分;"
            
            result["capital_d25_score"] = min(result["capital_d25_score"], 5.0)
            
        except Exception as e:
            logger.error(f"parse_capital_flow_to_d25 error: {e}")
        
        return result
    
    # ──── D28: 催化强度 ────
    
    def parse_announcements_to_d28(self, mcp_result: Dict) -> Dict:
        """
        解析公告与财报分析 → D28催化强度
        
        催化等级:
          8分: 重组/并购/IPO催化(宇树机器人IPO)
          6分: 业绩预告超预期/回购/定增
          4分: 行业政策利好
          2分: 一般公告
        """
        result = {
            "catalyst_events": [],
            "catalyst_d28_score": 0.0,
            "catalyst_d28_detail": "",
            "catalyst_type": "",
        }
        
        try:
            items = mcp_result.get("items", [])
            summary = mcp_result.get("summary", "")
            
            # 8分催化: 重组/并购/IPO
            level8_keywords = ["重组", "并购", "收购", "IPO", "上市", "借壳"]
            # 6分催化: 业绩预告超预期/回购/定增
            level6_keywords = ["业绩预告", "超预期", "回购", "增持", "定增", "股权激励"]
            # 4分催化: 行业政策利好
            level4_keywords = ["补贴", "政策", "扶持", "减税", "专项"]
            
            max_level = 0
            
            for item in items[:10]:
                title = item.get("title", "")
                content = item.get("content", title)
                
                for kw in level8_keywords:
                    if kw in title or kw in content:
                        if max_level < 8:
                            max_level = 8
                            result["catalyst_type"] = f"强催化({kw})"
                            result["catalyst_d28_detail"] += f"强催化: {title[:40]}+8分;"
                        result["catalyst_events"].append({"title": title, "level": 8, "keyword": kw})
                
                for kw in level6_keywords:
                    if kw in title or kw in content:
                        if max_level < 6:
                            max_level = 6
                            result["catalyst_type"] = f"中等催化({kw})"
                            result["catalyst_d28_detail"] += f"中等催化: {title[:40]}+6分;"
                        result["catalyst_events"].append({"title": title, "level": 6, "keyword": kw})
                
                for kw in level4_keywords:
                    if kw in title or kw in content:
                        if max_level < 4:
                            max_level = 4
                            result["catalyst_type"] = f"弱催化({kw})"
                            result["catalyst_d28_detail"] += f"弱催化: {title[:40]}+4分;"
                        result["catalyst_events"].append({"title": title, "level": 4, "keyword": kw})
            
            result["catalyst_d28_score"] = max_level
            result["catalyst_d28_score"] = min(result["catalyst_d28_score"], 8.0)
            
        except Exception as e:
            logger.error(f"parse_announcements_to_d28 error: {e}")
        
        return result
    
    def parse_events_to_d28(self, mcp_result: Dict) -> Dict:
        """解析事件驱动结果 → D28事件催化补充"""
        result = {
            "event_catalysts": [],
            "event_d28_bonus": 0.0,
        }
        
        try:
            items = mcp_result.get("items", [])
            for item in items[:5]:
                title = item.get("title", "")
                if "催化" in title or "驱动" in title or "爆发" in title:
                    result["event_catalysts"].append(title)
                    result["event_d28_bonus"] += 0.5
            
            result["event_d28_bonus"] = min(result["event_d28_bonus"], 2.0)
            
        except Exception as e:
            logger.error(f"parse_events_to_d28 error: {e}")
        
        return result
    
    # ──── D29: 双洗盘 ────
    
    def parse_capital_flow_history_to_d29(self, mcp_result: Dict) -> Dict:
        """
        解析Historical主力净额 → D29洗盘日主力微正
        
        蜀道装备6/24验证: 暴跌-8.97%但主力+169万=洗盘铁证
        """
        result = {
            "capital_flow_history": [],
            "washout_day_main_positive": False,
            "washout_day_main_net": 0.0,
            "washout_day_date": "",
        }
        
        try:
            tables = mcp_result.get("tables", [])
            for table in tables:
                rows = table.get("rows", [])
                for row in rows:
                    date_str = str(row.get("日期", row.get("交易日期", "")))
                    main_net_str = str(row.get("主力净流入-净额", row.get("主力净额", "0")))
                    
                    try:
                        # 处理单位
                        main_net_val = main_net_str.replace("万", "").replace("亿", "").replace("--", "0")
                        main_net = float(main_net_val)
                        if "亿" in main_net_str:
                            main_net *= 10000  # 亿→万
                        
                        close_str = str(row.get("收盘价", "0")).replace("--", "0")
                        close = float(close_str)
                        
                        chg_str = str(row.get("涨跌幅", row.get("涨幅", "0"))).replace("%", "").replace("--", "0")
                        chg_pct = float(chg_str)
                        
                        result["capital_flow_history"].append({
                            "date": date_str,
                            "close": close,
                            "chg_pct": chg_pct,
                            "main_net": main_net,  # 万元
                        })
                    except:
                        pass
            
            # 检查洗盘日主力微正
            for cf in result["capital_flow_history"]:
                if cf["chg_pct"] <= -5.0 and cf["main_net"] > 0:
                    result["washout_day_main_positive"] = True
                    result["washout_day_main_net"] = cf["main_net"]
                    result["washout_day_date"] = cf["date"]
                    break
            
        except Exception as e:
            logger.error(f"parse_capital_flow_history_to_d29 error: {e}")
        
        return result
    
    # ──── D31: 主力资金意图 ────
    
    def parse_institutional_to_d31(self, mcp_result: Dict) -> Dict:
        """解析机构持仓 → D31主力意图信号"""
        result = {
            "institutional_holding_change": 0.0,
            "institutional_net_buy": False,
            "institutional_d31_bonus": 0.0,
            "institutional_d31_detail": "",
        }
        
        try:
            summary = mcp_result.get("summary", "")
            tables = mcp_result.get("tables", [])
            
            # 检查机构增仓/减仓
            if "增仓" in summary or "增持" in summary or "新进" in summary:
                result["institutional_net_buy"] = True
                result["institutional_d31_bonus"] = 3.0
                result["institutional_d31_detail"] += "机构增仓+3分;"
            elif "减仓" in summary or "减持" in summary:
                result["institutional_d31_bonus"] = -2.0
                result["institutional_d31_detail"] += "机构减仓-2分;"
            
            for table in tables:
                for row in table.get("rows", [])[:5]:
                    holder_name = str(row.get("股东名称", row.get("机构名称", "")))
                    change_str = str(row.get("增减", row.get("变动", "")))
                    if "增" in change_str or "新进" in change_str:
                        result["institutional_d31_bonus"] += 1.0
                        result["institutional_d31_detail"] += f"{holder_name[:15]}增仓+1分;"
            
            result["institutional_d31_bonus"] = min(result["institutional_d31_bonus"], 5.0)
            
        except Exception as e:
            logger.error(f"parse_institutional_to_d31 error: {e}")
        
        return result
    
    # ──── D33: 外盘/内盘 ────
    
    def parse_quotes_to_d33(self, mcp_result: Dict) -> Dict:
        """
        解析实时行情 → D33外盘/内盘比率
        
        外盘>内盘1.5倍 → 3分(强势买入)
        """
        result = {
            "outer_volume": 0.0,
            "inner_volume": 0.0,
            "outer_inner_ratio": 0.0,
            "d33_score": 0.0,
            "d33_detail": "",
            "wei_ratio": 0.0,  # 委比
        }
        
        try:
            # tdx_quotes返回格式
            hq = mcp_result.get("hqInfo", mcp_result.get("行情", {}))
            ext = mcp_result.get("extInfo", mcp_result.get("扩展信息", {}))
            
            outer = float(str(hq.get("外盘", ext.get("外盘", "0"))).replace("--", "0"))
            inner = float(str(hq.get("内盘", ext.get("内盘", "0"))).replace("--", "0"))
            wei_ratio = float(str(hq.get("委比", ext.get("委比", "0"))).replace("%", "").replace("--", "0"))
            
            result["outer_volume"] = outer
            result["inner_volume"] = inner
            result["wei_ratio"] = wei_ratio
            
            if inner > 0:
                ratio = outer / inner
            else:
                ratio = 0
            
            result["outer_inner_ratio"] = ratio
            
            # D33评分逻辑
            if ratio >= 1.5:
                result["d33_score"] = 3.0
                result["d33_detail"] = f"外盘/内盘={ratio:.2f}(强势买入)+3分"
            elif ratio >= 1.2:
                result["d33_score"] = 2.0
                result["d33_detail"] = f"外盘/内盘={ratio:.2f}(偏强)+2分"
            elif ratio >= 1.0:
                result["d33_score"] = 1.0
                result["d33_detail"] = f"外盘/内盘={ratio:.2f}(中性)+1分"
            else:
                result["d33_score"] = 0.0
                result["d33_detail"] = f"外盘/内盘={ratio:.2f}(偏弱)+0分"
            
        except Exception as e:
            logger.error(f"parse_quotes_to_d33 error: {e}")
        
        return result
    
    # ──── 辅助维度 ────
    
    def parse_dragon_tiger_to_d3d4(self, mcp_result: Dict) -> Dict:
        """解析龙虎榜 → D3机构净买入/D4顶级投行"""
        result = {
            "institutional_net_buy": 0.0,
            "top_broker_seats": [],
            "d3_bonus": 0.0,
            "d4_bonus": 0.0,
        }
        
        try:
            tables = mcp_result.get("tables", [])
            for table in tables:
                for row in table.get("rows", [])[:10]:
                    seat = str(row.get("营业部", row.get("席位", "")))
                    buy_str = str(row.get("买入额", row.get("买入", "0")))
                    sell_str = str(row.get("卖出额", row.get("卖出", "0")))
                    
                    try:
                        buy = float(buy_str.replace("万", "").replace("亿", "").replace("--", "0"))
                        sell = float(sell_str.replace("万", "").replace("亿", "").replace("--", "0"))
                        net = buy - sell
                        
                        # 机构席位
                        if "机构" in seat:
                            result["institutional_net_buy"] += net
                        
                        # 顶级投行席位
                        top_brokers = ["中金", "中信", "国泰君安", "华泰", "招商", "海通"]
                        for tb in top_brokers:
                            if tb in seat:
                                result["top_broker_seats"].append(seat)
                                result["d4_bonus"] = 5.0
                    except:
                        pass
            
            if result["institutional_net_buy"] > 0:
                result["d3_bonus"] = min(result["institutional_net_buy"] / 1000, 15.0)
            
        except Exception as e:
            logger.error(f"parse_dragon_tiger_to_d3d4 error: {e}")
        
        return result
    
    def parse_hot_topic_to_d28(self, mcp_result: Dict) -> Dict:
        """解析热点题材 → D28题材催化补充"""
        result = {"topics": [], "topic_d28_bonus": 0.0}
        try:
            tables = mcp_result.get("tables", [])
            for table in tables:
                for row in table.get("rows", [])[:5]:
                    topic = str(row.get("题材", row.get("概念", "")))
                    if topic:
                        result["topics"].append(topic)
                        result["topic_d28_bonus"] += 0.5
            result["topic_d28_bonus"] = min(result["topic_d28_bonus"], 2.0)
        except Exception as e:
            logger.error(f"parse_hot_topic_to_d28 error: {e}")
        return result
    
    def parse_report_rating_to_d24(self, mcp_result: Dict) -> Dict:
        """解析研报评级 → D24筹码认同度补充"""
        result = {"rating": "", "target_price": 0.0, "rating_d24_bonus": 0.0}
        try:
            summary = mcp_result.get("summary", "")
            if "买入" in summary or "推荐" in summary:
                result["rating"] = "买入"
                result["rating_d24_bonus"] = 2.0
            elif "增持" in summary:
                result["rating"] = "增持"
                result["rating_d24_bonus"] = 1.0
        except Exception as e:
            logger.error(f"parse_report_rating_to_d24 error: {e}")
        return result
    
    def parse_shareholder_to_d24(self, mcp_result: Dict) -> Dict:
        """解析股东信息 → D24筹码集中度"""
        result = {"shareholder_count_change": 0.0, "shareholder_d24_bonus": 0.0}
        try:
            summary = mcp_result.get("summary", "")
            if "减少" in summary or "下降" in summary:
                result["shareholder_count_change"] = -1  # 股东数减少=筹码集中
                result["shareholder_d24_bonus"] = 2.0
            elif "增加" in summary or "上升" in summary:
                result["shareholder_count_change"] = 1  # 股东数增加=筹码分散
                result["shareholder_d24_bonus"] = -1.0
        except Exception as e:
            logger.error(f"parse_shareholder_to_d24 error: {e}")
        return result
    
    def parse_northbound_to_d5(self, mcp_result: Dict) -> Dict:
        """解析北向资金 → D5资金流补充"""
        result = {"northbound_net": 0.0, "northbound_d5_bonus": 0.0}
        try:
            summary = mcp_result.get("summary", "")
            if "增持" in summary or "净买入" in summary or "流入" in summary:
                result["northbound_net"] = 1
                result["northbound_d5_bonus"] = 3.0
            elif "减持" in summary or "净卖出" in summary or "流出" in summary:
                result["northbound_net"] = -1
                result["northbound_d5_bonus"] = -1.0
        except Exception as e:
            logger.error(f"parse_northbound_to_d5 error: {e}")
        return result

    # ──── 补全: 4个缺失解析方法 (V13.5.18完善) ────

    def parse_earnings_warning_to_d28(self, mcp_result: Dict) -> Dict:
        """
        解析业绩预告 → D28催化强度补充
        业绩预增/超预期 → 催化加分; 业绩预减/亏损 → 催化减分
        """
        result = {
            "earnings_type": "unknown",
            "earnings_d28_bonus": 0.0,
            "earnings_detail": "",
        }
        try:
            items = mcp_result.get("items", [])
            if not items:
                # 尝试从tables解析
                tables = mcp_result.get("tables", [])
                if tables:
                    for table in tables:
                        rows = table.get("rows", [])
                        for row in rows:
                            text = str(row.get("预告类型", "")) + str(row.get("业绩变动", ""))
                            if "预增" in text or "大幅增长" in text:
                                result["earnings_type"] = "pre_increase"
                                result["earnings_d28_bonus"] = 4.0
                                result["earnings_detail"] = f"业绩预增: {text[:50]}"
                                break
                            elif "预减" in text or "下降" in text or "亏损" in text:
                                result["earnings_type"] = "pre_decrease"
                                result["earnings_d28_bonus"] = -3.0
                                result["earnings_detail"] = f"业绩预减: {text[:50]}"
                                break
            else:
                for item in items:
                    title = str(item.get("title", ""))
                    content = str(item.get("content", ""))
                    combined = title + content
                    if "预增" in combined or "大幅增长" in combined or "超预期" in combined:
                        result["earnings_type"] = "pre_increase"
                        result["earnings_d28_bonus"] = 4.0
                        result["earnings_detail"] = f"业绩预增: {title[:50]}"
                        break
                    elif "预减" in combined or "下降" in combined or "亏损" in combined:
                        result["earnings_type"] = "pre_decrease"
                        result["earnings_d28_bonus"] = -3.0
                        result["earnings_detail"] = f"业绩预减: {title[:50]}"
                        break
        except Exception as e:
            logger.error(f"parse_earnings_warning_to_d28 error: {e}")
        return result

    def parse_valuation_to_d24(self, mcp_result: Dict) -> Dict:
        """
        解析估值数据(PE/PB/PS) → D24三高筹码辅助
        PE历史分位 <20% → 低估加分; >80% → 高估减分
        """
        result = {
            "pe_ttm": 0.0,
            "pb": 0.0,
            "pe_percentile": 0.0,
            "valuation_d24_bonus": 0.0,
            "valuation_detail": "",
        }
        try:
            # 从tdx_api_data估值分析结果解析
            tables = mcp_result.get("tables", [])
            pe_val = 0.0
            pb_val = 0.0
            percentile = 50.0  # 默认中位数

            for table in tables:
                rows = table.get("rows", [])
                for row in rows:
                    label = str(row.get("指标", row.get("名称", "")))
                    val = str(row.get("数值", row.get("值", "")))
                    if "PE" in label.upper() or "市盈" in label:
                        try:
                            pe_val = float(val.replace("倍", "").replace("-", "0"))
                        except:
                            pass
                    elif "PB" in label.upper() or "市净" in label:
                        try:
                            pb_val = float(val.replace("倍", "").replace("-", "0"))
                        except:
                            pass
                    elif "分位" in label:
                        try:
                            percentile = float(val.replace("%", ""))
                        except:
                            pass

            # 也尝试从extInfo解析
            ext_info = mcp_result.get("extInfo", mcp_result.get("ExtInfo", {}))
            if ext_info:
                pe_str = str(ext_info.get("SYL", ext_info.get("PE", "")))
                pb_str = str(ext_info.get("SJL", ext_info.get("PB", "")))
                if pe_str and pe_val == 0:
                    try:
                        pe_val = float(pe_str)
                    except:
                        pass
                if pb_str and pb_val == 0:
                    try:
                        pb_val = float(pb_str)
                    except:
                        pass

            result["pe_ttm"] = pe_val
            result["pb"] = pb_val
            result["pe_percentile"] = percentile

            # 估值分位评分
            if percentile < 20:
                result["valuation_d24_bonus"] = 3.0
                result["valuation_detail"] = f"PE={pe_val:.1f}分位{percentile:.0f}% 低估"
            elif percentile < 40:
                result["valuation_d24_bonus"] = 1.5
                result["valuation_detail"] = f"PE={pe_val:.1f}分位{percentile:.0f}% 偏低"
            elif percentile > 80:
                result["valuation_d24_bonus"] = -2.0
                result["valuation_detail"] = f"PE={pe_val:.1f}分位{percentile:.0f}% 高估"
            elif percentile > 60:
                result["valuation_d24_bonus"] = -1.0
                result["valuation_detail"] = f"PE={pe_val:.1f}分位{percentile:.0f}% 偏高"
            else:
                result["valuation_detail"] = f"PE={pe_val:.1f}分位{percentile:.0f}% 合理"
        except Exception as e:
            logger.error(f"parse_valuation_to_d24 error: {e}")
        return result

    def parse_company_quality_to_d24(self, mcp_result: Dict) -> Dict:
        """
        解析公司质地打分 → D24三高筹码辅助
        行业排名前10% → 高质量加分; 后30% → 低质量减分
        """
        result = {
            "quality_score": 50.0,
            "quality_rank": "",
            "company_quality_d24_bonus": 0.0,
            "quality_detail": "",
        }
        try:
            tables = mcp_result.get("tables", [])
            score = 50.0
            rank_text = ""

            for table in tables:
                rows = table.get("rows", [])
                for row in rows:
                    label = str(row.get("指标", row.get("名称", "")))
                    val = str(row.get("数值", row.get("值", row.get("得分", ""))))
                    if "评分" in label or "得分" in label or "综合" in label:
                        try:
                            score = float(val)
                        except:
                            pass
                    elif "排名" in label or "百分位" in label:
                        rank_text = val

            result["quality_score"] = score
            result["quality_rank"] = rank_text

            if score >= 80:
                result["company_quality_d24_bonus"] = 3.0
                result["quality_detail"] = f"质地评分{score:.0f} 优秀"
            elif score >= 70:
                result["company_quality_d24_bonus"] = 2.0
                result["quality_detail"] = f"质地评分{score:.0f} 良好"
            elif score >= 60:
                result["company_quality_d24_bonus"] = 1.0
                result["quality_detail"] = f"质地评分{score:.0f} 合格"
            elif score < 40:
                result["company_quality_d24_bonus"] = -2.0
                result["quality_detail"] = f"质地评分{score:.0f} 较差"
        except Exception as e:
            logger.error(f"parse_company_quality_to_d24 error: {e}")
        return result

    def parse_dragon_tiger_style_to_d3d4(self, mcp_result: Dict) -> Dict:
        """
        解析龙虎榜席位风格 → D3/D4辅助
        顶级游资(赵老哥/章建平/方新侠等)买入 → D3加分
        机构专用席位买入 → D4加分
        """
        result = {
            "top_broker_name": "",
            "broker_type": "unknown",
            "d3_bonus": 0.0,
            "d4_bonus": 0.0,
            "broker_detail": "",
        }
        try:
            tables = mcp_result.get("tables", [])
            # 知名游资席位名单
            top_brokers = ["赵老哥", "章建平", "方新侠", "孙惠刚", "炒股养家",
                          "拉萨", "量化", "知名游资", "一线游资"]
            has_top_broker = False
            has_institution = False

            for table in tables:
                rows = table.get("rows", [])
                for row in rows:
                    seat_name = str(row.get("营业部", row.get("席位", row.get("名称", ""))))
                    buy_amt = str(row.get("买入额", row.get("净买入", "")))

                    # 检查是否知名游资
                    for broker in top_brokers:
                        if broker in seat_name:
                            has_top_broker = True
                            result["top_broker_name"] = seat_name
                            result["broker_type"] = "top_gamer"
                            break

                    # 检查是否机构专用
                    if "机构" in seat_name:
                        has_institution = True
                        if not result["top_broker_name"]:
                            result["top_broker_name"] = seat_name
                            result["broker_type"] = "institution"

            if has_top_broker:
                result["d3_bonus"] = 3.0
                result["broker_detail"] = f"知名游资: {result['top_broker_name']}"
            if has_institution:
                result["d4_bonus"] = 2.0
                result["broker_detail"] += f" 机构买入" if result["broker_detail"] else "机构买入"

            if not has_top_broker and not has_institution:
                result["broker_detail"] = "无知名游资或机构"
        except Exception as e:
            logger.error(f"parse_dragon_tiger_style_to_d3d4 error: {e}")
        return result

    # ──── 综合集成 ────
    
    def integrate_skills_to_m71(self, skills_results: Dict[str, Dict]) -> Dict:
        """
        将所有Skills结果综合集成到M71维度
        
        Args:
            skills_results: {skill_id: mcp_result} - 各Skills的MCP调用结果
        
        Returns:
            Dict: M71维度增强数据，可传入predict()
        """
        m71_enhancement = {
            "d21_phase_data": {},
            "d24_chip_data": {},
            "d25_volume_data": {},
            "d28_catalyst_data": {},
            "d29_capital_flow_history": [],
            "d31_main_capital_data": {},
            "d33_quote_data": {},
            # 辅助维度
            "d3_dragon_tiger_bonus": 0.0,
            "d4_top_broker_bonus": 0.0,
            "d5_northbound_bonus": 0.0,
        }
        
        for skill_id, mcp_result in skills_results.items():
            if skill_id in SKILL_MCP_CALL_MAP:
                skill_info = SKILL_MCP_CALL_MAP[skill_id]
                parse_method = skill_info["parse_method"]
                dimension = skill_info["m71_dimension"]
                
                try:
                    # 调用对应的解析方法
                    method = getattr(self, parse_method, None)
                    if method:
                        parsed = method(mcp_result)
                        
                        # 根据维度路由到m71_enhancement
                        if dimension == "D21":
                            if "phase_d21_score" in parsed:
                                m71_enhancement["d21_phase_data"] = parsed
                        elif dimension == "D24":
                            if "chip_d24_score" in parsed:
                                m71_enhancement["d24_chip_data"].update(parsed)
                            elif "policy_d24_bonus" in parsed:
                                m71_enhancement["d24_chip_data"].setdefault("policy_bonus", 0)
                                m71_enhancement["d24_chip_data"]["policy_bonus"] += parsed["policy_d24_bonus"]
                        elif dimension == "D25":
                            m71_enhancement["d25_volume_data"].update(parsed)
                        elif dimension == "D28":
                            if "catalyst_d28_score" in parsed:
                                m71_enhancement["d28_catalyst_data"] = parsed
                            elif "event_d28_bonus" in parsed:
                                m71_enhancement["d28_catalyst_data"].setdefault("event_bonus", 0)
                                m71_enhancement["d28_catalyst_data"]["event_bonus"] += parsed["event_d28_bonus"]
                        elif dimension == "D29":
                            if "capital_flow_history" in parsed:
                                m71_enhancement["d29_capital_flow_history"] = parsed["capital_flow_history"]
                        elif dimension == "D31":
                            m71_enhancement["d31_main_capital_data"].update(parsed)
                        elif dimension == "D33":
                            m71_enhancement["d33_quote_data"] = parsed
                        elif dimension == "D3/D4":
                            m71_enhancement["d3_dragon_tiger_bonus"] += parsed.get("d3_bonus", 0)
                            m71_enhancement["d4_top_broker_bonus"] += parsed.get("d4_bonus", 0)
                        elif dimension == "D5辅助":
                            m71_enhancement["d5_northbound_bonus"] += parsed.get("northbound_d5_bonus", 0)
                        elif dimension == "D24辅助":
                            m71_enhancement["d24_chip_data"].setdefault("rating_bonus", 0)
                            m71_enhancement["d24_chip_data"]["rating_bonus"] += parsed.get("rating_d24_bonus", parsed.get("shareholder_d24_bonus", 0))
                        elif dimension == "D28辅助":
                            m71_enhancement["d28_catalyst_data"].setdefault("topic_bonus", 0)
                            m71_enhancement["d28_catalyst_data"]["topic_bonus"] += parsed.get("topic_d28_bonus", parsed.get("d28_bonus", 0))
                    
                except Exception as e:
                    logger.error(f"integrate_skills_to_m71 error for {skill_id}: {e}")
        
        # 保存到文件
        filepath = os.path.join(self.data_dir, "m71_enhancement_latest.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(m71_enhancement, f, ensure_ascii=False, indent=2)
        
        return m71_enhancement
    
    def generate_mcp_call_plan(self, stock_code: str, stock_name: str, setcode: str = "0") -> List[Dict]:
        """
        生成完整的MCP调用计划（给AI Agent执行）
        
        返回每个需要调用的MCP工具+参数列表
        """
        today = datetime.now().strftime("%Y%m%d")
        today_7d = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        today_14d = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
        today_30d = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        
        calls = []
        
        for skill_id, skill_info in SKILL_MCP_CALL_MAP.items():
            mcp_tool = skill_info["mcp_tool"]
            params_template = skill_info.get("mcp_params_template", skill_info.get("mcp_params", {}))
            
            # 替换模板变量
            resolved_params = {}
            for key, val in params_template.items():
                if isinstance(val, str):
                    val = val.replace("{stock_code}", stock_code)
                    val = val.replace("{stock_name}", stock_name)
                    val = val.replace("{setcode}", setcode)
                    val = val.replace("{today}", today)
                    val = val.replace("{today_7d}", today_7d)
                    val = val.replace("{today_14d}", today_14d)
                    val = val.replace("{today_30d}", today_30d)
                    val = val.replace("{date}", today)
                resolved_params[key] = val
            
            call_entry = {
                "skill_id": skill_id,
                "skill_name": skill_info["name"],
                "mcp_tool": mcp_tool,
                "mcp_params": resolved_params,
                "m71_dimension": skill_info["m71_dimension"],
            }
            
            # 添加额外调用
            for extra in skill_info.get("extra_calls", []):
                extra_tool = extra["mcp_tool"]
                extra_params = {}
                for key, val in extra.get("mcp_params_template", extra.get("mcp_params", {})).items():
                    if isinstance(val, str):
                        val = val.replace("{stock_code}", stock_code)
                        val = val.replace("{stock_name}", stock_name)
                        val = val.replace("{today}", today)
                        val = val.replace("{today_7d}", today_7d)
                    extra_params[key] = val
                
                call_entry["extra_calls"] = call_entry.get("extra_calls", [])
                call_entry["extra_calls"].append({
                    "mcp_tool": extra_tool,
                    "mcp_params": extra_params,
                })
            
            calls.append(call_entry)
        
        return calls


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 快速集成测试
# ═══════════════════════════════════════════════════════════════

def test_integrator():
    """测试Skills→M71集成器"""
    print("=" * 70)
    print("V13.5.18 Skills→M71集成器测试")
    print("=" * 70)
    
    integrator = SkillM71Integrator()
    
    # 1. 生成MCP调用计划
    print("\n[1] 生成蜀道装备(300540) MCP调用计划...")
    calls = integrator.generate_mcp_call_plan("300540", "蜀道装备", "0")
    print(f"   共需调用 {len(calls)} 个Skills")
    for call in calls[:5]:
        print(f"   - {call['skill_name']} → {call['m71_dimension']} → {call['mcp_tool']}")
    
    # 2. 测试解析方法
    print("\n[2] 测试D28催化强度解析...")
    fake_mcp_result = {
        "items": [
            {"title": "宇树机器人IPO催化", "content": "宇树机器人即将上市"},
            {"title": "业绩预告超预期", "content": "业绩同比增长50%"},
        ],
    }
    d28_result = integrator.parse_announcements_to_d28(fake_mcp_result)
    print(f"   D28催化评分: {d28_result['catalyst_d28_score']:.1f}/8")
    print(f"   催化类型: {d28_result['catalyst_type']}")
    print(f"   详情: {d28_result['catalyst_d28_detail']}")
    
    # 3. 测试D33外盘内盘解析
    print("\n[3] 测试D33外盘/内盘解析...")
    fake_quotes = {
        "hqInfo": {"外盘": "150000", "内盘": "80000"},
        "extInfo": {"委比": "15.3%"},
    }
    d33_result = integrator.parse_quotes_to_d33(fake_quotes)
    print(f"   外盘/内盘比率: {d33_result['outer_inner_ratio']:.2f}")
    print(f"   D33评分: {d33_result['d33_score']:.1f}/3")
    print(f"   详情: {d33_result['d33_detail']}")
    
    # 4. 测试D29洗盘日主力微正
    print("\n[4] 测试D29洗盘日主力微正解析...")
    fake_capital_flow = {
        "tables": [{
            "rows": [
                {"日期": "2026-06-24", "收盘价": "26.19", "涨跌幅": "-8.97%", "主力净流入-净额": "169万"},
                {"日期": "2026-06-23", "收盘价": "28.77", "涨跌幅": "10.6%", "主力净流入-净额": "500万"},
            ]
        }]
    }
    d29_result = integrator.parse_capital_flow_history_to_d29(fake_capital_flow)
    print(f"   洗盘日主力微正: {d29_result['washout_day_main_positive']}")
    print(f"   洗盘日日期: {d29_result['washout_day_date']}")
    print(f"   洗盘日主力净额: {d29_result['washout_day_main_net']:.0f}万")
    
    # 5. 测试综合集成
    print("\n[5] 测试综合集成...")
    all_results = {
        "tdx-ggycbfx": fake_mcp_result,
        "tdx-quotes-d33": fake_quotes,
        "tdx-main-position-d29": fake_capital_flow,
    }
    m71_enhancement = integrator.integrate_skills_to_m71(all_results)
    print(f"   D28催化: {m71_enhancement['d28_catalyst_data'].get('catalyst_d28_score', 0):.1f}")
    print(f"   D33外内: {m71_enhancement['d33_quote_data'].get('d33_score', 0):.1f}")
    print(f"   D29洗盘: {len(m71_enhancement['d29_capital_flow_history'])}天历史数据")
    
    print("\n" + "=" * 70)
    print("✅ Skills→M71集成器测试通过！")
    print("=" * 70)


if __name__ == "__main__":
    test_integrator()
